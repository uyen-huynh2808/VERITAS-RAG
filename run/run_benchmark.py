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
from src.rag.veritas_model import VeritasModel
from src.core.config import load_system_config

from .run_eval import (
    BenchmarkSample,
    EvaluationResult,
    RetrievedChunk,
    SystemOutput,
    evaluate_system,
    load_dataset,
)

CACHE_VERSION = 9


# ============================================================
# Benchmark Orchestrator
# ============================================================

class BenchmarkRunner:
    """
    End-to-end benchmark pipeline.

    Gold Chunks
         │
         ▼
    BM25 / Naive / VERITAS Retriever
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
        top_k: int | None = None,
        resume: bool = False,
        cache_dir: str | Path | None = None,
    ):
        self.config = load_system_config()
        self.model_name = model_name.lower()
        self.top_k = top_k if top_k is not None else self.config.retrieval.top_k
        self.resume = resume

        if self.model_name == "bm25":
            self.retriever = BM25Retriever(gold_path)
        elif self.model_name == "naive":
            self.retriever = NaiveDenseRetriever(gold_path)
        elif self.model_name == "veritas":
            self.retriever = VeritasModel(
                config=self.config,
                gold_path=gold_path,
            )
        else:
            raise ValueError("model_name must be 'bm25', 'naive', or 'veritas'")

        self.generator = GeneratorAgent()

        self.cache_dir = Path(cache_dir or self.config.benchmark.cache_dir)
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
                    k=self.top_k,
                )

        retrieve_args = {
            "query": sample.query,
            "t_event": sample.t_event,
            "top_k": self.top_k,
        }
        if self.model_name == "veritas":
            retrieve_args["date_basis"] = sample.date_basis
        retrieved_chunks = self.retriever.retrieve(**retrieve_args)

        output = self.generator.generate(
            question=sample.query,
            chunks=retrieved_chunks,
        )

        self._save_cache(sample.id, output)

        return evaluate_system(
            sample=sample,
            output=output,
            model_name=self.model_name,
            k=self.top_k,
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
    recall_values = [r.recall_at_k for r in results if r.recall_at_k is not None]
    print(
        f"Recall@K               : {mean(recall_values):.3f}"
        if recall_values
        else "Recall@K               : N/A"
    )
    temporal_values = [
        r.temporal_validity for r in results
        if r.temporal_validity is not None
    ]
    temporal_summary = f"{mean(temporal_values):.3f}" if temporal_values else "N/A"
    print(f"Temporal Validity      : {temporal_summary}")
    print(
        f"Temporal Coverage     : {mean(r.temporal_coverage for r in results):.3f}"
    )
    citation_error_values = [
        r.invalid_citation_rate for r in results
        if r.invalid_citation_rate is not None
    ]
    print(
        f"Invalid Citation Rate  : {mean(citation_error_values):.3f}"
        if citation_error_values
        else "Invalid Citation Rate  : N/A"
    )
    citation_values = [r.citation_coverage for r in results if r.citation_coverage is not None]
    print(
        f"Citation Coverage      : {mean(citation_values):.3f}"
        if citation_values
        else "Citation Coverage      : N/A"
    )
    faithfulness_values = [
        r.faithfulness for r in results if r.faithfulness is not None
    ]
    print(
        f"Faithfulness           : {mean(faithfulness_values):.3f}"
        if faithfulness_values
        else "Faithfulness           : N/A"
    )
    empty_answers = sum(r.faithfulness is None for r in results)
    print(f"Empty Answers          : {empty_answers}")
    print(
        f"Latency (ms)           : {mean(r.latency_ms for r in results):.1f}"
    )


# ============================================================
# Entry Point
# ============================================================

def main():

    parser = argparse.ArgumentParser(
        description="Run the BM25, Naive RAG, or VERITAS benchmark."
    )

    parser.add_argument(
        "--model",
        type=str,
        required=True,
        choices=["bm25", "naive", "veritas"],
        help="Retrieval model to evaluate.",
    )

    parser.add_argument(
        "--gold",
        type=str,
        default=None,
        help="Path to Gold chunk parquet.",
    )

    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="Benchmark dataset JSON.",
    )

    parser.add_argument(
        "--top_k",
        type=int,
        default=None,
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
    config = load_system_config()
    gold_path = args.gold or config.benchmark.gold_path
    dataset_path = args.dataset or config.benchmark.output_path

    dataset = load_dataset(dataset_path)

    runner = BenchmarkRunner(
        gold_path=gold_path,
        model_name=args.model,
        top_k=args.top_k,
        resume=args.resume,
    )

    results = runner.run_dataset(dataset)

    summary = {
        "model": args.model,
        "samples": len(results),
        "recall_at_k": (
            round(mean(
                r.recall_at_k for r in results
                if r.recall_at_k is not None
            ), 3)
            if any(r.recall_at_k is not None for r in results)
            else None
        ),
        "recall_assessable_samples": sum(
            r.recall_at_k is not None for r in results
        ),
        "temporal_validity": (
            round(mean(
                r.temporal_validity for r in results
                if r.temporal_validity is not None
            ), 3)
            if any(r.temporal_validity is not None for r in results)
            else None
        ),
        "temporal_validity_assessable_samples": sum(
            r.temporal_validity is not None for r in results
        ),
        "temporal_coverage": round(
            mean(r.temporal_coverage for r in results),
            3,
        ),
        "invalid_citation_rate": (
            round(mean(
                r.invalid_citation_rate for r in results
                if r.invalid_citation_rate is not None
            ), 3)
            if any(r.invalid_citation_rate is not None for r in results)
            else None
        ),
        "invalid_citation_assessable_samples": sum(
            r.invalid_citation_rate is not None for r in results
        ),
        "citation_coverage": (
            round(mean(
                r.citation_coverage for r in results
                if r.citation_coverage is not None
            ), 3)
            if any(r.citation_coverage is not None for r in results)
            else None
        ),
        "citation_coverage_assessable_samples": sum(
            r.citation_coverage is not None for r in results
        ),
        "faithfulness": (
            round(mean(
                r.faithfulness for r in results
                if r.faithfulness is not None
            ), 3)
            if any(r.faithfulness is not None for r in results)
            else None
        ),
        "faithfulness_assessable_samples": sum(
            r.faithfulness is not None for r in results
        ),
        "empty_answer_samples": sum(r.faithfulness is None for r in results),
        "latency_ms": round(
            mean(r.latency_ms for r in results),
            1,
        ),
    }

    print_summary(results)

    if args.output is None:
        output_path = Path(config.benchmark.results_dir) / (
            f"{args.model}_benchmark.json"
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
