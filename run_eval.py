from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


# ============================================================================
# Data structures
# ============================================================================


@dataclass
class BenchmarkSample:
    """
    One evaluation sample.

    Example:
    {
        "query": "Điều kiện ...?",
        "t_event": "2025-01-01",
        "gold_doc_ids": ["123/2020/NĐ-CP"],
        "gold_chunk_ids": ["chunk_001"],
        "gold_answer": "...",
        "gold_citation_doc_ids": ["123/2020/NĐ-CP"]
    }
    """

    query: str
    t_event: Optional[str] = None

    gold_doc_ids: Optional[List[str]] = None
    gold_chunk_ids: Optional[List[str]] = None

    gold_answer: Optional[str] = None
    gold_citation_doc_ids: Optional[List[str]] = None


@dataclass
class SystemOutput:

    system_name: str

    answer: str = ""

    retrieved_doc_ids: Optional[List[str]] = None
    retrieved_chunk_ids: Optional[List[str]] = None

    cited_doc_ids: Optional[List[str]] = None
    cited_chunk_ids: Optional[List[str]] = None

    # Future VERITAS capability.
    lineage_records: Optional[List[Dict[str, Any]]] = None

    # Future HART capability.
    hart_result: Optional[Dict[str, Any]] = None

    latency_seconds: Optional[float] = None

    metadata: Optional[Dict[str, Any]] = None


# ============================================================================
# Dataset
# ============================================================================


def load_evaluation_dataset(
    path: str | Path,
) -> List[BenchmarkSample]:
    """Load benchmark samples from JSON."""

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Evaluation dataset not found: {path}"
        )

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict):
        data = data.get("samples", [])

    if not isinstance(data, list):
        raise ValueError(
            "Evaluation dataset must be a list or contain "
            "a 'samples' list."
        )

    samples: List[BenchmarkSample] = []

    for item in data:
        samples.append(
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
        )

    return samples


# ============================================================================
# Metric: Retrieval Recall@K
# ============================================================================


def retrieval_recall_at_k(
    gold_chunk_ids: Sequence[str],
    retrieved_chunk_ids: Sequence[str],
) -> float:
    """
    Binary Recall@K for one sample.

    A sample is counted as correctly retrieved when at least one
    gold evidence chunk appears in the top-K results.

    Aggregate score = mean across evaluation samples.
    """

    gold = set(gold_chunk_ids)
    retrieved = set(retrieved_chunk_ids)

    if not gold:
        return 0.0

    return float(bool(gold & retrieved))


def evaluate_retrieval_recall_at_k(
    samples: Sequence[BenchmarkSample],
    outputs: Sequence[SystemOutput],
) -> float:
    """Calculate aggregate Retrieval Recall@K."""

    scores: List[float] = []

    for sample, output in zip(samples, outputs):
        scores.append(
            retrieval_recall_at_k(
                sample.gold_chunk_ids or [],
                output.retrieved_chunk_ids or [],
            )
        )

    return sum(scores) / len(scores) if scores else 0.0


# ============================================================================
# Metric: Temporal Validity Accuracy
# ============================================================================


def temporal_validity_accuracy(
    sample: BenchmarkSample,
    output: SystemOutput,
) -> Optional[float]:
    """
    Determine whether the retrieved documents contain at least one
    document known to be legally valid for t_event.

    The temporal ground truth is defined by the evaluation dataset.
    """

    if not sample.gold_doc_ids:
        return None

    gold = set(sample.gold_doc_ids)
    retrieved = set(output.retrieved_doc_ids or [])

    return float(bool(gold & retrieved))


def evaluate_temporal_validity_accuracy(
    samples: Sequence[BenchmarkSample],
    outputs: Sequence[SystemOutput],
) -> Optional[float]:
    """Calculate aggregate Temporal Validity Accuracy."""

    scores: List[float] = []

    for sample, output in zip(samples, outputs):
        score = temporal_validity_accuracy(
            sample,
            output,
        )

        if score is not None:
            scores.append(score)

    if not scores:
        return None

    return sum(scores) / len(scores)


# ============================================================================
# Metric: Invalid Citation Rate
# ============================================================================


