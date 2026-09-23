from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSample:
    """
    Ground-truth information for one benchmark query.

    gold_doc_ids:
        Documents considered temporally valid for this query at t_event.

    gold_chunk_ids:
        Gold evidence chunks relevant to the query.

    gold_answer:
        Reference answer, if available.

    gold_citation_doc_ids:
        Optional document-level citation ground truth.
    """

    query: str
    t_event: Optional[str] = None

    gold_doc_ids: Optional[List[str]] = None
    gold_chunk_ids: Optional[List[str]] = None

    gold_answer: Optional[str] = None
    gold_citation_doc_ids: Optional[List[str]] = None

@dataclass
class SystemOutput:
    """
    Standardized output produced by every benchmark system.
    """

    system_name: str

    answer: str = ""

    retrieved_doc_ids: Optional[List[str]] = None
    retrieved_chunk_ids: Optional[List[str]] = None

    cited_doc_ids: Optional[List[str]] = None
    cited_chunk_ids: Optional[List[str]] = None

    # Future VERITAS components
    lineage_records: Optional[List[Dict[str, Any]]] = None
    hart_result: Optional[Dict[str, Any]] = None

    latency_seconds: Optional[float] = None

    metadata: Optional[Dict[str, Any]] = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique(values: Optional[List[str]]) -> List[str]:
    """Return unique non-empty string values while preserving order."""

    if not values:
        return []

    result: List[str] = []

    for value in values:
        if value and value not in result:
            result.append(value)

    return result

# ---------------------------------------------------------------------------
# Retrieval metrics
# ---------------------------------------------------------------------------

def retrieval_recall_at_k(
    output: SystemOutput,
    sample: BenchmarkSample,
    k: int,
) -> Optional[float]:
    """
    Sample-level Recall@K.

    A sample is counted as a hit when at least one gold chunk is present
    among the top-k retrieved chunks.

    Returns None when no chunk-level ground truth is available.
    """

    if not sample.gold_chunk_ids:
        return None

    retrieved = _unique(output.retrieved_chunk_ids)[:k]
    gold = set(_unique(sample.gold_chunk_ids))

    if not gold:
        return None

    return 1.0 if any(chunk_id in gold for chunk_id in retrieved) else 0.0

def aggregate_retrieval_recall(
    outputs: List[SystemOutput],
    samples: List[BenchmarkSample],
    k: int,
) -> Optional[float]:
    """Calculate mean sample-level Recall@K."""

    values: List[float] = []

    for output, sample in zip(outputs, samples):
        score = retrieval_recall_at_k(output, sample, k)

        if score is not None:
            values.append(score)

    if not values:
        return None

    return sum(values) / len(values)

# ---------------------------------------------------------------------------
# Temporal validity
# ---------------------------------------------------------------------------

def temporal_validity_accuracy(
    output: SystemOutput,
    sample: BenchmarkSample,
) -> Optional[float]:
    """
    Check whether retrieved documents are temporally valid for t_event.

    The benchmark dataset must define gold_doc_ids as documents that are
    legally valid at the supplied t_event.

    A sample is counted as correct when at least one retrieved document
    belongs to that temporally valid ground-truth set.
    """

    if not sample.gold_doc_ids:
        return None

    retrieved = set(_unique(output.retrieved_doc_ids))
    valid_docs = set(_unique(sample.gold_doc_ids))

    if not valid_docs:
        return None

    return 1.0 if retrieved.intersection(valid_docs) else 0.0

def aggregate_temporal_validity_accuracy(
    outputs: List[SystemOutput],
    samples: List[BenchmarkSample],
) -> Optional[float]:
    """Calculate mean temporal validity accuracy."""

    values: List[float] = []

    for output, sample in zip(outputs, samples):
        score = temporal_validity_accuracy(output, sample)

        if score is not None:
            values.append(score)

    if not values:
        return None

    return sum(values) / len(values)

# ---------------------------------------------------------------------------
# Citation metrics
# ---------------------------------------------------------------------------

def invalid_citation_rate(
    output: SystemOutput,
    sample: BenchmarkSample,
) -> Optional[float]:
    """
    Fraction of cited documents that are not temporally valid gold documents.

    Returns None when no citations were produced.
    """

    cited = _unique(output.cited_doc_ids)

    if not cited:
        return None

    if not sample.gold_doc_ids:
        return None

    valid_docs = set(_unique(sample.gold_doc_ids))

    invalid_count = sum(
        1 for doc_id in cited if doc_id not in valid_docs
    )

    return invalid_count / len(cited)

