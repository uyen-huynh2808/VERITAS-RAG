from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_results(input_path: str | Path) -> List[Dict[str, Any]]:
    """Load mutation/contract results from JSON."""

    input_path = Path(input_path)

    with input_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, list):
        return data

    if isinstance(data, dict) and "results" in data:
        return data["results"]

    raise ValueError(
        "Input JSON must be a list or an object containing 'results'."
    )

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def contract_detection_rate(
    results: List[Dict[str, Any]],
) -> float | None:
    """
    Detection rate over mutated documents only.

    A mutation is detected when:
        expected == QUARANTINE
        actual   == QUARANTINE
    """

    mutated = [
        result
        for result in results
        if str(result.get("mutation_type", "")).lower() != "clean"
        and str(result.get("expected", "")).upper() == "QUARANTINE"
    ]

    if not mutated:
        return None

    detected = sum(
        1
        for result in mutated
        if str(result.get("actual", "")).upper() == "QUARANTINE"
    )

    return detected / len(mutated)

def per_mutation_detection_rate(
    results: List[Dict[str, Any]],
) -> Dict[str, float | None]:
    """Calculate detection rate for each mutation type."""

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for result in results:
        mutation_type = str(
            result.get("mutation_type", "unknown")
        )

        if mutation_type.lower() == "clean":
            continue

        grouped[mutation_type].append(result)

    metrics: Dict[str, float | None] = {}

    for mutation_type, mutation_results in grouped.items():
        expected_mutations = [
            result
            for result in mutation_results
            if str(result.get("expected", "")).upper()
            == "QUARANTINE"
        ]

        if not expected_mutations:
            metrics[mutation_type] = None
            continue

        detected = sum(
            1
            for result in expected_mutations
            if str(result.get("actual", "")).upper()
            == "QUARANTINE"
        )

        metrics[mutation_type] = (
            detected / len(expected_mutations)
        )

    return metrics

def confusion_matrix(
    results: List[Dict[str, Any]],
) -> Dict[str, int]:
    """
    Calculate PASS/QUARANTINE confusion matrix.

    Positive = document should be quarantined.
    """

    tp = 0
    tn = 0
    fp = 0
    fn = 0

    for result in results:
        expected = str(
            result.get("expected", "")
        ).upper()

        actual = str(
            result.get("actual", "")
        ).upper()

        if expected == "QUARANTINE" and actual == "QUARANTINE":
            tp += 1
        elif expected == "PASS" and actual == "PASS":
            tn += 1
        elif expected == "PASS" and actual == "QUARANTINE":
            fp += 1
        elif expected == "QUARANTINE" and actual == "PASS":
            fn += 1

    return {
        "TP": tp,
        "TN": tn,
        "FP": fp,
        "FN": fn,
    }

# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate_contracts(
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Evaluate contract detection performance."""

    return {
        "num_results": len(results),
        "metrics": {
            "contract_detection_rate": (
                contract_detection_rate(results)
            ),
            "per_mutation_detection_rate": (
                per_mutation_detection_rate(results)
            ),
            "confusion_matrix": confusion_matrix(results),
        },
    }

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_json(
    data: Dict[str, Any],
    output_path: str | Path,
) -> None:
    """Save contract evaluation results."""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate VERITAS-RAG contract/mutation results."
    )

    parser.add_argument(
        "--input",
        required=True,
        help="Path to mutation result JSON.",
    )

    parser.add_argument(
        "--output",
        default="results/contract/contract_metrics.json",
        help="Output JSON path.",
    )

    args = parser.parse_args()

    results = load_results(args.input)
    metrics = evaluate_contracts(results)

    save_json(
        metrics,
        args.output,
    )

    print(
        json.dumps(
            metrics,
            ensure_ascii=False,
            indent=2,
        )
    )

if __name__ == "__main__":
    main()