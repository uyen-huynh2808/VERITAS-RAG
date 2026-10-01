from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from statistics import mean
from typing import List

from src.baselines.bm25_baseline import BM25Retriever
from src.baselines.naive_rag_baseline import NaiveDenseRetriever
from src.rag.generator_agent import GeneratorAgent

from .run_eval import (
    BenchmarkSample,
    EvaluationResult,
    RetrievedChunk,
    SystemOutput,
    evaluate_system,
    load_dataset,
)

CACHE_VERSION = 2


# ============================================================
# Benchmark Orchestrator
# ============================================================

class BenchmarkRunner:
    """
    End-to-end benchmark pipeline.

    Gold Chunks
         │
         ▼
    BM25 / Naive Retriever
         │
         ▼
    Shared Generator
         │
         ▼
    Evaluation Metrics
    """

    def __init__(
        self,
        gold_path: str,
        model_name: str,
        top_k: int = 5,
        resume: bool = False,
    ):
        self.model_name = model_name.lower()
        self.top_k = top_k
        self.resume = resume

        if self.model_name == "bm25":
            self.retriever = BM25Retriever(gold_path)
        elif self.model_name == "naive":
            self.retriever = NaiveDenseRetriever(gold_path)
        else:
            raise ValueError("model_name must be 'bm25' or 'naive'")

        self.generator = GeneratorAgent()

        self.cache_dir = Path("results/cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------
    # Cache utilities
    # --------------------------------------------------------

    def _cache_path(self, sample_id: str) -> Path:
        return self.cache_dir / f"{self.model_name}_{sample_id}.json"

    def _save_cache(
        self,
        sample_id: str,
        output: SystemOutput,
    ) -> None:

        cache = {
            "version": CACHE_VERSION,
            "question": output.question,
            "answer": output.answer,
            "latency_ms": output.latency_ms,
            "retrieved_chunks": [
                asdict(chunk)
                for chunk in output.retrieved_chunks
            ],
        }

        with open(
            self._cache_path(sample_id),
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

    def _load_cache(
        self,
        sample_id: str,
    ) -> SystemOutput | None:

        cache_file = self._cache_path(sample_id)

        if not cache_file.exists():
            return None

        with open(cache_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("version") != CACHE_VERSION:
            return None

        chunks = [
            RetrievedChunk(**chunk)
            for chunk in data["retrieved_chunks"]
        ]

        return SystemOutput(
            question=data["question"],
            answer=data["answer"],
            retrieved_chunks=chunks,
            latency_ms=data["latency_ms"],
        )

    # --------------------------------------------------------
    # Single benchmark sample
    # --------------------------------------------------------

    def run_sample(
        self,
        sample: BenchmarkSample,
    ) -> EvaluationResult:

        if self.resume:

            cached = self._load_cache(sample.id)

            if cached is not None:
                print(f"[CACHE] {sample.id}")

                return evaluate_system(
                    sample=sample,
                    output=cached,
                    model_name=self.model_name,
                )

        retrieved_chunks = self.retriever.retrieve(
            query=sample.query,
            top_k=self.top_k,
        )

        output = self.generator.generate(
            question=sample.query,
            chunks=retrieved_chunks,
        )

        self._save_cache(sample.id, output)

        return evaluate_system(
            sample=sample,
            output=output,
            model_name=self.model_name,
        )

    # --------------------------------------------------------
    # Full benchmark dataset
    # --------------------------------------------------------

    def run_dataset(
        self,
        dataset: List[BenchmarkSample],
    ) -> List[EvaluationResult]:

        results: List[EvaluationResult] = []

        for sample in dataset:

            print(f"[{self.model_name}] {sample.id}")

            result = self.run_sample(sample)

            results.append(result)

        return results


# ============================================================
# Benchmark Summary
# ============================================================

def print_summary(results: List[EvaluationResult]):

    print("\n========== BENCHMARK SUMMARY ==========")

    print(f"Samples                : {len(results)}")
    print(
        f"Recall@K               : {mean(r.recall_at_k for r in results):.3f}"
    )
    print(
        f"Temporal Validity      : {mean(r.temporal_validity for r in results):.3f}"
    )
    print(
        f"Invalid Citation Rate  : {mean(r.invalid_citation_rate for r in results):.3f}"
    )
    print(
        f"Faithfulness           : {mean(r.faithfulness for r in results):.3f}"
    )
    print(
        f"Latency (ms)           : {mean(r.latency_ms for r in results):.1f}"
    )


# ============================================================
# Entry Point
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Run BM25 or Naive RAG benchmark."
    )

    parser.add_argument(
        "--model",
        type=str,
        required=True,
        choices=["bm25", "naive"],
        help="Retrieval baseline to evaluate.",
    )

    parser.add_argument(
        "--gold",
        type=str,
        default="data/gold/gold_chunks.parquet",
        help="Path to Gold chunk parquet.",
    )

    parser.add_argument(
        "--dataset",
        type=str,
        default="data/splits/vn_legal_temporal_eval_split.json",
        help="Benchmark dataset JSON.",
    )

    parser.add_argument(
        "--top_k",
        type=int,
        default=5,
        help="Number of retrieved chunks.",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output evaluation JSON.",
    )

    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse cached generated responses.",
    )

    args = parser.parse_args()

    dataset = load_dataset(args.dataset)

    runner = BenchmarkRunner(
        gold_path=args.gold,
        model_name=args.model,
        top_k=args.top_k,
        resume=args.resume,
    )

    results = runner.run_dataset(dataset)

    summary = {
        "model": args.model,
        "samples": len(results),
        "recall_at_k": round(
            mean(r.recall_at_k for r in results),
            3,
        ),
        "temporal_validity": round(
            mean(r.temporal_validity for r in results),
            3,
        ),
        "invalid_citation_rate": round(
            mean(r.invalid_citation_rate for r in results),
            3,
        ),
        "faithfulness": round(
            mean(r.faithfulness for r in results),
            3,
        ),
        "latency_ms": round(
            mean(r.latency_ms for r in results),
            1,
        ),
    }

    print_summary(results)

    if args.output is None:
        output_path = (
            Path("results")
            / f"{args.model}_benchmark.json"
        )
    else:
        output_path = Path(args.output)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "summary": summary,
                "results": [
                    asdict(result)
                    for result in results
                ],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\nSaved to: {output_path}")


if __name__ == "__main__":
    main()