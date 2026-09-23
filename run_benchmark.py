from __future__ import annotations

import argparse
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List

from run_eval import (
    BenchmarkSample,
    SystemOutput,
    evaluate_system,
    load_evaluation_dataset,
    save_json,
    save_system_result,
)


# ============================================================================
# System interface
# ============================================================================


class BenchmarkSystem(ABC):

    name: str

    @abstractmethod
    def run(
        self,
        sample: BenchmarkSample,
    ) -> SystemOutput:
        """Run the system for one evaluation sample."""
        raise NotImplementedError


# ============================================================================
# Utility
# ============================================================================


def _extract_doc_id(
    item: Dict[str, Any],
) -> Any:

    return (
        item.get("doc_id")
        or item.get("logical_doc_id")
    )


def _extract_chunk_id(
    item: Dict[str, Any],
) -> Any:

    return item.get("chunk_id")


def _normalize_retrieved_chunks(
    items: List[Dict[str, Any]],
) -> tuple[List[str], List[str]]:

    doc_ids: List[str] = []
    chunk_ids: List[str] = []

    for item in items:

        doc_id = _extract_doc_id(item)
        chunk_id = _extract_chunk_id(item)

        if doc_id is not None:
            doc_ids.append(str(doc_id))

        if chunk_id is not None:
            chunk_ids.append(str(chunk_id))

    return doc_ids, chunk_ids


# ============================================================================
# BM25
# ============================================================================


class BM25Adapter(BenchmarkSystem):

    name = "bm25"

    def __init__(
        self,
        retriever: Any,
        top_k: int = 5,
    ) -> None:

        self.retriever = retriever
        self.top_k = top_k

    def run(
        self,
        sample: BenchmarkSample,
    ) -> SystemOutput:

        start = time.perf_counter()

        results = self.retriever.retrieve(
            query=sample.query,
            t_event=sample.t_event,
            top_k=self.top_k,
        )

        latency = (
            time.perf_counter()
            - start
        )

        doc_ids, chunk_ids = (
            _normalize_retrieved_chunks(
                results
            )
        )

        return SystemOutput(
            system_name=self.name,
            retrieved_doc_ids=doc_ids,
            retrieved_chunk_ids=chunk_ids,
            latency_seconds=latency,
            metadata={
                "retrieval_method": "bm25",
            },
        )


# ============================================================================
# Naive Flat RAG
# ============================================================================


class NaiveRAGAdapter(BenchmarkSystem):

    name = "naive_rag"

    def __init__(
        self,
        rag_system: Any,
        top_k: int = 5,
    ) -> None:

        self.rag_system = rag_system
        self.top_k = top_k

    def run(
        self,
        sample: BenchmarkSample,
    ) -> SystemOutput:

        start = time.perf_counter()

        result = self.rag_system.run(
            query=sample.query,
            t_event=sample.t_event,
            top_k=self.top_k,
        )

        latency = (
            time.perf_counter()
            - start
        )

        retrieved = result.get(
            "retrieved_chunks",
            [],
        )

        doc_ids, chunk_ids = (
            _normalize_retrieved_chunks(
                retrieved
            )
        )

        return SystemOutput(
            system_name=self.name,
            answer=result.get(
                "answer",
                "",
            ),
            retrieved_doc_ids=doc_ids,
            retrieved_chunk_ids=chunk_ids,
            cited_doc_ids=result.get(
                "cited_doc_ids",
                [],
            ),
            cited_chunk_ids=result.get(
                "cited_chunk_ids",
                [],
            ),
            latency_seconds=latency,
            metadata={
                "retrieval_method": "naive_flat_rag",
            },
        )


# ============================================================================
# Current partial VERITAS-RAG
# ============================================================================


