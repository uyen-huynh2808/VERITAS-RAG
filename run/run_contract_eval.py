"""
Contract evaluation for VERITAS-RAG (Contract Testing experiments).

Two modes
---------

1. PDF mode (default) - test the real ingestion path on legal PDFs that
   contain known, hand-made defects (e.g. ``Docs/Task1``)::

       python -m run.run_contract_eval
       python -m run.run_contract_eval --pdf-dir Docs/Task1 --parser pymupdf

   Every PDF goes through exactly what ``prepare_gold_dataset.py`` does
   before persistence - source hash, ``PDFParser``, metadata extraction,
   Silver provisions, Gold-ready chunks from ``ChunkBuilder`` - and is then
   validated by the four contract checkpoints::

       pre_parse  (Schema + document Provenance)
       bronze     (Schema + document Provenance)
       silver     (Temporal incl. cross-document relations + Quality incl.
                   provision-structure integrity)
       gold       (chunk Provenance + final integrity)

   Nothing is written to the lakehouse or to ``data/quarantine`` (unless
   ``--write-quarantine``). Each PDF is compared with the expectation in
   ``expected_results.json`` (same folder by default): expected outcome,
   mutation family, expected contract rule codes and the checkpoint that
   should block it.

   Three views are reported for each file:
   - ``stopped_at`` / ``actual``: pipeline semantics - the first failing
     checkpoint blocks promotion, so this is what the system really does;
   - ``checkpoint_codes``: rule codes from all four checkpoints, each run
     independently (attribution, as in the mutation suite);
   - ``diagnostic_codes``: when the schema gate fails, later layers are
     NOT_CHECKED; their rules are then probed directly on the parsed
     content so that Individual Rule Detection still shows whether the
     defect's own rule would have fired.

2. JSON mode - score an existing result file, e.g. the synthetic mutation
   suite::

       python -m run.run_contract_eval --input results/contract/mutation_results.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.config import load_system_config
from src.core.environment import setup_environment

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

def _in_scope(result: Dict[str, Any]) -> bool:
    """Blind-spot probes carry in_scope=False; older files lack the flag."""
    return bool(result.get("in_scope", True))


def _rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


def _is_quarantined(result: Dict[str, Any]) -> bool:
    return str(result.get("actual", "")).upper() == "QUARANTINE"


def _expected_mutations(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        result
        for result in results
        if str(result.get("mutation_type", "")).lower() != "clean"
        and str(result.get("expected", "")).upper() == "QUARANTINE"
        and _in_scope(result)
    ]


def individual_rule_detection_rate(
    results: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """
    Individual Rule Detection Rate.

    For every contract rule code listed in a case's ``expected_codes``, the
    share of in-scope cases targeting that rule in which the rule itself
    fired (not merely some other rule). A case targeting several
    alternative codes counts for each of them.
    """

    targeted: Dict[str, int] = defaultdict(int)
    fired: Dict[str, int] = defaultdict(int)
    for result in _expected_mutations(results):
        issued = set(result.get("issue_codes") or [])
        for code in result.get("expected_codes") or []:
            targeted[code] += 1
            if code in issued:
                fired[code] += 1
    return {
        code: {
            "targeted_cases": targeted[code],
            "fired": fired[code],
            "rate": _rate(fired[code], targeted[code]),
        }
        for code in sorted(targeted)
    }


def rule_hit_rate(results: List[Dict[str, Any]]) -> float | None:
    """Share of in-scope mutations caught by one of their intended rules."""
    scored = [
        result for result in _expected_mutations(results)
        if result.get("expected_codes")
    ]
    hits = sum(
        1 for result in scored
        if set(result.get("expected_codes") or [])
        & set(result.get("issue_codes") or [])
    )
    return _rate(hits, len(scored))


def grouped_detection_rate(
    results: List[Dict[str, Any]],
    key: str,
) -> Dict[str, Dict[str, Any]]:
    """Detection rate of in-scope mutations grouped by ``key``."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for result in _expected_mutations(results):
        grouped[str(result.get(key, "n/a"))].append(result)
    return {
        name: {
            "cases": len(items),
            "detected": sum(_is_quarantined(item) for item in items),
            "rate": _rate(sum(_is_quarantined(item) for item in items), len(items)),
        }
        for name, items in sorted(grouped.items())
    }


