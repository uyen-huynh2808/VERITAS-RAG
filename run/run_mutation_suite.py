"""
Generate the Synthetic Mutation Suite and replay it through the contract.

Typical use (after `python -m run.prepare_gold_dataset`):

    python -m run.run_mutation_suite
    python -m run.run_contract_eval --input results/contract/mutation_results.json

Smoke test without a materialized lakehouse (fictitious document, results
must not be reported):

    python -m run.run_mutation_suite --synthetic --output results/contract/mutation_results_synthetic.json
"""

from __future__ import annotations

import argparse
import json
import platform
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.contracts.document_contract import ExecutableDocumentContract
from src.core.config import load_system_config
from src.core.environment import setup_environment
from src.data.mutation_suite import (
    MutationSuite,
    build_synthetic_bundle,
    load_bundles_from_lakehouse,
    run_cases,
)


def default_output_path(config, synthetic: bool) -> Path:
    base = Path(config.benchmark.contract_metrics_path).parent
    name = (
        "mutation_results_synthetic.json"
        if synthetic
        else "mutation_results.json"
    )
    return base / name


def build_cases(
    config,
    synthetic: bool,
    doc_ids: Optional[List[str]],
    mutation_types: Optional[List[str]],
    seed: int,
    work_dir: Path,
):
    if synthetic:
        bundles = [build_synthetic_bundle(work_dir / "synthetic")]
    else:
        bundles = load_bundles_from_lakehouse(config, doc_ids=doc_ids)
        if not bundles:
            raise RuntimeError("No Gold documents found in the lakehouse.")

    suite = MutationSuite(
        seed=seed,
        work_dir=work_dir / "tampered",
        mutation_types=mutation_types,
    )
    return bundles, suite.generate(bundles)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the VERITAS-RAG synthetic mutation suite."
    )
    parser.add_argument("--output", default=None, help="Result JSON path.")
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Use a fictitious built-in document (smoke test only).",
    )
    parser.add_argument(
        "--doc-ids", nargs="*", default=None,
        help="Restrict to these logical_doc_id values.",
    )
    parser.add_argument(
        "--types", nargs="*", default=None,
        help="Mutation types to run (default: all, plus clean controls).",
    )
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    config = load_system_config()
    seed = setup_environment(config, seed=args.seed)

    with tempfile.TemporaryDirectory(prefix="veritas_mutation_") as tmp:
        work_dir = Path(tmp)
        bundles, cases = build_cases(
            config,
            synthetic=args.synthetic,
            doc_ids=args.doc_ids,
            mutation_types=args.types,
            seed=seed,
            work_dir=work_dir,
        )
        contract = ExecutableDocumentContract(
            config,
            write_quarantine_reports=False,
        )
        results = run_cases(contract, cases)

    output = Path(args.output) if args.output else default_output_path(
        config, args.synthetic
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "seed": seed,
            "source": "synthetic" if args.synthetic else "lakehouse",
            "documents": [bundle.doc_id for bundle in bundles],
            "num_cases": len(results),
            "rules_path": config.contracts.rules_path,
            "python": platform.python_version(),
            "note": (
                "Synthetic smoke-test document - do not report."
                if args.synthetic
                else "Mutations applied to deep copies of materialized "
                "Gold documents; sources were not modified."
            ),
        },
        "results": results,
    }
    with output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    by_type = Counter(result["mutation_type"] for result in results)
    print(f"Documents : {len(bundles)}")
    print(f"Cases     : {len(results)}  {dict(by_type)}")
    misses = [
        result for result in results
        if result["expected"] != result["actual"] and result["in_scope"]
    ]
    print(f"In-scope mismatches: {len(misses)}")
    for result in misses[:15]:
        print(
            f"  - {result['mutation_type']}/{result['variant']}"
            f"/{result['severity']}: expected {result['expected']}, "
            f"got {result['actual']} {result['issue_codes']}"
        )
    print(f"Saved to: {output}")


if __name__ == "__main__":
    main()