class VeritasAdapter(BenchmarkSystem):

    name = "veritas_rag"

    def __init__(
        self,
        retriever: Any,
        generator: Any,
        top_k: int = 5,
        hart_validator: Any = None,
        lineage_tracer: Any = None,
    ) -> None:

        self.retriever = retriever
        self.generator = generator
        self.top_k = top_k

        # Future Thesis components.
        self.hart_validator = hart_validator
        self.lineage_tracer = lineage_tracer

    def run(
        self,
        sample: BenchmarkSample,
    ) -> SystemOutput:

        start = time.perf_counter()

        # --------------------------------------------------------------
        # 1. Temporal / Gold retrieval
        # --------------------------------------------------------------

        retrieved = self.retriever.retrieve(
            query=sample.query,
            t_event=sample.t_event,
            top_k=self.top_k,
        )

        doc_ids, chunk_ids = (
            _normalize_retrieved_chunks(
                retrieved
            )
        )

        # --------------------------------------------------------------
        # 2. Generation
        # --------------------------------------------------------------

        generation_result = self.generator.generate(
            query=sample.query,
            evidences=retrieved,
            t_event=sample.t_event,
        )

        if isinstance(
            generation_result,
            str,
        ):
            answer = generation_result
            cited_doc_ids = []
            cited_chunk_ids = []

        else:
            answer = generation_result.get(
                "answer",
                generation_result.get(
                    "answer_text",
                    "",
                ),
            )

            cited_doc_ids = generation_result.get(
                "cited_doc_ids",
                [],
            )

            cited_chunk_ids = generation_result.get(
                "cited_chunk_ids",
                [],
            )

        # --------------------------------------------------------------
        # 3. HART — currently skipped
        # --------------------------------------------------------------

        hart_result = None

        if self.hart_validator is not None:
            hart_result = (
                self.hart_validator.validate(
                    answer=answer,
                    evidences=retrieved,
                )
            )

        # --------------------------------------------------------------
        # 4. Lineage — currently skipped
        # --------------------------------------------------------------

        lineage_records = None

        if self.lineage_tracer is not None:
            lineage_records = (
                self.lineage_tracer.trace(
                    answer=answer,
                    evidences=retrieved,
                )
            )

        # --------------------------------------------------------------
        # 5. Runtime
        # --------------------------------------------------------------

        latency = (
            time.perf_counter()
            - start
        )

        return SystemOutput(
            system_name=self.name,
            answer=answer,
            retrieved_doc_ids=doc_ids,
            retrieved_chunk_ids=chunk_ids,
            cited_doc_ids=cited_doc_ids,
            cited_chunk_ids=cited_chunk_ids,
            lineage_records=lineage_records,
            hart_result=hart_result,
            latency_seconds=latency,
            metadata={
                "retrieval_method": "veritas_temporal",
                "hart_enabled": (
                    self.hart_validator is not None
                ),
                "lineage_enabled": (
                    self.lineage_tracer is not None
                ),
            },
        )


# ============================================================================
# Benchmark execution
# ============================================================================


def run_system(
    system: BenchmarkSystem,
    samples: List[BenchmarkSample],
) -> Dict[str, Any]:

    print(
        f"\n{'=' * 70}"
    )

    print(
        f"Running: {system.name}"
    )

    print(
        f"{'=' * 70}"
    )

    outputs: List[SystemOutput] = []

    for index, sample in enumerate(
        samples,
        start=1,
    ):

        print(
            f"[{system.name}] "
            f"{index}/{len(samples)}"
        )

        output = system.run(
            sample
        )

        outputs.append(
            output
        )

    metrics = evaluate_system(
        samples,
        outputs,
    )

    return {
        "system": system.name,
        "metrics": metrics,
        "outputs": outputs,
    }


# ============================================================================
# Build systems
# ============================================================================


