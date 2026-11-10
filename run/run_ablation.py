"""
Contract ablation over the Synthetic Mutation Suite.

The same mutation cases (same seed, same clean documents) are replayed
through the full four-layer contract and through each configuration in
``config/ablations/*.yaml`` (one layer disabled at a time). The drop in
detection rate relative to the full contract quantifies what each layer
contributes, and shows which defects are caught redundantly by more than
one layer.

    python -m run.run_ablation                 # lakehouse documents
    python -m run.run_ablation --synthetic     # smoke test only
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

from src.contracts.document_contract import ExecutableDocumentContract
from src.core.config import load_system_config
from src.core.environment import setup_environment
from src.data.mutation_suite import run_cases

from .run_contract_eval import evaluate_contracts
from .run_mutation_suite import build_cases


def load_ablation_configs(directory: str | Path) -> List[Dict[str, Any]]:
    configs: List[Dict[str, Any]] = [
        {
            "name": "full",
            "description": "Đầy đủ bốn lớp contract.",
            "disabled_layers": [],
        }
    ]
    for path in sorted(Path(directory).glob("*.yaml")):
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not data.get("disabled_layers"):
            raise ValueError(f"{path} must define disabled_layers.")
        configs.append(
            {
                "name": data.get("name", path.stem),
                "description": data.get("description", ""),
                "disabled_layers": list(data["disabled_layers"]),
                "path": str(path),
            }
        )
    return configs


def _summary_row(name: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    m = metrics["metrics"]
    return {
        "config": name,
        "detection_rate": m["contract_detection_rate"],
        "rule_hit_rate": m["rule_hit_rate"],
        "false_positive_rate": m["false_positive_rate"],
        "per_mutation": m["per_mutation_detection_rate"],
        "per_target_layer": {
            layer: values["rate"]
            for layer, values in m["per_target_layer_detection_rate"].items()
        },
    }


def _fmt(value: Any) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def print_table(rows: List[Dict[str, Any]]) -> None:
    mutation_types = sorted(
        {name for row in rows for name in row["per_mutation"]}
    )
    header = ["config", "detect", "Δ", "FP"] + mutation_types
    print(" | ".join(header))
    print(" | ".join("---" for _ in header))
    full = rows[0]["detection_rate"] or 0.0
    for row in rows:
        delta = (
            None if row["detection_rate"] is None
            else row["detection_rate"] - full
        )
        cells = [
            row["config"],
            _fmt(row["detection_rate"]),
            "—" if row["config"] == "full" else _fmt(delta),
            _fmt(row["false_positive_rate"]),
        ] + [_fmt(row["per_mutation"].get(name)) for name in mutation_types]
        print(" | ".join(cells))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run contract ablations.")
    parser.add_argument("--ablations-dir", default="config/ablations")
    parser.add_argument("--output", default=None)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--doc-ids", nargs="*", default=None)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    config = load_system_config()
    seed = setup_environment(config, seed=args.seed)
    ablations = load_ablation_configs(args.ablations_dir)

    report: Dict[str, Any] = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "source": "synthetic" if args.synthetic else "lakehouse",
        },
        "configs": {},
        "summary": [],
    }

    with tempfile.TemporaryDirectory(prefix="veritas_ablation_") as tmp:
        bundles, cases = build_cases(
            config,
            synthetic=args.synthetic,
            doc_ids=args.doc_ids,
            mutation_types=None,
            seed=seed,
            work_dir=Path(tmp),
        )
        report["metadata"]["documents"] = [bundle.doc_id for bundle in bundles]
        report["metadata"]["num_cases"] = len(cases)

        for ablation in ablations:
            contract = ExecutableDocumentContract(
                config,
                disabled_layers=ablation["disabled_layers"],
                write_quarantine_reports=False,
            )
            results = run_cases(contract, cases)
            metrics = evaluate_contracts(results)
            report["configs"][ablation["name"]] = {
                "description": ablation["description"],
                "disabled_layers": ablation["disabled_layers"],
                "metrics": metrics,
            }
            report["summary"].append(_summary_row(ablation["name"], metrics))

    full_rate = report["summary"][0]["detection_rate"]
    for row in report["summary"]:
        row["delta_vs_full"] = (
            None
            if row["detection_rate"] is None or full_rate is None
            else row["detection_rate"] - full_rate
        )

    output = Path(args.output) if args.output else (
        Path(config.benchmark.contract_metrics_path).parent
        / (
            "ablation_metrics_synthetic.json"
            if args.synthetic
            else "ablation_metrics.json"
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)

    print_table(report["summary"])
    print(f"\nSaved to: {output}")


if __name__ == "__main__":
    main()
