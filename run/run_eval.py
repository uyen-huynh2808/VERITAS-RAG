from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional, Set


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_K = 5


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkSample:
    id: str
    query: str
    t_event: str
    difficulty: str
    category: str
    gold_doc_ids: List[str]
    gold_article_ids: List[str]
    gold_chunk_ids: List[str]
    gold_answer: str
    gold_citation_doc_ids: List[str]


@dataclass
class RetrievedChunk:
    chunk_id: str
    article_id: str
    logical_doc_id: str
    text: str
    global_page: int
    score: float
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None


@dataclass
class SystemOutput:
    question: str
    answer: str
    retrieved_chunks: List[RetrievedChunk]
    latency_ms: float


@dataclass
class EvaluationResult:
    sample_id: str
    model_name: str
    recall_at_k: float
    temporal_validity: float
    invalid_citation_rate: float
    faithfulness: float
    latency_ms: float


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------

def load_dataset(path: str | Path) -> List[BenchmarkSample]:
    path = Path(path)

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return [BenchmarkSample(**sample) for sample in data["samples"]]


# ---------------------------------------------------------------------------
# Recall@K
# ---------------------------------------------------------------------------

def retrieval_recall_at_k(
    sample: BenchmarkSample,
    output: SystemOutput,
    k: int = DEFAULT_K,
) -> float:
    """
    Retrieval Recall@K at chunk level.

    Only the first K retrieved chunks are evaluated.
    The benchmark gold set is gold_chunk_ids.

    Formula:
        |gold_chunks ∩ retrieved_top_k| / |gold_chunks|
    """
    if k <= 0:
        raise ValueError("k must be > 0")

    gold: Set[str] = set(sample.gold_chunk_ids)

    if not gold:
        return 0.0

    retrieved_top_k = output.retrieved_chunks[:k]
    retrieved: Set[str] = {
        chunk.chunk_id
        for chunk in retrieved_top_k
    }

    return len(gold & retrieved) / len(gold)


# ---------------------------------------------------------------------------
# Date / temporal validity
# ---------------------------------------------------------------------------

def _parse_date(value: Optional[str]) -> Optional[date]:
    """
    Parse ISO date strings (YYYY-MM-DD).

    NULL / empty values are accepted as missing values.
    In the current Gold dataset, effective_to is NULL because no upper
    validity bound is encoded.

    A non-empty, non-NULL value with an invalid format raises ValueError
    instead of being silently treated as missing.
    """
    if value is None:
        return None

    value = str(value).strip()

    if value == "" or value.lower() in {"null", "none"}:
        return None

    return date.fromisoformat(value)


def temporal_validity_accuracy(
    sample: BenchmarkSample,
    output: SystemOutput,
    k: int = DEFAULT_K,
) -> float:
    """
    Fraction of the top-K retrieved chunks whose temporal interval contains
    t_event.

    Current Gold convention:
        effective_from = required lower bound
        effective_to   = NULL means no encoded upper bound

    Therefore:
        valid iff
            effective_from <= t_event
        AND
            (effective_to IS NULL OR t_event <= effective_to)

    Note:
    This metric evaluates the temporal metadata stored in Gold. It does not
    independently infer that a document became invalid because another legal
    document later amended/replaced it when that relation is not encoded in
    effective_to.
    """
    if k <= 0:
        raise ValueError("k must be > 0")

    retrieved_top_k = output.retrieved_chunks[:k]

    if not retrieved_top_k:
        return 0.0

    t_event = _parse_date(sample.t_event)

    if t_event is None:
        return 0.0

    valid = 0

    for chunk in retrieved_top_k:
        start = _parse_date(chunk.effective_from)
        end = _parse_date(chunk.effective_to)

        if start is None:
            continue

        if start <= t_event and (end is None or t_event < end):
            valid += 1

    return valid / len(retrieved_top_k)


# ---------------------------------------------------------------------------
# Citation extraction
# ---------------------------------------------------------------------------