def build_systems(
    lakehouse_manager: Any,
    *,
    enable_veritas: bool = True,
) -> List[BenchmarkSystem]:

    from src.baselines.bm25_baseline import (
        BM25Baseline,
    )

    from src.baselines.naive_rag_baseline import (
        NaiveRAGBaseline,
    )

    # --------------------------------------------------------------
    # BM25
    # --------------------------------------------------------------

    bm25 = BM25Baseline(
        lakehouse_manager=lakehouse_manager,
    )

    # --------------------------------------------------------------
    # Naive Flat RAG
    # --------------------------------------------------------------

    naive_rag = NaiveRAGBaseline(
        lakehouse_manager=lakehouse_manager,
    )

    systems: List[BenchmarkSystem] = [
        BM25Adapter(
            retriever=bm25,
            top_k=5,
        ),

        NaiveRAGAdapter(
            rag_system=naive_rag,
            top_k=5,
        ),
    ]

    # --------------------------------------------------------------
    # VERITAS
    # --------------------------------------------------------------

    if enable_veritas:

        from src.rag.temporal_retriever import (
            TemporalRetriever,
        )

        from src.rag.generator_agent import (
            LegalGeneratorAgent,
        )

        temporal_retriever = (
            TemporalRetriever(
                lakehouse_manager=lakehouse_manager,
            )
        )

        generator = LegalGeneratorAgent()

        veritas = VeritasAdapter(
            retriever=temporal_retriever,
            generator=generator,

            # Seminar:
            # HART not implemented yet.
            hart_validator=None,

            # Seminar:
            # Lineage not implemented yet.
            lineage_tracer=None,

            top_k=5,
        )

        systems.append(
            veritas
        )

    return systems


# ============================================================================
# Benchmark comparison table
# ============================================================================


def build_comparison(
    results: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:

    comparison: Dict[str, Dict[str, Any]] = {}

    for result in results:

        comparison[
            result["system"]
        ] = result["metrics"]

    return comparison

# ============================================================================
# CLI
# ============================================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Run BM25, Naive Flat RAG and "
            "VERITAS-RAG benchmark."
        )
    )

    parser.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help=(
            "Evaluation dataset JSON."
        ),
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(
            "results/seminar"
        ),
        help=(
            "Directory for benchmark results."
        ),
    )

    parser.add_argument(
        "--skip-veritas",
        action="store_true",
        help=(
            "Run only BM25 and Naive RAG."
        ),
    )

    args = parser.parse_args()

    # --------------------------------------------------------------
    # Dataset
    # --------------------------------------------------------------

    samples = load_evaluation_dataset(
        args.dataset
    )

    print(
        f"Loaded {len(samples)} "
        f"evaluation samples."
    )

    # --------------------------------------------------------------
    # Lakehouse
    # --------------------------------------------------------------

    from src.database.lakehouse_manager import (
        LakehouseManager,
    )

    lakehouse = LakehouseManager()

    # --------------------------------------------------------------
    # Systems
    # --------------------------------------------------------------

    systems = build_systems(
        lakehouse_manager=lakehouse,
        enable_veritas=(
            not args.skip_veritas
        ),
    )

    # --------------------------------------------------------------
    # Run
    # --------------------------------------------------------------

    all_results: List[
        Dict[str, Any]
    ] = []

    for system in systems:

        result = run_system(
            system,
            samples,
        )

        all_results.append(
            result
        )

        output_path = (
            args.output_dir
            / f"{system.name}_results.json"
        )

        save_system_result(
            system_name=system.name,
            metrics=result["metrics"],
            outputs=result["outputs"],
            path=output_path,
        )

        print(
            f"\nSaved: {output_path}"
        )

    # --------------------------------------------------------------
    # Comparison
    # --------------------------------------------------------------

    comparison = build_comparison(
        all_results
    )

    comparison_path = (
        args.output_dir
        / "comparison.json"
    )

    save_json(
        comparison,
        comparison_path,
    )

    print(
        f"\nSaved comparison: "
        f"{comparison_path}"
    )

    # --------------------------------------------------------------
    # Console summary
    # --------------------------------------------------------------

    print(
        "\n"
        + "=" * 80
    )

    print(
        "BENCHMARK SUMMARY"
    )

    print(
        "=" * 80
    )

    metric_names = [
        "temporal_validity_accuracy",
        "invalid_citation_rate",
        "retrieval_recall_at_k",
        "answer_faithfulness",
        "lineage_traceability",
        "average_latency_seconds",
    ]

    for system_name, metrics in comparison.items():

        print(
            f"\n{system_name}"
        )

        for metric_name in metric_names:

            value = metrics.get(
                metric_name
            )

            if value is None:
                print(
                    f"  {metric_name}: N/A"
                )
            else:
                print(
                    f"  {metric_name}: "
                    f"{value:.4f}"
                )


if __name__ == "__main__":
    main()