def invalid_citation_rate(
    sample: BenchmarkSample,
    output: SystemOutput,
) -> Optional[float]:
    """
    Percentage of cited documents that are not valid according
    to the temporal ground truth.

    If a system produces no citations, the sample is skipped.
    """

    cited = output.cited_doc_ids or []

    if not cited:
        return None

    valid = set(sample.gold_doc_ids or [])

    invalid = sum(
        1
        for doc_id in cited
        if doc_id not in valid
    )

    return invalid / len(cited)


def evaluate_invalid_citation_rate(
    samples: Sequence[BenchmarkSample],
    outputs: Sequence[SystemOutput],
) -> Optional[float]:
    """Calculate aggregate Invalid Citation Rate."""

    scores: List[float] = []

    for sample, output in zip(samples, outputs):
        score = invalid_citation_rate(
            sample,
            output,
        )

        if score is not None:
            scores.append(score)

    if not scores:
        return None

    return sum(scores) / len(scores)


# ============================================================================
# Metric: Answer Faithfulness
# ============================================================================


def evaluate_answer_faithfulness(
    samples: Sequence[BenchmarkSample],
    outputs: Sequence[SystemOutput],
) -> Optional[float]:

    scores: List[float] = []

    for output in outputs:
        metadata = output.metadata or {}

        score = metadata.get(
            "faithfulness_score"
        )

        if score is not None:
            scores.append(float(score))

    if not scores:
        return None

    return sum(scores) / len(scores)


# ============================================================================
# Metric: Lineage Traceability
# ============================================================================


def evaluate_lineage_traceability(
    outputs: Sequence[SystemOutput],
) -> Optional[float]:

    total = 0
    traceable = 0

    for output in outputs:
        for record in output.lineage_records or []:
            total += 1

            required = (
                "chunk_id",
                "logical_doc_id",
                "physical_file_id",
                "physical_page",
            )

            if all(record.get(key) is not None for key in required):
                traceable += 1

    if total == 0:
        return None

    return traceable / total


# ============================================================================
# Metric: Pipeline Overhead
# ============================================================================


def calculate_pipeline_overhead(
    baseline_latency: float,
    veritas_latency: float,
) -> Optional[float]:
    """
    Calculate VERITAS overhead relative to a baseline.

    Result is percentage.

        ((VERITAS - baseline) / baseline) * 100
    """

    if baseline_latency <= 0:
        return None

    return (
        (veritas_latency - baseline_latency)
        / baseline_latency
        * 100.0
    )


def average_latency(
    outputs: Sequence[SystemOutput],
) -> Optional[float]:
    """Average end-to-end latency."""

    values = [
        output.latency_seconds
        for output in outputs
        if output.latency_seconds is not None
    ]

    if not values:
        return None

    return sum(values) / len(values)


# ============================================================================
# Full evaluation
# ============================================================================


def evaluate_system(
    samples: Sequence[BenchmarkSample],
    outputs: Sequence[SystemOutput],
) -> Dict[str, Any]:
    """
    Calculate all metrics that are currently available.

    Metrics that require future VERITAS components return None
    rather than fabricated values.
    """

    return {
        "temporal_validity_accuracy":
            evaluate_temporal_validity_accuracy(
                samples,
                outputs,
            ),

        "invalid_citation_rate":
            evaluate_invalid_citation_rate(
                samples,
                outputs,
            ),

        "retrieval_recall_at_k":
            evaluate_retrieval_recall_at_k(
                samples,
                outputs,
            ),

        "answer_faithfulness":
            evaluate_answer_faithfulness(
                samples,
                outputs,
            ),

        "lineage_traceability":
            evaluate_lineage_traceability(
                outputs,
            ),

        "average_latency_seconds":
            average_latency(
                outputs,
            ),
    }


# ============================================================================
# Result persistence
# ============================================================================


def save_json(
    data: Dict[str, Any],
    path: str | Path,
) -> None:
    """Save JSON result."""

    path = Path(path)
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


def save_system_result(
    system_name: str,
    metrics: Dict[str, Any],
    outputs: Sequence[SystemOutput],
    path: str | Path,
) -> None:
    """Save metrics and normalized outputs."""

    payload = {
        "system": system_name,
        "metrics": metrics,
        "outputs": [
            asdict(output)
            for output in outputs
        ],
    }

    save_json(
        payload,
        path,
    )