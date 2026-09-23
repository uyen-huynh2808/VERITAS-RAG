from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List


# ============================================================================
# I/O
# ============================================================================


def load_json(
    path: str | Path,
) -> Any:

    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(
            f"Input file not found: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        return json.load(file)


def save_json(
    data: Dict[str, Any],
    path: str | Path,
) -> None:

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


# ============================================================================
# Contract metrics
# ============================================================================


def calculate_contract_metrics(
    results: List[Dict[str, Any]],
) -> Dict[str, Any]:

    mutated = [
        item
        for item in results
        if item.get("mutation_type") != "clean"
    ]

    if not mutated:
        return {
            "contract_detection_rate": None,
            "sample_count": 0,
            "by_mutation_type": {},
        }

    detected = 0

    by_type: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {
            "total": 0,
            "detected": 0,
        }
    )

    for item in mutated:

        mutation_type = item.get(
            "mutation_type",
            "unknown",
        )

        expected = item.get(
            "expected"
        )

        actual = item.get(
            "actual"
        )

        by_type[mutation_type]["total"] += 1

        correctly_detected = (
            expected == "QUARANTINE"
            and actual == "QUARANTINE"
        )

        if correctly_detected:
            detected += 1
            by_type[mutation_type][
                "detected"
            ] += 1

    overall_rate = (
        detected / len(mutated)
    )

    breakdown: Dict[str, Any] = {}

    for mutation_type, stats in by_type.items():

        total = stats["total"]

        breakdown[mutation_type] = {
            "total": total,
            "detected": stats["detected"],
            "detection_rate": (
                stats["detected"] / total
                if total
                else 0.0
            ),
        }

    return {
        "contract_detection_rate": overall_rate,
        "sample_count": len(mutated),
        "by_mutation_type": breakdown,
    }


# ============================================================================
# Optional confusion statistics
# ============================================================================


def calculate_contract_confusion(
    results: List[Dict[str, Any]],
) -> Dict[str, int]:

    tp = 0
    tn = 0
    fp = 0
    fn = 0

    for item in results:

        expected = item.get("expected")
        actual = item.get("actual")

        if expected == "QUARANTINE":
            if actual == "QUARANTINE":
                tp += 1
            else:
                fn += 1

        elif expected == "PASS":
            if actual == "PASS":
                tn += 1
            else:
                fp += 1

    return {
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
    }


# ============================================================================
# Main experiment
# ============================================================================


def run_contract_eval(
    input_path: str | Path,
) -> Dict[str, Any]:

    data = load_json(
        input_path
    )

    if isinstance(data, dict):
        results = data.get(
            "results",
            [],
        )
    elif isinstance(data, list):
        results = data
    else:
        raise ValueError(
            "Mutation result JSON must be a list "
            "or an object containing 'results'."
        )

    metrics = calculate_contract_metrics(
        results
    )

    confusion = calculate_contract_confusion(
        results
    )

    return {
        **metrics,
        "confusion_matrix": confusion,
    }


# ============================================================================
# CLI
# ============================================================================


def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Evaluate VERITAS Data Contract "
            "using mutation testing."
        )
    )

    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Mutation test result JSON.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "results/contract/"
            "contract_metrics.json"
        ),
        help="Output metric JSON.",
    )

    args = parser.parse_args()

    metrics = run_contract_eval(
        args.input
    )

    save_json(
        metrics,
        args.output,
    )

    print(
        "\n=== VERITAS Contract Evaluation ==="
    )

    rate = metrics[
        "contract_detection_rate"
    ]

    if rate is None:
        print(
            "Contract Detection Rate: N/A"
        )
    else:
        print(
            "Contract Detection Rate: "
            f"{rate:.4f}"
        )

    print(
        f"Mutation samples: "
        f"{metrics['sample_count']}"
    )

    print("\nMutation breakdown:")

    for mutation_type, stats in (
        metrics[
            "by_mutation_type"
        ].items()
    ):
        print(
            f"  {mutation_type}: "
            f"{stats['detection_rate']:.4f}"
        )

    print("\nConfusion matrix:")

    for key, value in (
        metrics[
            "confusion_matrix"
        ].items()
    ):
        print(
            f"  {key}: {value}"
        )


if __name__ == "__main__":
    main()