# Vietnamese legal-document identifiers such as:
#   238/2026/NĐ-CP
#   01/2021/TT-BKHĐT
#   12/2020/QĐ-TTg
#   15/2020/QH14
#   05/2024/NQ-CP
DOC_PATTERN = re.compile(
    r"\b\d+/\d{4}/[A-ZĐ0-9]+(?:-[A-ZĐ0-9]+)*\b",
    flags=re.IGNORECASE,
)


def _normalize_unicode(text: str) -> str:
    """Normalize Vietnamese text to NFC."""
    return unicodedata.normalize("NFC", text or "")


def extract_cited_documents(text: str) -> Set[str]:
    """
    Extract Vietnamese legal-document IDs from an answer.

    Matching is case-insensitive; extracted IDs are normalized to uppercase
    so comparison with gold annotations is stable.
    """
    text = _normalize_unicode(text)
    matches = DOC_PATTERN.findall(text)

    return {match.upper() for match in matches}


# ---------------------------------------------------------------------------
# Invalid citation rate
# ---------------------------------------------------------------------------

def invalid_citation_rate(
    sample: BenchmarkSample,
    output: SystemOutput,
) -> float:
    """
    Invalid Citation Rate:

        number of predicted citations not in gold
        -----------------------------------------
              number of predicted citations

    A response with no explicit legal-document citation receives 0.0 here.
    This means "no invalid citation was detected"; it does NOT mean that the
    answer has good citation coverage.
    """
    predicted = extract_cited_documents(output.answer)

    if not predicted:
        return 0.0

    gold = {
        _normalize_unicode(doc_id).upper()
        for doc_id in sample.gold_citation_doc_ids
    }

    invalid = predicted - gold

    return len(invalid) / len(predicted)


# ---------------------------------------------------------------------------
# Lexical faithfulness
# ---------------------------------------------------------------------------

def normalize(text: str) -> Set[str]:
    """
    Lightweight lexical normalization used for the seminar faithfulness proxy.

    This is intentionally a lexical overlap metric, not an NLI/HART
    faithfulness score.
    """
    text = _normalize_unicode(text).lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)

    # Keep single-character letters and all numeric tokens because legal
    # references such as "Điều 5", "Khoản 1", and "Điểm a" are meaningful.
    return {
        token
        for token in text.split()
        if len(token) > 1 or token.isdigit() or token.isalpha()
    }


def answer_faithfulness(
    sample: BenchmarkSample,
    output: SystemOutput,
    k: int = DEFAULT_K,
) -> float:
    """
    Lexical evidence-overlap proxy over the same top-K evidence used by the
    other retrieval metrics:

        |answer_tokens ∩ evidence_tokens|
        ---------------------------------
              |answer_tokens|

    Important:
    This is NOT semantic faithfulness and cannot detect contradiction.
    """
    if k <= 0:
        raise ValueError("k must be > 0")

    retrieved_top_k = output.retrieved_chunks[:k]

    evidence = " ".join(
        chunk.text
        for chunk in retrieved_top_k
    )

    answer_tokens = normalize(output.answer)
    evidence_tokens = normalize(evidence)

    if not answer_tokens:
        return 0.0

    supported = answer_tokens & evidence_tokens

    return len(supported) / len(answer_tokens)


# ---------------------------------------------------------------------------
# System evaluation
# ---------------------------------------------------------------------------

def evaluate_system(
    sample: BenchmarkSample,
    output: SystemOutput,
    model_name: str,
    k: int = DEFAULT_K,
) -> EvaluationResult:
    return EvaluationResult(
        sample_id=sample.id,
        model_name=model_name,
        recall_at_k=retrieval_recall_at_k(sample, output, k=k),
        temporal_validity=temporal_validity_accuracy(sample, output, k=k),
        invalid_citation_rate=invalid_citation_rate(sample, output),
        faithfulness=answer_faithfulness(sample, output, k=k),
        latency_ms=output.latency_ms,
    )


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def save_json(
    results: List[EvaluationResult],
    path: str | Path,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            [asdict(result) for result in results],
            f,
            ensure_ascii=False,
            indent=2,
        )
