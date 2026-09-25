from __future__ import annotations

import argparse
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from run_eval import (
    BenchmarkSample,
    SystemOutput,
    calculate_pipeline_overhead,
    evaluate_system,
    save_json,
)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def load_dataset(
    dataset_path: str | Path,
) -> List[BenchmarkSample]:
    """Load benchmark samples from JSON."""

    dataset_path = Path(dataset_path)

    with dataset_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        data = data.get(
            "samples",
            data.get("data", data),
        )

    if not isinstance(data, list):
        raise ValueError(
            "Benchmark dataset must be a JSON list or contain "
            "'samples'/'data'."
        )

    return [
        BenchmarkSample(
            query=item["query"],
            t_event=item.get("t_event"),
            gold_doc_ids=item.get("gold_doc_ids"),
            gold_chunk_ids=item.get("gold_chunk_ids"),
            gold_answer=item.get("gold_answer"),
            gold_citation_doc_ids=item.get(
                "gold_citation_doc_ids"
            ),
        )
        for item in data
    ]


# ---------------------------------------------------------------------------
# Benchmark system interface
# ---------------------------------------------------------------------------

class BenchmarkSystem(ABC):
    """Common interface for all benchmark systems."""

    name: str

    @abstractmethod
    def run(
        self,
        sample: BenchmarkSample,
        top_k: int,
    ) -> SystemOutput:
        """Run one benchmark sample."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# BM25
# ---------------------------------------------------------------------------

class BM25Adapter(BenchmarkSystem):
    """Adapter around BM25Baseline."""

    name = "BM25"

    def __init__(self, retriever: Any):
        self.retriever = retriever

    def run(
        self,
        sample: BenchmarkSample,
        top_k: int,
    ) -> SystemOutput:

        start = time.perf_counter()

        results = self.retriever.retrieve(
            query=sample.query,
            t_event=sample.t_event,
            top_k=top_k,
        )

        latency = time.perf_counter() - start

        retrieved_doc_ids: List[str] = []
        retrieved_chunk_ids: List[str] = []

        for item in results:
            doc_id = (
                item.get("doc_id")
                or item.get("logical_doc_id")
            )

            chunk_id = item.get("chunk_id")

            if doc_id:
                retrieved_doc_ids.append(doc_id)

            if chunk_id:
                retrieved_chunk_ids.append(chunk_id)

        return SystemOutput(
            system_name=self.name,
            retrieved_doc_ids=retrieved_doc_ids,
            retrieved_chunk_ids=retrieved_chunk_ids,
            latency_seconds=latency,
            metadata={
                "retrieval_method": "bm25",
            },
        )


# ---------------------------------------------------------------------------
# Naive Flat RAG
# ---------------------------------------------------------------------------

class NaiveRAGAdapter(BenchmarkSystem):
    """Adapter around NaiveRAGBaseline."""

    name = "Naive Flat RAG"

    def __init__(self, rag_system: Any):
        self.rag_system = rag_system

    def run(
        self,
        sample: BenchmarkSample,
        top_k: int,
    ) -> SystemOutput:

        start = time.perf_counter()

        retrieved_chunks = self.rag_system.retrieve(
            query=sample.query,
            t_event=sample.t_event,
            top_k=top_k,
        )

        generated = self.rag_system.generate_answer(
            query=sample.query,
            retrieved_chunks=retrieved_chunks,
        )

        latency = time.perf_counter() - start

        if not isinstance(generated, dict):
            raise TypeError(
                "NaiveRAGBaseline.generate_answer() "
                "must return a dictionary."
            )

        retrieved_doc_ids: List[str] = []
        retrieved_chunk_ids: List[str] = []

        for item in retrieved_chunks:
            if not isinstance(item, dict):
                continue

            doc_id = (
                item.get("doc_id")
                or item.get("logical_doc_id")
            )

            chunk_id = item.get("chunk_id")

            if doc_id:
                retrieved_doc_ids.append(doc_id)

            if chunk_id:
                retrieved_chunk_ids.append(chunk_id)

        return SystemOutput(
            system_name=self.name,
            answer=generated.get(
                "answer_text",
                generated.get("answer", ""),
            ),
            retrieved_doc_ids=retrieved_doc_ids,
            retrieved_chunk_ids=retrieved_chunk_ids,
            cited_doc_ids=generated.get(
                "cited_doc_ids"
            ),
            cited_chunk_ids=generated.get(
                "cited_chunk_ids"
            ),
            latency_seconds=latency,
            metadata={
                "retrieval_method": "flat_vector",
            },
        )


# ---------------------------------------------------------------------------
# VERITAS-RAG
# ---------------------------------------------------------------------------

class VeritasAdapter(BenchmarkSystem):
    """
    Adapter for the current VERITAS-RAG pipeline.

    Current flow:
        TemporalRetriever
            ↓
        LegalGeneratorAgent

    Optional future components:
        HARTValidator
        LineageTracer
    """

    name = "VERITAS-RAG"

    def __init__(
        self,
        retriever: Any,
        generator: Any,
        hart_validator: Optional[Any] = None,
        lineage_tracer: Optional[Any] = None,
    ):
        self.retriever = retriever
        self.generator = generator

        self.hart_validator = hart_validator
        self.lineage_tracer = lineage_tracer

    def run(
        self,
        sample: BenchmarkSample,
        top_k: int,
    ) -> SystemOutput:

        start = time.perf_counter()

        # ---------------------------------------------------------------
        # 1. Temporal retrieval
        # ---------------------------------------------------------------

        evidences = self.retriever.retrieve(
            query=sample.query,
            t_event=sample.t_event,
            top_k=top_k,
        )

        # ---------------------------------------------------------------
        # 2. Grounded generation
        # ---------------------------------------------------------------

        generated = self.generator.generate(
            query=sample.query,
            evidences=evidences,
            t_event=sample.t_event,
        )

        answer = ""
        cited_doc_ids: Optional[List[str]] = None
        cited_chunk_ids: Optional[List[str]] = None
        metadata: Dict[str, Any] = {}

        if isinstance(generated, str):
            answer = generated

        elif isinstance(generated, dict):
            answer = generated.get(
                "answer",
                generated.get("answer_text", ""),
            )

            cited_doc_ids = generated.get(
                "cited_doc_ids"
            )

            cited_chunk_ids = generated.get(
                "cited_chunk_ids"
            )

            metadata.update(
                generated.get("metadata", {})
            )

        else:
            answer = getattr(
                generated,
                "answer_text",
                "",
            )

            citations = getattr(
                generated,
                "citations",
                [],
            )

            cited_doc_ids = []
            cited_chunk_ids = []

            for citation in citations:
                doc_id = getattr(
                    citation,
                    "logical_doc_id",
                    None,
                )

                chunk_id = getattr(
                    citation,
                    "chunk_id",
                    None,
                )

                if doc_id:
                    cited_doc_ids.append(doc_id)

                if chunk_id:
                    cited_chunk_ids.append(chunk_id)

        # ---------------------------------------------------------------
        # 3. Normalize retrieved evidence
        # ---------------------------------------------------------------

        retrieved_doc_ids: List[str] = []
        retrieved_chunk_ids: List[str] = []

        for item in evidences:
            if isinstance(item, dict):
                doc_id = (
                    item.get("logical_doc_id")
                    or item.get("doc_id")
                )

                chunk_id = item.get("chunk_id")

            else:
                doc_id = getattr(
                    item,
                    "logical_doc_id",
                    None,
                )

                if doc_id is None:
                    doc_id = getattr(
                        item,
                        "doc_id",
                        None,
                    )

                chunk_id = getattr(
                    item,
                    "chunk_id",
                    None,
                )

            if doc_id:
                retrieved_doc_ids.append(doc_id)

            if chunk_id:
                retrieved_chunk_ids.append(chunk_id)

        # ---------------------------------------------------------------
        # 4. Optional HART
        # ---------------------------------------------------------------

        hart_result = None

        if self.hart_validator is not None:
            hart_result = self.hart_validator.validate(
                query=sample.query,
                answer=answer,
                evidences=evidences,
            )

            metadata["hart_enabled"] = True

        else:
            metadata["hart_enabled"] = False

        # ---------------------------------------------------------------
        # 5. Optional lineage
        # ---------------------------------------------------------------

        lineage_records = None

        if self.lineage_tracer is not None:
            lineage_records = self.lineage_tracer.trace(
                evidences=evidences,
                citations=cited_chunk_ids,
            )

            metadata["lineage_enabled"] = True

        else:
            metadata["lineage_enabled"] = False

        latency = time.perf_counter() - start

        return SystemOutput(
            system_name=self.name,
            answer=answer,
            retrieved_doc_ids=retrieved_doc_ids,
            retrieved_chunk_ids=retrieved_chunk_ids,
            cited_doc_ids=cited_doc_ids,
            cited_chunk_ids=cited_chunk_ids,
            lineage_records=lineage_records,
            hart_result=hart_result,
            latency_seconds=latency,
            metadata=metadata,
        )


# ---------------------------------------------------------------------------
# System construction
# ---------------------------------------------------------------------------

def build_systems(
    lakehouse_manager: Any,
    enable_veritas: bool = True,
) -> List[BenchmarkSystem]:
    """
    Build benchmark systems from the existing project components.
    """

    from src.baselines.bm25_baseline import BM25Baseline
    from src.baselines.naive_rag_baseline import NaiveRAGBaseline

    systems: List[BenchmarkSystem] = []

    # ---------------------------------------------------------------
    # BM25
    # ---------------------------------------------------------------

    bm25 = BM25Baseline()

    bm25.index_from_lakehouse(
        lakehouse_manager
    )

    systems.append(
        BM25Adapter(bm25)
    )

    # ---------------------------------------------------------------
    # Naive Flat RAG
    # ---------------------------------------------------------------

    naive_rag = NaiveRAGBaseline()

    naive_rag.index_from_lakehouse(
        lakehouse_manager
    )

    systems.append(
        NaiveRAGAdapter(naive_rag)
    )

    # ---------------------------------------------------------------
    # VERITAS-RAG
    # ---------------------------------------------------------------

    if enable_veritas:

        from src.rag.temporal_retriever import TemporalRetriever
        from src.rag.generator_agent import LegalGeneratorAgent

        temporal_retriever = TemporalRetriever(
            lakehouse_manager=lakehouse_manager
        )

        generator = LegalGeneratorAgent()

        systems.append(
            VeritasAdapter(
                retriever=temporal_retriever,
                generator=generator,

                # Current Seminar:
                # HART and Lineage are not implemented yet.
                hart_validator=None,
                lineage_tracer=None,
            )
        )

    return systems


# ---------------------------------------------------------------------------
# Benchmark execution
# ---------------------------------------------------------------------------

def run_system(
    system: BenchmarkSystem,
    samples: List[BenchmarkSample],
    top_k: int,
) -> List[SystemOutput]:
    """Run one system over all benchmark samples."""

    outputs: List[SystemOutput] = []

    for index, sample in enumerate(samples, start=1):

        print(
            f"[{system.name}] "
            f"{index}/{len(samples)}"
        )

        output = system.run(
            sample=sample,
            top_k=top_k,
        )

        outputs.append(output)

    return outputs


def benchmark_system(
    system: BenchmarkSystem,
    samples: List[BenchmarkSample],
    top_k: int,
) -> Dict[str, Any]:
    """Run and evaluate one system."""

    outputs = run_system(
        system=system,
        samples=samples,
        top_k=top_k,
    )

    evaluation = evaluate_system(
        outputs=outputs,
        samples=samples,
        recall_k=top_k,
    )

    evaluation["outputs"] = [
        {
            "query": sample.query,
            "t_event": sample.t_event,
            "answer": output.answer,
            "retrieved_doc_ids": output.retrieved_doc_ids,
            "retrieved_chunk_ids": output.retrieved_chunk_ids,
            "cited_doc_ids": output.cited_doc_ids,
            "cited_chunk_ids": output.cited_chunk_ids,
            "latency_seconds": output.latency_seconds,
            "metadata": output.metadata,
        }
        for sample, output in zip(samples, outputs)
    ]

    return evaluation


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def build_comparison(
    evaluations: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build a cross-system comparison.

    Metrics that are not supported by a system remain null.
    """

    comparison: Dict[str, Any] = {
        "systems": {},
        "pipeline_overhead": {},
    }

    for system_name, evaluation in evaluations.items():

        comparison["systems"][system_name] = (
            evaluation.get("metrics", {})
        )

    # ---------------------------------------------------------------
    # Pipeline overhead
    # ---------------------------------------------------------------

    latencies = {
        name: evaluation.get(
            "metrics",
            {},
        ).get("average_latency_seconds")
        for name, evaluation in evaluations.items()
    }

    veritas_latency = latencies.get("VERITAS-RAG")

    baseline_latencies = {
        name: latency
        for name, latency in latencies.items()
        if name != "VERITAS-RAG"
        and latency is not None
    }

    comparison["pipeline_overhead"][
        "veritas_vs_baselines"
    ] = {}

    if veritas_latency is not None:
        for baseline_name, baseline_latency in baseline_latencies.items():
            comparison["pipeline_overhead"][
                "veritas_vs_baselines"
            ][baseline_name] = calculate_pipeline_overhead(
                system_latency=veritas_latency,
                baseline_latency=baseline_latency,
            )

    return comparison


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Run the BM25, Naive Flat RAG, and "
            "VERITAS-RAG benchmark."
        )
    )

    parser.add_argument(
        "--dataset",
        required=True,
        help="Path to benchmark dataset JSON.",
    )

    parser.add_argument(
        "--output-dir",
        default="results/seminar",
        help="Directory for benchmark results.",
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Retrieval top-k.",
    )

    parser.add_argument(
        "--skip-veritas",
        action="store_true",
        help="Skip VERITAS-RAG.",
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ---------------------------------------------------------------
    # Load benchmark data
    # ---------------------------------------------------------------

    samples = load_dataset(
        args.dataset
    )

    print(
        f"Loaded {len(samples)} benchmark samples."
    )

    # ---------------------------------------------------------------
    # Lakehouse
    # ---------------------------------------------------------------

    from src.database.lakehouse_manager import LakehouseManager

    lakehouse_manager = LakehouseManager()

    try:
        # -----------------------------------------------------------
        # Build systems
        # -----------------------------------------------------------

        systems = build_systems(
            lakehouse_manager=lakehouse_manager,
            enable_veritas=not args.skip_veritas,
        )

        # -----------------------------------------------------------
        # Run benchmark
        # -----------------------------------------------------------

        evaluations: Dict[str, Dict[str, Any]] = {}

        for system in systems:

            print()
            print("=" * 70)
            print(f"Running: {system.name}")
            print("=" * 70)

            evaluation = benchmark_system(
                system=system,
                samples=samples,
                top_k=args.top_k,
            )

            evaluations[system.name] = evaluation

            filename = (
                system.name
                .lower()
                .replace(" ", "_")
                .replace("-", "_")
                + "_results.json"
            )

            save_json(
                evaluation,
                output_dir / filename,
            )

        # -----------------------------------------------------------
        # Comparison
        # -----------------------------------------------------------

        comparison = build_comparison(
            evaluations
        )

        save_json(
            comparison,
            output_dir / "comparison.json",
        )

        print()
        print("=" * 70)
        print("Benchmark completed.")
        print("=" * 70)

        print(
            json.dumps(
                comparison,
                ensure_ascii=False,
                indent=2,
            )
        )

    finally:
        lakehouse_manager.close()


if __name__ == "__main__":
    main()