def variant_detection_rate(
    results: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for result in results:
        if str(result.get("mutation_type", "")).lower() == "clean":
            continue
        name = (
            f"{result.get('mutation_type')}/{result.get('variant', 'n/a')}"
            f"/{result.get('severity', 'n/a')}"
        )
        grouped[name].append(result)
    return {
        name: {
            "cases": len(items),
            "detected": sum(_is_quarantined(item) for item in items),
            "rate": _rate(sum(_is_quarantined(item) for item in items), len(items)),
            "in_scope": all(_in_scope(item) for item in items),
        }
        for name, items in sorted(grouped.items())
    }


def false_positive_rate(results: List[Dict[str, Any]]) -> float | None:
    """Share of clean controls (expected PASS) that were quarantined."""
    clean = [
        result for result in results
        if str(result.get("expected", "")).upper() == "PASS"
    ]
    return _rate(sum(_is_quarantined(result) for result in clean), len(clean))


def blind_spot_detection_rate(results: List[Dict[str, Any]]) -> float | None:
    """Detection rate on out-of-scope probes (expected to be low)."""
    probes = [
        result for result in results
        if not _in_scope(result)
        and str(result.get("expected", "")).upper() == "QUARANTINE"
    ]
    return _rate(sum(_is_quarantined(result) for result in probes), len(probes))


def checkpoint_stop_distribution(
    results: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Where the pipeline would have stopped each in-scope mutation."""
    counts: Dict[str, int] = defaultdict(int)
    for result in _expected_mutations(results):
        counts[str(result.get("stopped_at") or "not_stopped")] += 1
    return dict(sorted(counts.items()))


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
        and _in_scope(result)
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

        if mutation_type.lower() == "clean" or not _in_scope(result):
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
    Calculate PASS/QUARANTINE confusion matrix over clean controls and
    in-scope mutations.

    Positive = document should be quarantined.
    """

    tp = 0
    tn = 0
    fp = 0
    fn = 0

    for result in results:
        if not _in_scope(result):
            # Blind-spot probes are reported separately.
            continue

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

    in_scope = _expected_mutations(results)
    evaluation: Dict[str, Any] = {
        "num_results": len(results),
        "num_in_scope_mutations": len(in_scope),
        "num_clean_controls": sum(
            1 for result in results
            if str(result.get("expected", "")).upper() == "PASS"
        ),
        "metrics": {
            "contract_detection_rate": (
                contract_detection_rate(results)
            ),
            "per_mutation_detection_rate": (
                per_mutation_detection_rate(results)
            ),
            "rule_hit_rate": rule_hit_rate(results),
            "individual_rule_detection_rate": (
                individual_rule_detection_rate(results)
            ),
            "per_target_layer_detection_rate": (
                grouped_detection_rate(results, "target_layer")
            ),
            "per_severity_detection_rate": (
                grouped_detection_rate(results, "severity")
            ),
            "per_variant_detection_rate": variant_detection_rate(results),
            "false_positive_rate": false_positive_rate(results),
            "blind_spot_detection_rate": blind_spot_detection_rate(results),
            "checkpoint_stop_distribution": (
                checkpoint_stop_distribution(results)
            ),
            "mean_contract_time_ms": (
                sum(float(result.get("contract_time_ms", 0.0)) for result in results)
                / len(results)
                if results and any("contract_time_ms" in r for r in results)
                else None
            ),
            "confusion_matrix": confusion_matrix(results),
        },
    }

    # PDF-mode extras: whether the defect's rule fired inside the pipeline
    # run itself (not only in a diagnostic probe), and whether the expected
    # checkpoint was the one that blocked the document.
    scored = [r for r in in_scope if r.get("rule_hit_in_pipeline") is not None]
    if scored:
        evaluation["metrics"]["rule_hit_in_pipeline_rate"] = _rate(
            sum(bool(r["rule_hit_in_pipeline"]) for r in scored), len(scored)
        )
    staged = [
        r for r in results
        if r.get("stage_match") is not None and _in_scope(r)
    ]
    if staged:
        evaluation["metrics"]["stage_match_rate"] = _rate(
            sum(bool(r["stage_match"]) for r in staged), len(staged)
        )

    return evaluation

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
# PDF mode: run the real ingestion path on defect PDFs
# ---------------------------------------------------------------------------

CHECKPOINTS = ("pre_parse", "bronze", "silver", "gold")

DEFAULT_PDF_DIR = "Docs/Task1"
MANIFEST_NAME = "expected_results.json"


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def _file_key(name: str) -> str:
    """Case/Unicode-insensitive file key (Windows may store NFD names)."""
    return _nfc(Path(name).name).casefold()


def load_manifest(path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    """
    Load ``expected_results.json``.

    Format::

        {
          "dataset": "...",
          "cases": [
            {
              "file": "9992026NĐ-CP.pdf",
              "mutation_type": "wrong_date",
              "variant": "effective_before_issued",
              "target_layer": "temporal",
              "expected": "QUARANTINE",          # or "PASS" for clean files
              "expected_stage": "silver",
              "expected_codes": ["INVALID_START_DATE"],
              "description": "..."
            }
          ]
        }
    """

    if path is None or not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    cases = data.get("cases", data) if isinstance(data, dict) else data
    manifest: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict) or not case.get("file"):
            raise ValueError(f"Invalid manifest entry in {path}: {case!r}")
        manifest[_file_key(case["file"])] = case
    return manifest


def _issue_codes(report: Dict[str, Any]) -> List[str]:
    return [issue["code"] for issue in report.get("issues", [])]


def _issue_layers(report: Dict[str, Any]) -> List[str]:
    return [issue["layer"] for issue in report.get("issues", [])]


def _parser_provisions_to_silver(
    provisions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Give parser provisions the Silver column names used by ChunkBuilder."""
    rows = []
    for provision in provisions:
        row = dict(provision)
        row.setdefault("provision_number", provision.get("number"))
        row.setdefault("provision_path", provision.get("path"))
        rows.append(row)
    return rows


class PDFContractEvaluator:
    """Replay the pre-persistence part of ``prepare_gold_dataset``."""

    def __init__(
        self,
        config,
        parser_mode: str = "config",
        write_quarantine: bool = False,
        disabled_layers: Optional[List[str]] = None,
    ):
        from src.contracts.document_contract import ExecutableDocumentContract
        from src.ingestion.pdf_parser import PDFParser
        from src.ingestion.relation_extractor import RelationExtractor
        from src.rag.chunk_builder import ChunkBuilder

        self.config = config
        self.contract = ExecutableDocumentContract(
            config,
            disabled_layers=disabled_layers,
            write_quarantine_reports=write_quarantine,
        )
        self.rules = self.contract.rules
        self.hash_algorithm = self.rules.provenance_contract.hash_algorithm

        prefer_docling = {
            "config": config.parser.prefer_docling,
            "docling": True,
            "pymupdf": False,
        }[parser_mode]
        self.parser = PDFParser(
            prefer_docling=prefer_docling,
            hash_algorithm=self.hash_algorithm,
            header_ocr_engine=config.parser.header_ocr_engine,
            config=config,
        )
        if parser_mode == "docling" and not self.parser.prefer_docling:
            raise RuntimeError(
                "--parser docling was requested but Docling is not available."
            )
        self.relation_extractor = RelationExtractor()
        self._parse_cache: Dict[str, Any] = {}
        # build_chunks() is persistence-free; no lakehouse is needed.
        self.chunk_builder = ChunkBuilder(
            lakehouse=None,
            chunk_size=config.chunking.max_tokens,
            chunk_overlap=config.chunking.overlap,
            hash_algorithm=self.hash_algorithm,
        )

    # -- helpers ------------------------------------------------------------

    def _file_hash(self, path: Path) -> str:
        digest = hashlib.new(self.hash_algorithm)
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    def _diagnostic_codes(
        self,
        metadata: Dict[str, Any],
        content: str,
        tables: List[Dict[str, Any]],
        provisions: List[Dict[str, Any]],
        related_documents: List[Dict[str, Any]],
        relations: List[Dict[str, Any]],
        silver_report: Dict[str, Any],
    ) -> List[str]:
        """
        Probe Temporal/Quality rules directly when the schema gate left
        them NOT_CHECKED, so the defect's own rule can still be observed.
        """

        layer_status = silver_report.get("layer_status", {})
        codes: List[str] = []

        if layer_status.get("quality") == "NOT_CHECKED":
            _, quality_issues = self.contract._validate_quality(
                content, tables=tables
            )
            quality_issues.extend(
                self.contract._validate_structure(provisions, content)
            )
            codes.extend(issue.code for issue in quality_issues)

        if layer_status.get("temporal") == "NOT_CHECKED":
            from src.contracts.models import DocumentMetadata

            try:
                lenient = DocumentMetadata.model_validate(metadata)
            except Exception:
                lenient = None
            if lenient is not None:
                temporal_issues = self.contract._validate_temporal(lenient)
                temporal_issues.extend(
                    self.contract._validate_cross_document(
                        lenient,
                        related_documents=related_documents,
                        relations=relations,
                    )
                )
                codes.extend(issue.code for issue in temporal_issues)

        return sorted(set(codes))

    # -- main ---------------------------------------------------------------

    def _parse(self, pdf_path: Path):
        """Parse once per file; both passes reuse the result."""
        key = str(pdf_path.resolve())
        if key not in self._parse_cache:
            source_hash = self._file_hash(pdf_path)
            try:
                parsed = self.parser.parse(
                    parts_paths=[str(pdf_path)],
                    logical_doc_id=None,
                )
                self._parse_cache[key] = (source_hash, parsed, None)
            except Exception as exc:  # the pipeline would reject the file
                self._parse_cache[key] = (source_hash, None, exc)
        return self._parse_cache[key]

    def collect(self, pdf_path: Path) -> Optional[Dict[str, Any]]:
        """
        Pass 1: what this PDF contributes to the corpus catalog - its own
        dates (as the parser extracted them) and the relations it states.
        """
        _, parsed, error = self._parse(pdf_path)
        if error is not None or parsed is None or not parsed.articles:
            return None
        metadata = parsed.metadata
        doc_id = str(metadata.get("doc_id") or parsed.logical_doc_id)
        relations, _ = self.relation_extractor.extract(parsed.full_markdown, doc_id)
        return {
            "doc_id": doc_id,
            "file": _nfc(pdf_path.name),
            "issued_date": metadata.get("issued_date"),
            "effective_from": metadata.get("effective_from"),
            "effective_to": metadata.get("effective_to"),
            "metadata_source": "corpus",
            "relations": relations,
        }

    def evaluate(
        self,
        pdf_path: Path,
        expectation: Optional[Dict[str, Any]] = None,
        corpus: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Pass 2: validate one PDF. ``corpus`` (from ``collect``) lets the
        cross-document rules see the other PDFs in the folder: their real
        dates replace citation-only stubs, and relations stated by OTHER
        documents about this one (e.g. "Nghị định này thay thế Nghị định
        <this>") are applied to it.
        """
        expectation = expectation or {}
        started = time.perf_counter()

        result: Dict[str, Any] = {
            "case_id": _nfc(pdf_path.name),
            "file": _nfc(pdf_path.name),
            "doc_id": None,
            "mutation_type": expectation.get("mutation_type", "unlabelled"),
            "variant": expectation.get("variant", "n/a"),
            "severity": expectation.get("severity", "n/a"),
            "target_layer": expectation.get("target_layer", "n/a"),
            "in_scope": expectation.get("in_scope", True),
            "expected": str(expectation.get("expected", "")).upper() or None,
            "expected_stage": expectation.get("expected_stage"),
            "expected_codes": list(expectation.get("expected_codes", [])),
            "description": expectation.get("description", ""),
            "bundle_source": "pdf",
        }

        # 0-2. Source hash, parse + metadata extraction (cached) -----------
        source_hash, parsed, error = self._parse(pdf_path)
        if error is not None:
            return self._rejected(result, started, "PARSER_FAILED", str(error))

        result["doc_id"] = parsed.logical_doc_id
        result["parser_used"] = parsed.parser_used
        if not parsed.pages or not parsed.articles:
            return self._rejected(
                result, started, "PARSER_NO_ARTICLES",
                "Parser produced no pages or no legal articles.",
            )

        metadata = dict(parsed.metadata)
        metadata["storage_path"] = str(pdf_path)
        metadata["doc_version_hash"] = source_hash
        metadata.pop("effective_date_rules", None)

        content = parsed.full_markdown
        tables = [
            table for page in parsed.pages for table in (page.tables or [])
        ]
        provisions = parsed.provisions
        own_doc_id = str(metadata.get("doc_id") or parsed.logical_doc_id)
        relations, related_documents = self.relation_extractor.extract(
            content, own_doc_id
        )

        # Corpus view: other PDFs' real dates override citation stubs, and
        # relations other documents state about this one are added.
        if corpus:
            catalog = {
                item["doc_id"]: item
                for item in related_documents
            }
            for doc_id, entry in corpus["documents"].items():
                if doc_id != own_doc_id:
                    catalog[doc_id] = {
                        key: entry.get(key)
                        for key in (
                            "doc_id", "issued_date", "effective_from",
                            "effective_to", "metadata_source", "file",
                        )
                    }
            related_documents = list(catalog.values())
            seen = {
                (r["source_doc_id"], r["target_doc_id"], r["relation_type"])
                for r in relations
            }
            for relation in corpus["relations"]:
                key = (
                    relation["source_doc_id"],
                    relation["target_doc_id"],
                    relation["relation_type"],
                )
                if relation["target_doc_id"] == own_doc_id and key not in seen:
                    relations.append(dict(relation))
                    seen.add(key)

        # Gold-ready chunks exactly as ChunkBuilder would build them.
        try:
            gold_ready = self.chunk_builder.build_chunks(
                provisions=_parser_provisions_to_silver(provisions),
                logical_doc_id=parsed.logical_doc_id,
                doc_version_hash=source_hash,
            )
        except Exception as exc:
            gold_ready = []
            result["chunk_build_error"] = str(exc)
        contract_chunks = [
            {
                "chunk_id": chunk["chunk_id"],
                "article_id": chunk["article_id"],
                "provision_id": chunk["provision_id"],
                "provision_path": chunk["provision_path"],
                "text": chunk["text_content"],
                "doc_id": chunk["logical_doc_id"],
                "page_number": chunk["global_page"],
                "doc_version_hash": chunk["doc_version_hash"],
                "chunk_hash": chunk["chunk_hash"],
            }
            for chunk in gold_ready
        ]

        # 3-12. Contract checkpoints -----------------------------------------
        contract_started = time.perf_counter()
        reports = {
            "pre_parse": self.contract.validate_pre_parse(dict(metadata)),
            "bronze": self.contract.validate_bronze(dict(metadata)),
            "silver": self.contract.validate_silver(
                dict(metadata),
                content=content,
                tables=tables,
                provisions=provisions,
                related_documents=related_documents or None,
                relations=relations or None,
            ),
            "gold": self.contract.validate_gold(dict(metadata), contract_chunks),
        }
        contract_ms = (time.perf_counter() - contract_started) * 1000

        stopped_at = next(
            (name for name in CHECKPOINTS if reports[name]["status"] != "PASSED"),
            None,
        )
        checkpoint_codes = sorted(
            {code for name in CHECKPOINTS for code in _issue_codes(reports[name])}
        )
        diagnostic_codes = self._diagnostic_codes(
            metadata, content, tables, provisions,
            related_documents, relations, reports["silver"],
        )
        all_codes = sorted(set(checkpoint_codes) | set(diagnostic_codes))
        expected_codes = result["expected_codes"]

        quality = reports["silver"].get("quality_metrics")
        if quality is None:
            from src.contracts.quality_metrics import QualityMetricsCalculator

            quality = QualityMetricsCalculator.calculate(content, tables=tables)

        result.update(
            {
                "actual": "PASS" if stopped_at is None else "QUARANTINE",
                "stopped_at": stopped_at,
                "stage_match": (
                    None
                    if not result["expected_stage"]
                    else stopped_at == result["expected_stage"]
                ),
                "issue_codes": all_codes,
                "checkpoint_codes": checkpoint_codes,
                "diagnostic_codes": diagnostic_codes,
                "rule_hit": (
                    any(code in all_codes for code in expected_codes)
                    if expected_codes else None
                ),
                "rule_hit_in_pipeline": (
                    any(code in checkpoint_codes for code in expected_codes)
                    if expected_codes else None
                ),
                "detected_layers": sorted(
                    {
                        layer
                        for name in CHECKPOINTS
                        for layer in _issue_layers(reports[name])
                    }
                ),
                "checkpoint_status": {
                    name: reports[name]["status"] for name in CHECKPOINTS
                },
                "layer_status": {
                    name: reports[name]["layer_status"] for name in CHECKPOINTS
                },
                "extracted_metadata": {
                    key: metadata.get(key)
                    for key in (
                        "doc_id", "title", "doc_type", "issued_date",
                        "effective_from", "effective_to",
                    )
                },
                "relations": relations,
                "related_documents": related_documents,
                "num_articles": len(parsed.articles),
                "num_provisions": len(provisions),
                "num_tables": len(tables),
                "num_gold_chunks": len(contract_chunks),
                "quality_metrics": quality,
                "issues": {
                    name: reports[name]["issues"] for name in CHECKPOINTS
                },
                "contract_time_ms": round(contract_ms, 3),
                "total_time_ms": round(
                    (time.perf_counter() - started) * 1000, 3
                ),
            }
        )
        return result

    @staticmethod
    def _rejected(
        result: Dict[str, Any],
        started: float,
        code: str,
        message: str,
    ) -> Dict[str, Any]:
        result.update(
            {
                "actual": "QUARANTINE",
                "stopped_at": "parser",
                "stage_match": (
                    None if not result["expected_stage"]
                    else result["expected_stage"] == "parser"
                ),
                "issue_codes": [code],
                "checkpoint_codes": [],
                "diagnostic_codes": [code],
                "rule_hit": code in result["expected_codes"]
                if result["expected_codes"] else None,
                "rule_hit_in_pipeline": False,
                "detected_layers": ["parser"],
                "error": message,
                "total_time_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return result


def run_pdf_suite(
    pdf_dir: Path,
    manifest_path: Optional[Path],
    parser_mode: str,
    write_quarantine: bool,
    config,
    disabled_layers: Optional[List[str]] = None,
    use_corpus: bool = True,
) -> Dict[str, Any]:
    pdf_paths = sorted(
        path for path in pdf_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )
    if not pdf_paths:
        raise FileNotFoundError(f"No PDF files found in {pdf_dir}")

    manifest = load_manifest(manifest_path)
    unmatched = set(manifest)

    evaluator = PDFContractEvaluator(
        config,
        parser_mode=parser_mode,
        write_quarantine=write_quarantine,
        disabled_layers=disabled_layers,
    )

    # Pass 1: parse every PDF once and build the corpus catalog.
    corpus: Optional[Dict[str, Any]] = None
    if use_corpus:
        corpus = {"documents": {}, "relations": []}
        for pdf_path in pdf_paths:
            print(f"[contract-eval] parse {_nfc(pdf_path.name)}")
            entry = evaluator.collect(pdf_path)
            if entry is None:
                continue
            corpus["relations"].extend(entry.pop("relations"))
            corpus["documents"][entry["doc_id"]] = entry

    # Pass 2: run the contract checkpoints on each PDF.
    results: List[Dict[str, Any]] = []
    for pdf_path in pdf_paths:
        key = _file_key(pdf_path.name)
        unmatched.discard(key)
        print(f"[contract-eval] validate {_nfc(pdf_path.name)}")
        results.append(
            evaluator.evaluate(pdf_path, manifest.get(key), corpus=corpus)
        )

    return {
        "metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pdf_dir": str(pdf_dir),
            "manifest": str(manifest_path) if manifest_path else None,
            "manifest_entries_without_pdf": sorted(unmatched),
            "parser_mode": parser_mode,
            "disabled_layers": list(disabled_layers or []),
            "corpus_cross_check": use_corpus,
            "corpus_documents": (
                sorted(corpus["documents"]) if corpus else []
            ),
            "corpus_relations": corpus["relations"] if corpus else [],
            "rules_path": config.contracts.rules_path,
            "python": platform.python_version(),
            "note": (
                "Real ingestion path up to the Gold checkpoint; nothing was "
                "persisted to the lakehouse."
            ),
        },
        "results": results,
    }


# ---------------------------------------------------------------------------
# Console report
# ---------------------------------------------------------------------------

def _fmt_rate(value: Any) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def print_pdf_report(results: List[Dict[str, Any]]) -> None:
    print("\n========== CONTRACT TEST: PDF ==========")
    for result in results:
        expected = result.get("expected") or "?"
        if not result.get("in_scope", True):
            # Blind-spot / sensitivity probe: reported, never scored.
            verdict = "PROBE"
        else:
            verdict = (
                "OK " if result.get("actual") == expected
                else ("?? " if expected == "?" else "MISS")
            )
        print(
            f"\n{verdict} {result['file']}  "
            f"[{result['mutation_type']}/{result['variant']}]"
        )
        print(
            f"     parser={result.get('parser_used', '-')}  "
            f"doc_id={result.get('doc_id')}"
        )
        meta = result.get("extracted_metadata") or {}
        if meta:
            print(
                f"     issued={meta.get('issued_date')}  "
                f"effective_from={meta.get('effective_from')}  "
                f"effective_to={meta.get('effective_to')}  "
                f"relations={len(result.get('relations') or [])}  "
                f"tables={result.get('num_tables')}"
            )
        print(
            f"     expected={expected} at {result.get('expected_stage') or '-'} "
            f"codes={result.get('expected_codes')}"
        )
        print(
            f"     actual  ={result.get('actual')} at "
            f"{result.get('stopped_at') or '-'}  "
            f"checkpoint_codes={result.get('checkpoint_codes')}"
        )
        if result.get("diagnostic_codes"):
            print(f"     diagnostic_codes={result['diagnostic_codes']}")
        print(
            f"     rule_hit={result.get('rule_hit')}  "
            f"(in pipeline: {result.get('rule_hit_in_pipeline')})  "
            f"stage_match={result.get('stage_match')}"
        )


def print_metric_summary(evaluation: Dict[str, Any]) -> None:
    metrics = evaluation["metrics"]
    print("\n========== SUMMARY ==========")
    print(f"Results                     : {evaluation['num_results']}")
    print(f"In-scope defects            : {evaluation['num_in_scope_mutations']}")
    print(f"Clean controls              : {evaluation['num_clean_controls']}")
    probes = metrics.get("blind_spot_detection_rate")
    if probes is not None:
        print(f"Probe (ngoài phạm vi) bắt được: {_fmt_rate(probes)}")
    print(
        "Contract Detection Rate     : "
        f"{_fmt_rate(metrics['contract_detection_rate'])}"
    )
    print(f"Rule hit rate               : {_fmt_rate(metrics['rule_hit_rate'])}")
    if "rule_hit_in_pipeline_rate" in metrics:
        print(
            "Rule hit rate (pipeline)    : "
            f"{_fmt_rate(metrics['rule_hit_in_pipeline_rate'])}"
        )
    if "stage_match_rate" in metrics:
        print(
            "Expected checkpoint matched : "
            f"{_fmt_rate(metrics['stage_match_rate'])}"
        )
    print(
        "False Positive Rate         : "
        f"{_fmt_rate(metrics['false_positive_rate'])}"
    )
    print("Per mutation type           :")
    for name, rate in metrics["per_mutation_detection_rate"].items():
        print(f"  - {name:<24} {_fmt_rate(rate)}")
    print("Individual rule detection   :")
    for code, values in metrics["individual_rule_detection_rate"].items():
        print(
            f"  - {code:<34} {values['fired']}/{values['targeted_cases']}"
        )
    print(f"Confusion matrix            : {metrics['confusion_matrix']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate VERITAS-RAG contract detection on defect PDFs "
            "(default) or on an existing mutation result JSON (--input)."
        )
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Score an existing result JSON instead of parsing PDFs.",
    )
    parser.add_argument(
        "--pdf-dir",
        default=DEFAULT_PDF_DIR,
        help=f"Folder with test PDFs (default: {DEFAULT_PDF_DIR}).",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help=f"Expectation file (default: <pdf-dir>/{MANIFEST_NAME}).",
    )
    parser.add_argument(
        "--parser",
        choices=["config", "docling", "pymupdf"],
        default="config",
        help="PDF parser: as in config.yaml (default), or force one.",
    )
    parser.add_argument(
        "--disable-layers",
        nargs="*",
        default=None,
        choices=["schema", "temporal", "quality", "provenance"],
        help="Ablation on the PDF set: switch these contract layers off.",
    )
    parser.add_argument(
        "--no-corpus",
        action="store_true",
        help=(
            "Validate each PDF in isolation (no cross-checking against the "
            "other PDFs in the folder)."
        ),
    )
    parser.add_argument(
        "--write-quarantine",
        action="store_true",
        help="Also write quarantine reports to db.quarantine_dir.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path.",
    )
    args = parser.parse_args()

    config = load_system_config()

    if args.input:
        results = load_results(args.input)
        evaluation = evaluate_contracts(results)
        output_path = args.output or config.benchmark.contract_metrics_path
        save_json(evaluation, output_path)
        print(json.dumps(evaluation, ensure_ascii=False, indent=2))
        print(f"\nSaved to: {output_path}")
        return

    setup_environment(config)
    pdf_dir = Path(args.pdf_dir)
    manifest_path = (
        Path(args.manifest) if args.manifest else pdf_dir / MANIFEST_NAME
    )
    payload = run_pdf_suite(
        pdf_dir=pdf_dir,
        manifest_path=manifest_path if manifest_path.exists() else None,
        parser_mode=args.parser,
        write_quarantine=args.write_quarantine,
        config=config,
        disabled_layers=args.disable_layers,
        use_corpus=not args.no_corpus,
    )
    payload["evaluation"] = evaluate_contracts(payload["results"])

    output_path = Path(
        args.output
        or Path(config.benchmark.contract_metrics_path).parent
        / (
            f"pdf_contract_eval_{pdf_dir.name}"
            + (
                "_wo_" + "_".join(sorted(args.disable_layers))
                if args.disable_layers else ""
            )
            + ".json"
        )
    )
    save_json(payload, output_path)

    print_pdf_report(payload["results"])
    print_metric_summary(payload["evaluation"])
    print(f"\nSaved to: {output_path}")


if __name__ == "__main__":
    main()