def aggregate_invalid_citation_rate(
    outputs: List[SystemOutput],
    samples: List[BenchmarkSample],
) -> Optional[float]:
    """Calculate mean invalid citation rate."""

    values: List[float] = []

    for output, sample in zip(outputs, samples):
        score = invalid_citation_rate(output, sample)

        if score is not None:
            values.append(score)

    if not values:
        return None

    return sum(values) / len(values)

# ---------------------------------------------------------------------------
# Answer faithfulness
# ---------------------------------------------------------------------------

def answer_faithfulness(
    output: SystemOutput,
) -> Optional[float]:
    """
    Return an externally computed faithfulness score.

    The benchmark does not fabricate a score.

    Future evaluators may store:
        output.metadata["faithfulness_score"]
    """

    if not output.metadata:
        return None

    score = output.metadata.get("faithfulness_score")

    if score is None:
        return None

    try:
        return float(score)
    except (TypeError, ValueError):
        return None

def aggregate_answer_faithfulness(
    outputs: List[SystemOutput],
) -> Optional[float]:
    """Calculate mean answer faithfulness."""

    values: List[float] = []

    for output in outputs:
        score = answer_faithfulness(output)

        if score is not None:
            values.append(score)

    if not values:
        return None

    return sum(values) / len(values)

# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

def lineage_traceability(
    output: SystemOutput,
) -> Optional[float]:
    """
    Check page-level lineage completeness.

    Required lineage fields:
    - chunk_id
    - logical_doc_id
    - physical_file_id
    - physical_page

    Returns None when lineage has not been implemented.
    """

    if output.lineage_records is None:
        return None

    if not output.lineage_records:
        return 0.0

    required_fields = {
        "chunk_id",
        "logical_doc_id",
        "physical_file_id",
        "physical_page",
    }

    valid_records = 0

    for record in output.lineage_records:
        if required_fields.issubset(record.keys()):
            valid_records += 1

    return valid_records / len(output.lineage_records)

def aggregate_lineage_traceability(
    outputs: List[SystemOutput],
) -> Optional[float]:
    """Calculate mean lineage traceability."""

    values: List[float] = []

    for output in outputs:
        score = lineage_traceability(output)

        if score is not None:
            values.append(score)

    if not values:
        return None

    return sum(values) / len(values)

# ---------------------------------------------------------------------------
# Latency / overhead
# ---------------------------------------------------------------------------

def average_latency(
    outputs: List[SystemOutput],
) -> Optional[float]:
    """Calculate average latency in seconds."""

    values = [
        output.latency_seconds
        for output in outputs
        if output.latency_seconds is not None
    ]

    if not values:
        return None

    return sum(values) / len(values)

def calculate_pipeline_overhead(
    system_latency: Optional[float],
    baseline_latency: Optional[float],
) -> Optional[float]:
    """
    Calculate relative pipeline overhead.

    Formula:
        (system_latency - baseline_latency) / baseline_latency

    Example:
        baseline = 1.0s
        system   = 1.2s
        overhead = 0.20 = 20%
    """

    if system_latency is None or baseline_latency is None:
        return None

    if baseline_latency <= 0:
        return None

    return (system_latency - baseline_latency) / baseline_latency

# ---------------------------------------------------------------------------
# System-level evaluation
# ---------------------------------------------------------------------------

def evaluate_system(
    outputs: List[SystemOutput],
    samples: List[BenchmarkSample],
    recall_k: int = 5,
) -> Dict[str, Any]:
    """
    Evaluate one benchmark system against the supplied dataset.
    """

    return {
        "system_name": (
            outputs[0].system_name
            if outputs
            else None
        ),
        "num_samples": len(samples),
        "metrics": {
            "retrieval_recall_at_k": aggregate_retrieval_recall(
                outputs,
                samples,
                recall_k,
            ),
            "temporal_validity_accuracy": (
                aggregate_temporal_validity_accuracy(
                    outputs,
                    samples,
                )
            ),
            "invalid_citation_rate": (
                aggregate_invalid_citation_rate(
                    outputs,
                    samples,
                )
            ),
            "answer_faithfulness": (
                aggregate_answer_faithfulness(outputs)
            ),
            "lineage_traceability": (
                aggregate_lineage_traceability(outputs)
            ),
            "average_latency_seconds": average_latency(outputs),
        },
    }

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_json(
    data: Dict[str, Any],
    output_path: str | Path,
) -> None:
    """Save evaluation results as JSON."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

def save_outputs(
    outputs: List[SystemOutput],
    output_path: str | Path,
) -> None:
    """Save raw system outputs as JSON."""

    payload = {
        "outputs": [asdict(output) for output in outputs]
    }

    save_json(payload, output_path)
