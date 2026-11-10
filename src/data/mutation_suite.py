"""
Synthetic Mutation Suite for the VERITAS-RAG executable data contract.

The suite answers RQ1 ("do multi-layer executable contracts catch data
defects before they reach retrieval?") by injecting controlled defects into
*clean, already-materialized* documents and replaying them through the same
checkpoints the ingestion pipeline uses:

    pre-parse  -> Bronze -> Silver -> Gold
    (schema +     (schema +  (temporal +  (provenance +
     provenance)   provenance) quality)     final integrity)

A clean bundle is everything the contract sees for one document:
parser-derived metadata, Silver content and tables, the Silver provision
hierarchy, and the Gold-ready chunks. Mutations edit a deep copy of the
bundle; the source PDF and the lakehouse are never modified. Mutations that
need a different physical file (``provenance_tamper/source_file_modified``)
write a tampered *copy* into a temporary work directory.

Mutation families
-----------------
README core suite (Section 3):
    ocr_corruption, wrong_date, revoked_law, table_break, duplicated_article
Extensions used by the per-layer ablation:
    metadata_corruption (schema), provenance_tamper (provenance)
Controls:
    clean (expected PASS) - measures false positives.

Every case carries ``expected_codes`` (the contract rule(s) that should
fire) so that Individual Rule Detection Rate can be computed, and an
``in_scope`` flag. ``in_scope=False`` marks deliberate blind-spot probes:
real defects the current rule set is *not designed* to catch (e.g. a date
shifted consistently). They are reported separately instead of being mixed
into the headline detection rate.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import shutil
import tempfile
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from src.contracts.document_contract import ExecutableDocumentContract
from src.core.config import SystemConfig, load_system_config


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ContractBundle:
    """Everything the contract checkpoints inspect for one document."""

    doc_id: str
    metadata: Dict[str, Any]
    content: str
    tables: List[Dict[str, Any]] = field(default_factory=list)
    provisions: List[Dict[str, Any]] = field(default_factory=list)
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    related_documents: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)
    source: str = "lakehouse"

    def clone(self) -> "ContractBundle":
        return copy.deepcopy(self)


@dataclass
class MutationCase:
    case_id: str
    doc_id: str
    mutation_type: str
    variant: str
    severity: str
    target_layer: str
    expected: str
    expected_codes: List[str]
    in_scope: bool
    description: str
    bundle: ContractBundle


SEVERITIES = ("low", "medium", "high")

CHECKPOINT_ORDER = ("pre_parse", "bronze", "silver", "gold")


# ---------------------------------------------------------------------------
# Hash helpers (must match ChunkBuilder / ExecutableDocumentContract)
# ---------------------------------------------------------------------------


def compute_chunk_hash(
    chunk_id: str,
    doc_version_hash: str,
    text: str,
    algorithm: str = "sha256",
) -> str:
    """Canonical chunk hash used by ChunkBuilder and the Gold contract."""
    payload = (
        f"chunk_id={chunk_id}\n"
        f"doc_version_hash={doc_version_hash}\n"
        f"text={text}"
    )
    return hashlib.new(algorithm, payload.encode("utf-8")).hexdigest()


def _rehash_chunks(bundle: ContractBundle, algorithm: str) -> None:
    for chunk in bundle.chunks:
        chunk["chunk_hash"] = compute_chunk_hash(
            chunk["chunk_id"],
            chunk["doc_version_hash"],
            chunk["text"],
            algorithm,
        )


def _file_hash(path: str | Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _stable_seed(*parts: Any) -> int:
    text = "|".join(str(part) for part in parts)
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nat", "none", "null"}:
        return None
    return text[:10]


def _as_date(value: Any) -> Optional[date]:
    text = _iso(value)
    return date.fromisoformat(text) if text else None


# ---------------------------------------------------------------------------
# Clean bundle sources
# ---------------------------------------------------------------------------


def _contract_chunk(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Gold row to the DocumentChunk shape validated by the contract."""
    return {
        "chunk_id": row["chunk_id"],
        "article_id": row["article_id"],
        "provision_id": row.get("provision_id"),
        "provision_path": row.get("provision_path"),
        "text": row["text_content"],
        "doc_id": row["logical_doc_id"],
        "page_number": int(row["global_page"]),
        "doc_version_hash": row["doc_version_hash"],
        "chunk_hash": row["chunk_hash"],
    }


def _document_title(
    full_markdown: str,
    doc_id: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Re-derive title/doc_type/issued_date with the pipeline's own parser."""
    try:
        from src.ingestion.pdf_parser import PDFParser

        extracted = PDFParser._extract_document_metadata(
            full_markdown=full_markdown,
            logical_doc_id=doc_id,
            fallback_path="",
        )
        return (
            extracted.get("title"),
            extracted.get("doc_type"),
            extracted.get("issued_date"),
        )
    except Exception:  # pragma: no cover - parser deps are optional here
        return None, None, None


def load_bundles_from_lakehouse(
    config: Optional[SystemConfig] = None,
    doc_ids: Optional[Iterable[str]] = None,
) -> List[ContractBundle]:
    """
    Rebuild contract inputs for every document materialized through Gold.

    Metadata is reconstructed from persisted layers because the lakehouse
    does not store a document table:

    - issued_date, doc_version_hash: Gold chunks of the document;
    - effective_from: the document-wide ("*") explicit effectivity rule in
      ``silver_effectivity_rules``; otherwise the earliest article-level
      effective_from (the parser's own default rule);
    - title / doc_type: re-extracted from Silver page markdown with
      ``PDFParser._extract_document_metadata`` (same code as ingestion);
    - storage_path: Bronze part 1 file path (single-PDF seminar setting).
    """

    config = config or load_system_config()

    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("duckdb is required to read the lakehouse.") from exc

    db_path = Path(config.db.duckdb_path)
    if not db_path.exists():
        raise FileNotFoundError(
            f"Lakehouse not found: {db_path}. Run "
            "`python -m run.prepare_gold_dataset` first, or use --synthetic "
            "for a smoke test."
        )

    connection = duckdb.connect(str(db_path), read_only=True)

    def rows(sql: str, params: Optional[list] = None) -> List[Dict[str, Any]]:
        cursor = connection.execute(sql, params or [])
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, record)) for record in cursor.fetchall()]

    try:
        wanted = set(doc_ids or [])
        documents = [
            row["logical_doc_id"]
            for row in rows(
                "SELECT DISTINCT logical_doc_id FROM gold_chunks "
                "ORDER BY logical_doc_id"
            )
            if not wanted or row["logical_doc_id"] in wanted
        ]

        bundles: List[ContractBundle] = []

        for doc_id in documents:
            gold = rows(
                "SELECT * FROM gold_chunks WHERE logical_doc_id = ? "
                "ORDER BY global_page, provision_path, chunk_index",
                [doc_id],
            )
            pages = rows(
                "SELECT global_page, markdown_content, tables_json "
                "FROM silver_pages WHERE logical_doc_id = ? "
                "ORDER BY global_page",
                [doc_id],
            )
            provisions = rows(
                "SELECT * FROM silver_provisions WHERE logical_doc_id = ? "
                "ORDER BY start_global_page, text_start, provision_path",
                [doc_id],
            )
            rules = rows(
                "SELECT * FROM silver_effectivity_rules "
                "WHERE logical_doc_id = ?",
                [doc_id],
            )
            parts = rows(
                "SELECT file_path FROM bronze_pdf_parts "
                "WHERE logical_doc_id = ? ORDER BY part_no",
                [doc_id],
            )

            if not gold or not parts:
                continue

            content = "\n\n".join(
                str(page.get("markdown_content") or "") for page in pages
            )
            tables: List[Dict[str, Any]] = []
            for page in pages:
                raw_tables = page.get("tables_json")
                if raw_tables:
                    try:
                        tables.extend(json.loads(raw_tables) or [])
                    except json.JSONDecodeError:
                        pass

            document_rule = next(
                (
                    rule
                    for rule in rules
                    if rule.get("target_path") == "*"
                    and rule.get("mode") == "explicit_date"
                    and rule.get("effective_from") is not None
                ),
                None,
            )
            if document_rule is not None:
                effective_from = _iso(document_rule["effective_from"])
            else:
                article_dates = sorted(
                    _iso(row.get("effective_from"))
                    for row in provisions
                    if row.get("level") == "article"
                    and row.get("effective_from") is not None
                )
                effective_from = article_dates[0] if article_dates else None

            title, doc_type, issued_from_text = _document_title(
                content, doc_id
            )
            issued_date = _iso(gold[0].get("issued_date")) or issued_from_text

            metadata = {
                "doc_id": doc_id,
                "title": title or doc_id,
                "doc_type": doc_type,
                "issued_date": issued_date,
                "effective_from": effective_from,
                "effective_to": None,
                "storage_path": str(parts[0]["file_path"]),
                "doc_version_hash": gold[0]["doc_version_hash"],
            }

            silver_provisions = [
                {
                    "provision_id": row["provision_id"],
                    "logical_doc_id": row["logical_doc_id"],
                    "article_id": row["article_id"],
                    "parent_provision_id": row.get("parent_provision_id"),
                    "level": row["level"],
                    "number": row["provision_number"],
                    "path": row["provision_path"],
                    "content": row.get("content") or "",
                }
                for row in provisions
            ]

            bundles.append(
                ContractBundle(
                    doc_id=doc_id,
                    metadata=metadata,
                    content=content,
                    tables=tables,
                    provisions=silver_provisions,
                    chunks=[_contract_chunk(row) for row in gold],
                    source="lakehouse",
                )
            )

        return bundles
    finally:
        connection.close()


_SYNTHETIC_ARTICLES = [
    (
        "Phạm vi điều chỉnh",
        [
            "Luật này quy định về quyền, nghĩa vụ và trách nhiệm của cơ quan, "
            "tổ chức, cá nhân trong hoạt động thử nghiệm dữ liệu pháp lý; "
            "nguyên tắc quản lý, lưu trữ và khai thác hồ sơ điện tử.",
            "Việc áp dụng Luật này phải bảo đảm tính minh bạch, công khai và "
            "khả năng truy vết nguồn gốc của từng văn bản được sử dụng.",
        ],
    ),
    (
        "Đối tượng áp dụng",
        [
            "Cơ quan nhà nước, tổ chức chính trị xã hội và đơn vị sự nghiệp "
            "công lập có hoạt động liên quan đến dữ liệu pháp lý.",
            "Doanh nghiệp, hợp tác xã và cá nhân tham gia cung cấp hoặc khai "
            "thác dịch vụ hỏi đáp pháp luật trên môi trường số.",
        ],
    ),
    (
        "Nguyên tắc lưu trữ hồ sơ",
        [
            "Hồ sơ điện tử phải được lưu giữ đầy đủ, nguyên vẹn, có dấu thời "
            "gian và mã băm xác thực cho từng phiên bản tài liệu.",
            "Mọi thay đổi đối với hồ sơ phải được ghi nhận trong nhật ký và "
            "không được làm mất khả năng khôi phục phiên bản trước đó.",
        ],
    ),
    (
        "Hiệu lực thi hành",
        [
            "Luật này có hiệu lực thi hành từ ngày 01 tháng 7 năm 2026.",
            "Các quy định trước đây trái với Luật này hết hiệu lực kể từ ngày "
            "Luật này có hiệu lực thi hành theo quy định tại khoản 1 Điều này.",
        ],
    ),
]


def build_synthetic_bundle(work_dir: str | Path) -> ContractBundle:
    """
    Build a small, clearly fictitious law for smoke-testing the suite.

    Uses a ``DOC-<24 hex>`` identifier (allowed by the schema contract) so
    it can never be mistaken for a real instrument. Not for reporting.
    """

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    source_path = work_dir / "synthetic_source.pdf"
    source_path.write_bytes(
        b"%PDF-1.4\n% VERITAS-RAG synthetic smoke-test source\n%%EOF\n"
    )
    doc_version_hash = _file_hash(source_path, "sha256")
    doc_id = "DOC-" + hashlib.sha256(b"veritas-synthetic").hexdigest()[:24]

    provisions: List[Dict[str, Any]] = []
    chunks: List[Dict[str, Any]] = []
    lines = [
        "QUỐC HỘI",
        f"Số: {doc_id}",
        "Hà Nội, ngày 20 tháng 11 năm 2025",
        "LUẬT THỬ NGHIỆM DỮ LIỆU PHÁP LÝ (VĂN BẢN GIẢ LẬP)",
    ]

    for number, (title, clauses) in enumerate(_SYNTHETIC_ARTICLES, start=1):
        article_id = f"{doc_id}:article:{number}"
        heading = f"Điều {number}. {title}"
        lines.append(heading)
        provisions.append(
            {
                "provision_id": article_id,
                "logical_doc_id": doc_id,
                "article_id": article_id,
                "parent_provision_id": None,
                "level": "article",
                "number": str(number),
                "path": str(number),
                "content": heading,
            }
        )
        for clause_number, clause_text in enumerate(clauses, start=1):
            path = f"{number}.{clause_number}"
            provision_id = f"{doc_id}:provision:{path}"
            text = f"{clause_number}. {clause_text}"
            lines.append(text)
            provisions.append(
                {
                    "provision_id": provision_id,
                    "logical_doc_id": doc_id,
                    "article_id": article_id,
                    "parent_provision_id": article_id,
                    "level": "clause",
                    "number": str(clause_number),
                    "path": path,
                    "content": text,
                }
            )
            chunk_id = f"{doc_id}:{provision_id}:{number}:0"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "article_id": article_id,
                    "provision_id": provision_id,
                    "provision_path": path,
                    "text": text,
                    "doc_id": doc_id,
                    "page_number": number,
                    "doc_version_hash": doc_version_hash,
                    "chunk_hash": compute_chunk_hash(
                        chunk_id, doc_version_hash, text
                    ),
                }
            )

    metadata = {
        "doc_id": doc_id,
        "title": "Luật thử nghiệm dữ liệu pháp lý (văn bản giả lập)",
        "doc_type": "Luật",
        "issued_date": "2025-11-20",
        "effective_from": "2026-07-01",
        "effective_to": None,
        "storage_path": str(source_path),
        "doc_version_hash": doc_version_hash,
    }

    return ContractBundle(
        doc_id=doc_id,
        metadata=metadata,
        content="\n".join(lines),
        tables=[],
        provisions=provisions,
        chunks=chunks,
        source="synthetic",
    )


# ---------------------------------------------------------------------------
# Text corruption primitives
# ---------------------------------------------------------------------------

# Characters outside QualityMetricsCalculator's allowed set; typical OCR
# garbage on scanned Vietnamese legal PDFs.
_OCR_NOISE_CHARS = "~^`@$&¬¦§•□¤"

_OCR_SEVERITY = {
    # (share of diacritic letters stripped, noise characters per character)
    "low": (0.15, 0.005),
    "medium": (0.50, 0.025),
    "high": (0.95, 0.06),
}


def _strip_diacritic(char: str) -> str:
    if char in "đĐ":
        return "d" if char == "đ" else "D"
    decomposed = unicodedata.normalize("NFD", char)
    base = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", base) or char


def corrupt_text(
    text: str,
    rng: random.Random,
    strip_rate: float,
    noise_rate: float,
) -> str:
    output: List[str] = []
    for char in text:
        if strip_rate and char.isalpha() and rng.random() < strip_rate:
            char = _strip_diacritic(char)
        output.append(char)
        if noise_rate and not char.isspace() and rng.random() < noise_rate:
            output.append(rng.choice(_OCR_NOISE_CHARS))
    return "".join(output)


# ---------------------------------------------------------------------------
# Mutation operators
# ---------------------------------------------------------------------------

MutationBuilder = Callable[["MutationSuite", ContractBundle], List[MutationCase]]


class MutationSuite:
    """Generate mutation cases from clean bundles."""

    CORE_TYPES = (
        "ocr_corruption",
        "wrong_date",
        "revoked_law",
        "table_break",
        "duplicated_article",
    )
    EXTENSION_TYPES = ("metadata_corruption", "provenance_tamper")

    def __init__(
        self,
        seed: int = 42,
        work_dir: str | Path | None = None,
        hash_algorithm: str = "sha256",
        mutation_types: Optional[Iterable[str]] = None,
    ):
        self.seed = seed
        self.hash_algorithm = hash_algorithm
        self._owns_work_dir = work_dir is None
        self.work_dir = Path(
            work_dir or tempfile.mkdtemp(prefix="veritas_mutations_")
        )
        self.work_dir.mkdir(parents=True, exist_ok=True)
        selected = list(mutation_types or ("clean",) + self.CORE_TYPES
                        + self.EXTENSION_TYPES)
        builders: Dict[str, MutationBuilder] = {
            "clean": MutationSuite._clean_controls,
            "ocr_corruption": MutationSuite._ocr_corruption,
            "wrong_date": MutationSuite._wrong_date,
            "revoked_law": MutationSuite._revoked_law,
            "table_break": MutationSuite._table_break,
            "duplicated_article": MutationSuite._duplicated_article,
            "metadata_corruption": MutationSuite._metadata_corruption,
            "provenance_tamper": MutationSuite._provenance_tamper,
        }
        unknown = set(selected).difference(builders)
        if unknown:
            raise ValueError(
                "Unknown mutation type(s): " + ", ".join(sorted(unknown))
            )
        self.builders = [(name, builders[name]) for name in selected]

    def close(self) -> None:
        if self._owns_work_dir:
            shutil.rmtree(self.work_dir, ignore_errors=True)

    def __enter__(self) -> "MutationSuite":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    # -- public -------------------------------------------------------------

    def generate(self, bundles: Iterable[ContractBundle]) -> List[MutationCase]:
        cases: List[MutationCase] = []
        for bundle in bundles:
            for _, builder in self.builders:
                cases.extend(builder(self, bundle))
        return cases

    # -- helpers ------------------------------------------------------------

    def _rng(self, *parts: Any) -> random.Random:
        return random.Random(_stable_seed(self.seed, *parts))

    @staticmethod
    def _case(
        bundle: ContractBundle,
        mutated: ContractBundle,
        mutation_type: str,
        variant: str,
        target_layer: str,
        expected_codes: List[str],
        description: str,
        severity: str = "n/a",
        expected: str = "QUARANTINE",
        in_scope: bool = True,
    ) -> MutationCase:
        case_id = f"{bundle.doc_id}|{mutation_type}|{variant}|{severity}"
        return MutationCase(
            case_id=case_id,
            doc_id=bundle.doc_id,
            mutation_type=mutation_type,
            variant=variant,
            severity=severity,
            target_layer=target_layer,
            expected=expected,
            expected_codes=expected_codes,
            in_scope=in_scope,
            description=description,
            bundle=mutated,
        )

    def _predecessor(
        self,
        bundle: ContractBundle,
    ) -> Dict[str, Any]:
        """Fictitious predecessor replaced by ``bundle`` (valid by default)."""
        issued = _as_date(bundle.metadata.get("issued_date"))
        effective = _as_date(bundle.metadata.get("effective_from"))
        if issued is None or effective is None:
            raise ValueError("Bundle metadata lacks issued/effective dates.")
        predecessor_issued = issued - timedelta(days=5 * 365)
        return {
            "doc_id": "DOC-" + hashlib.sha256(
                f"predecessor:{bundle.doc_id}".encode("utf-8")
            ).hexdigest()[:24],
            "title": f"Văn bản tiền nhiệm giả lập của {bundle.doc_id}",
            "doc_type": bundle.metadata.get("doc_type"),
            "issued_date": predecessor_issued.isoformat(),
            "effective_from": (predecessor_issued + timedelta(days=180)).isoformat(),
            "effective_to": effective.isoformat(),
        }

    def _successor(
        self,
        bundle: ContractBundle,
    ) -> Dict[str, Any]:
        """Fictitious later document that replaces ``bundle``."""
        issued = _as_date(bundle.metadata.get("issued_date"))
        effective = _as_date(bundle.metadata.get("effective_from"))
        if issued is None or effective is None:
            raise ValueError("Bundle metadata lacks issued/effective dates.")
        successor_issued = max(issued, effective) + timedelta(days=365)
        return {
            "doc_id": "DOC-" + hashlib.sha256(
                f"successor:{bundle.doc_id}".encode("utf-8")
            ).hexdigest()[:24],
            "title": f"Văn bản thay thế giả lập của {bundle.doc_id}",
            "doc_type": bundle.metadata.get("doc_type"),
            "issued_date": successor_issued.isoformat(),
            "effective_from": (successor_issued + timedelta(days=60)).isoformat(),
            "effective_to": None,
        }

    @staticmethod
    def _inject_table(mutated: ContractBundle) -> Dict[str, Any]:
        """Append one well-formed table (markdown + structured form)."""
        header = ["STT", "Mức vi phạm", "Mức phạt tiền"]
        body = [
            ["1", "Vi phạm lần đầu", "Từ 1.000.000 đồng đến 3.000.000 đồng"],
            ["2", "Tái phạm", "Từ 3.000.000 đồng đến 5.000.000 đồng"],
            ["3", "Vi phạm nghiêm trọng", "Từ 5.000.000 đồng đến 10.000.000 đồng"],
            ["4", "Vi phạm có tổ chức", "Từ 10.000.000 đồng đến 20.000.000 đồng"],
        ]
        rows = [header] + body
        markdown = "\n".join(
            ["| " + " | ".join(header) + " |", "| --- | --- | --- |"]
            + ["| " + " | ".join(row) + " |" for row in body]
        )
        table = {
            "rows": len(rows),
            "columns": len(header),
            "cells": [
                {"row": r, "column": c, "rowspan": 1, "colspan": 1, "value": v}
                for r, row in enumerate(rows)
                for c, v in enumerate(row)
            ],
            "markdown": markdown,
            "synthetic": True,
        }
        mutated.content = mutated.content.rstrip() + "\n\n" + markdown + "\n"
        mutated.tables = list(mutated.tables) + [table]
        return table

    @staticmethod
    def _article_groups(bundle: ContractBundle) -> List[List[Dict[str, Any]]]:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for row in bundle.provisions:
            groups.setdefault(str(row["article_id"]), []).append(row)
        return list(groups.values())

    def _pick_article(
        self,
        bundle: ContractBundle,
        rng: random.Random,
    ) -> Optional[List[Dict[str, Any]]]:
        candidates = []
        for group in self._article_groups(bundle):
            article_id = group[0]["article_id"]
            words = sum(
                len(str(chunk.get("text") or "").split())
                for chunk in bundle.chunks
                if chunk.get("article_id") == article_id
            )
            if words >= 40:
                candidates.append(group)
        return rng.choice(candidates) if candidates else None

    # -- controls -----------------------------------------------------------

    def _clean_controls(self, bundle: ContractBundle) -> List[MutationCase]:
        cases = [
            self._case(
                bundle, bundle.clone(), "clean", "original", "none", [],
                "Unmodified clean bundle.", expected="PASS",
            )
        ]

        reflowed = bundle.clone()
        reflowed.content = re.sub(r"[ \t]+", " ", reflowed.content)
        reflowed.content = re.sub(r"\n{3,}", "\n\n", reflowed.content)
        cases.append(
            self._case(
                bundle, reflowed, "clean", "whitespace_reflow", "none", [],
                "Benign whitespace normalisation of Silver content.",
                expected="PASS",
            )
        )

        with_table = bundle.clone()
        self._inject_table(with_table)
        cases.append(
            self._case(
                bundle, with_table, "clean", "valid_table", "none", [],
                "A well-formed table appended (control for table_break).",
                expected="PASS",
            )
        )

        try:
            successor = self._successor(bundle)
        except ValueError:
            return cases
        related = bundle.clone()
        related.metadata["effective_to"] = successor["effective_from"]
        related.related_documents = [successor]
        related.relations = [
            {
                "source_doc_id": successor["doc_id"],
                "target_doc_id": bundle.doc_id,
                "relation_type": "replaces",
            }
        ]
        cases.append(
            self._case(
                bundle, related, "clean", "valid_replacement", "none", [],
                "The document is replaced by a later one and its validity "
                "correctly ends on the successor's effective date (control "
                "for revoked_law).",
                expected="PASS",
            )
        )
        return cases

    # -- 1. OCR corruption (quality) ---------------------------------------

    def _ocr_corruption(self, bundle: ContractBundle) -> List[MutationCase]:
        cases: List[MutationCase] = []
        variants = {
            "diacritic_loss": (True, False, ["DIACRITIC_DENSITY_LOW"]),
            "noise_injection": (False, True, ["OCR_NOISE_EXCEEDED"]),
            "mixed": (
                True,
                True,
                ["DIACRITIC_DENSITY_LOW", "OCR_NOISE_EXCEEDED"],
            ),
        }
        for variant, (strip, noise, codes) in variants.items():
            for severity in SEVERITIES:
                strip_rate, noise_rate = _OCR_SEVERITY[severity]
                strip_rate = strip_rate if strip else 0.0
                noise_rate = noise_rate if noise else 0.0
                mutated = bundle.clone()
                rng = self._rng(bundle.doc_id, "ocr", variant, severity)
                mutated.content = corrupt_text(
                    mutated.content, rng, strip_rate, noise_rate
                )
                # The parser would have produced the same corrupted text in
                # Silver provisions and in Gold chunks; hashes are rebuilt
                # so that only the quality layer is responsible.
                for row in mutated.provisions:
                    row["content"] = corrupt_text(
                        row["content"], rng, strip_rate, noise_rate
                    )
                for chunk in mutated.chunks:
                    chunk["text"] = corrupt_text(
                        chunk["text"], rng, strip_rate, noise_rate
                    )
                _rehash_chunks(mutated, self.hash_algorithm)
                cases.append(
                    self._case(
                        bundle, mutated, "ocr_corruption", variant, "quality",
                        codes,
                        f"OCR corruption: strip {strip_rate:.0%} of "
                        f"diacritics, {noise_rate:.1%} noise characters.",
                        severity=severity,
                    )
                )
        return cases

    # -- 2. Wrong date (temporal / schema) ---------------------------------

    def _wrong_date(self, bundle: ContractBundle) -> List[MutationCase]:
        issued = _as_date(bundle.metadata.get("issued_date"))
        effective = _as_date(bundle.metadata.get("effective_from"))
        if issued is None or effective is None:
            return []

        cases: List[MutationCase] = []

        mutated = bundle.clone()
        mutated.metadata["effective_from"] = (
            issued - timedelta(days=30)
        ).isoformat()
        cases.append(
            self._case(
                bundle, mutated, "wrong_date", "effective_before_issued",
                "temporal", ["INVALID_START_DATE"],
                "effective_from set 30 days before issued_date.",
            )
        )

        mutated = bundle.clone()
        mutated.metadata["effective_to"] = (
            effective - timedelta(days=1)
        ).isoformat()
        cases.append(
            self._case(
                bundle, mutated, "wrong_date", "end_before_start",
                "temporal", ["INVALID_END_DATE"],
                "effective_to set one day before effective_from.",
            )
        )

        mutated = bundle.clone()
        mutated.metadata["issued_date"] = f"{issued.year}-02-30"
        cases.append(
            self._case(
                bundle, mutated, "wrong_date", "impossible_calendar_date",
                "schema", ["SCHEMA_VIOLATION"],
                "issued_date replaced by a non-existent calendar date.",
            )
        )

        mutated = bundle.clone()
        mutated.metadata["issued_date"] = (
            issued + timedelta(days=365)
        ).isoformat()
        mutated.metadata["effective_from"] = (
            effective + timedelta(days=365)
        ).isoformat()
        cases.append(
            self._case(
                bundle, mutated, "wrong_date", "shifted_consistent",
                "temporal", ["INVALID_START_DATE"],
                "Blind-spot probe: both dates shifted by one year while "
                "staying internally consistent. Needs an external "
                "reference (e.g. official gazette) to detect.",
                in_scope=False,
            )
        )
        return cases

    # -- 3. Revoked law (cross-document temporal) ---------------------------

    def _revoked_law(self, bundle: ContractBundle) -> List[MutationCase]:
        """
        The validated document is the REPLACED one (relation target) for the
        "still in force" variants, and the replacing one (relation source)
        for the inverted-order variant, matching where the contract places
        each defect.
        """
        try:
            successor = self._successor(bundle)
            predecessor = self._predecessor(bundle)
        except ValueError:
            return []
        effective = _as_date(bundle.metadata["effective_from"])
        issued = _as_date(bundle.metadata["issued_date"])
        successor_effective = _as_date(successor["effective_from"])

        def replaced_by_successor(own_effective_to: Optional[str]) -> ContractBundle:
            mutated = bundle.clone()
            mutated.metadata["effective_to"] = own_effective_to
            mutated.related_documents = [dict(successor)]
            mutated.relations = [
                {
                    "source_doc_id": successor["doc_id"],
                    "target_doc_id": bundle.doc_id,
                    "relation_type": "replaces",
                }
            ]
            return mutated

        inverted = bundle.clone()
        newer_target = dict(predecessor)
        newer_target.update(
            {
                "issued_date": (issued + timedelta(days=30)).isoformat(),
                "effective_from": (effective + timedelta(days=30)).isoformat(),
                "effective_to": (effective + timedelta(days=400)).isoformat(),
            }
        )
        inverted.related_documents = [newer_target]
        inverted.relations = [
            {
                "source_doc_id": bundle.doc_id,
                "target_doc_id": predecessor["doc_id"],
                "relation_type": "replaces",
            }
        ]

        return [
            self._case(
                bundle, replaced_by_successor(None), "revoked_law",
                "still_in_force", "temporal",
                ["REVOKED_TARGET_STILL_EFFECTIVE"],
                "The document has been replaced by a later one but keeps an "
                "open-ended validity interval.",
            ),
            self._case(
                bundle,
                replaced_by_successor(
                    (successor_effective + timedelta(days=365)).isoformat()
                ),
                "revoked_law", "ends_after_replacement", "temporal",
                ["REVOKED_TARGET_STILL_EFFECTIVE"],
                "The document's effective_to falls one year after its "
                "successor takes effect.",
            ),
            self._case(
                bundle, inverted,
                "revoked_law", "replaced_by_older_document", "temporal",
                [
                    "RELATION_ISSUED_ORDER_INVALID",
                    "RELATION_EFFECTIVE_ORDER_INVALID",
                ],
                "Relation direction inverted: the document claims to "
                "replace one that is newer than itself.",
            ),
        ]

    # -- 4. Table break (quality) ------------------------------------------

    def _table_break(self, bundle: ContractBundle) -> List[MutationCase]:
        cases: List[MutationCase] = []

        drop_share = {"low": 0.2, "medium": 0.5, "high": 1.0}
        for severity in SEVERITIES:
            mutated = bundle.clone()
            table = self._inject_table(mutated)
            rng = self._rng(bundle.doc_id, "table", severity)
            lines = table["markdown"].splitlines()
            body_indexes = list(range(2, len(lines)))
            count = max(1, round(len(body_indexes) * drop_share[severity]))
            broken = set(rng.sample(body_indexes, count))
            new_lines = []
            for index, line in enumerate(lines):
                if index in broken:
                    cells = line.strip().strip("|").split("|")
                    cells = cells[:-1]
                    line = "| " + " | ".join(c.strip() for c in cells) + " |"
                new_lines.append(line)
            mutated.content = mutated.content.replace(
                table["markdown"], "\n".join(new_lines)
            )
            cases.append(
                self._case(
                    bundle, mutated, "table_break", "ragged_rows", "quality",
                    ["TABLE_INTEGRITY_FAILED"],
                    f"{count}/{len(body_indexes)} markdown table rows lose "
                    "their last cell.",
                    severity=severity,
                )
            )

        mutated = bundle.clone()
        table = self._inject_table(mutated)
        for cell in table["cells"]:
            cell["row"] += 1
        cases.append(
            self._case(
                bundle, mutated, "table_break", "missing_header", "quality",
                ["TABLE_HEADER_MISSING"],
                "Structured table loses its header row (row 0).",
            )
        )

        mutated = bundle.clone()
        table = self._inject_table(mutated)
        table["cells"][1]["colspan"] = table["columns"] + 2
        cases.append(
            self._case(
                bundle, mutated, "table_break", "span_overflow", "quality",
                ["MERGED_CELL_ISSUE"],
                "A merged cell spans beyond the table boundary.",
            )
        )
        return cases

    # -- 5. Duplicated article (quality structure / Gold integrity) ---------

    def _duplicated_article(self, bundle: ContractBundle) -> List[MutationCase]:
        rng = self._rng(bundle.doc_id, "duplicate")
        group = self._pick_article(bundle, rng)
        if group is None:
            return []
        article_id = group[0]["article_id"]
        article_chunks = [
            chunk for chunk in bundle.chunks if chunk["article_id"] == article_id
        ]
        article_text = "\n".join(row["content"] for row in group)
        cases: List[MutationCase] = []

        # (a) same number, same identifiers: the parser emitted the article
        # twice (e.g. repeated page in a scan).
        mutated = bundle.clone()
        mutated.provisions.extend(copy.deepcopy(group))
        mutated.chunks.extend(copy.deepcopy(article_chunks))
        mutated.content = mutated.content.rstrip() + "\n\n" + article_text
        cases.append(
            self._case(
                bundle, mutated, "duplicated_article", "same_number",
                "quality",
                [
                    "DUPLICATE_ARTICLE_HEADING",
                    "DUPLICATE_ARTICLE_NUMBER",
                    "DUPLICATE_PROVISION_ID",
                    "DUPLICATE_CHUNK_ID",
                ],
                f"Article {group[0]['number']} appears twice with the same "
                "number and identifiers.",
            )
        )

        # (b) copied under a new number: identical body, fresh identifiers.
        numbers = [
            int(re.match(r"\d+", str(row["number"])).group())
            for row in bundle.provisions
            if row["level"] == "article" and re.match(r"\d+", str(row["number"]))
        ]
        new_number = str((max(numbers) if numbers else 0) + 1)
        old_path = str(group[0]["path"])
        new_article_id = re.sub(r"[^:]+$", new_number, str(article_id))

        def renumber(path: str) -> str:
            return new_number + path[len(old_path):]

        mutated = bundle.clone()
        id_map: Dict[str, str] = {}
        for row in group:
            new_row = copy.deepcopy(row)
            new_path = renumber(str(row["path"]))
            new_id = (
                new_article_id
                if row["level"] == "article"
                else f"{bundle.doc_id}:provision:{new_path}"
            )
            id_map[row["provision_id"]] = new_id
            new_row.update(
                provision_id=new_id,
                article_id=new_article_id,
                path=new_path,
                number=new_number if row["level"] == "article" else row["number"],
                parent_provision_id=id_map.get(row.get("parent_provision_id")),
            )
            mutated.provisions.append(new_row)
        for index, chunk in enumerate(article_chunks):
            new_chunk = copy.deepcopy(chunk)
            provision_id = id_map.get(chunk.get("provision_id"), new_article_id)
            new_chunk.update(
                chunk_id=f"{bundle.doc_id}:{provision_id}:dup:{index}",
                article_id=new_article_id,
                provision_id=provision_id,
                provision_path=renumber(str(chunk.get("provision_path") or old_path)),
            )
            new_chunk["chunk_hash"] = compute_chunk_hash(
                new_chunk["chunk_id"],
                new_chunk["doc_version_hash"],
                new_chunk["text"],
                self.hash_algorithm,
            )
            mutated.chunks.append(new_chunk)
        renumbered_text = re.sub(
            rf"(Điều\s+){re.escape(str(group[0]['number']))}(?=\s*[.:])",
            lambda match: match.group(1) + new_number,
            article_text,
            count=1,
        )
        mutated.content = mutated.content.rstrip() + "\n\n" + renumbered_text
        cases.append(
            self._case(
                bundle, mutated, "duplicated_article", "renumbered_copy",
                "quality",
                ["DUPLICATE_ARTICLE_CONTENT", "DUPLICATE_CHUNK_CONTENT"],
                f"Article {group[0]['number']} copied verbatim as Article "
                f"{new_number}.",
            )
        )

        # (c) blind-spot probe: only part of the article is duplicated, and
        # the copy is lightly edited, so no exact-match rule can fire.
        mutated = bundle.clone()
        # Body only (no "Điều N." heading line): a heading copy would be
        # caught by DUPLICATE_ARTICLE_HEADING and the probe would no longer
        # test the exact-match blind spot.
        body_lines = article_text.splitlines()[1:] or article_text.splitlines()
        words = " ".join(body_lines).split()
        partial = " ".join(words[: max(10, len(words) // 2)]) + " (sao chép một phần)"
        partial_id = f"{bundle.doc_id}:article:{new_number}"
        mutated.provisions.append(
            {
                "provision_id": partial_id,
                "logical_doc_id": bundle.doc_id,
                "article_id": partial_id,
                "parent_provision_id": None,
                "level": "article",
                "number": new_number,
                "path": new_number,
                "content": partial,
            }
        )
        partial_chunk_id = f"{bundle.doc_id}:{partial_id}:partial:0"
        mutated.chunks.append(
            {
                "chunk_id": partial_chunk_id,
                "article_id": partial_id,
                "provision_id": partial_id,
                "provision_path": new_number,
                "text": partial,
                "doc_id": bundle.doc_id,
                "page_number": int(article_chunks[0]["page_number"]),
                "doc_version_hash": bundle.metadata["doc_version_hash"],
                "chunk_hash": compute_chunk_hash(
                    partial_chunk_id,
                    bundle.metadata["doc_version_hash"],
                    partial,
                    self.hash_algorithm,
                ),
            }
        )
        mutated.content = mutated.content.rstrip() + "\n\n" + partial
        cases.append(
            self._case(
                bundle, mutated, "duplicated_article", "partial_near_duplicate",
                "quality",
                ["DUPLICATE_ARTICLE_CONTENT", "DUPLICATE_CHUNK_CONTENT"],
                "Blind-spot probe: half of an article re-appears with a small "
                "edit; exact-match duplicate rules cannot see it.",
                in_scope=False,
            )
        )
        return cases

    # -- 6. Metadata corruption (schema) -- extension -----------------------

    def _metadata_corruption(self, bundle: ContractBundle) -> List[MutationCase]:
        variants = {
            # Header text leaking into the identifier ("Số: 45/2019/QH14")
            # is a typical extraction error; it violates the ID pattern for
            # every document type, including DOC-<hex> identifiers.
            "invalid_doc_id": ("doc_id", f"Số: {bundle.doc_id}"),
            "missing_title": ("title", ""),
            "unsupported_doc_type": ("doc_type", "Công văn"),
            "missing_effective_from": ("effective_from", None),
        }
        cases = []
        for variant, (key, value) in variants.items():
            mutated = bundle.clone()
            mutated.metadata[key] = value
            cases.append(
                self._case(
                    bundle, mutated, "metadata_corruption", variant, "schema",
                    ["SCHEMA_VIOLATION"],
                    f"Metadata field '{key}' corrupted.",
                )
            )
        return cases

    # -- 7. Provenance tamper -- extension ---------------------------------

    def _provenance_tamper(self, bundle: ContractBundle) -> List[MutationCase]:
        cases: List[MutationCase] = []
        rng = self._rng(bundle.doc_id, "provenance")

        source = Path(str(bundle.metadata.get("storage_path") or ""))
        if source.is_file():
            target_dir = self.work_dir / hashlib.sha256(
                bundle.doc_id.encode("utf-8")
            ).hexdigest()[:16]
            target_dir.mkdir(parents=True, exist_ok=True)
            tampered = target_dir / f"tampered_{source.name}"
            shutil.copyfile(source, tampered)
            with open(tampered, "ab") as handle:
                handle.write(b"\n% tampered after ingestion\n")
            mutated = bundle.clone()
            mutated.metadata["storage_path"] = str(tampered)
            cases.append(
                self._case(
                    bundle, mutated, "provenance_tamper",
                    "source_file_modified", "provenance",
                    ["DOCUMENT_HASH_MISMATCH"],
                    "Source PDF bytes changed after its hash was recorded "
                    "(applied to a temporary copy).",
                )
            )

        if bundle.chunks:
            index = rng.randrange(len(bundle.chunks))

            mutated = bundle.clone()
            mutated.chunks[index]["text"] += " (đã chỉnh sửa)"
            cases.append(
                self._case(
                    bundle, mutated, "provenance_tamper", "chunk_text_tampered",
                    "provenance", ["CHUNK_HASH_MISMATCH"],
                    "One Gold chunk's text edited without recomputing its hash.",
                )
            )

            mutated = bundle.clone()
            chunk = mutated.chunks[index]
            chunk["doc_version_hash"] = hashlib.sha256(
                b"another-version"
            ).hexdigest()
            chunk["chunk_hash"] = compute_chunk_hash(
                chunk["chunk_id"], chunk["doc_version_hash"], chunk["text"],
                self.hash_algorithm,
            )
            cases.append(
                self._case(
                    bundle, mutated, "provenance_tamper", "chunk_wrong_version",
                    "provenance", ["CHUNK_DOCUMENT_HASH_MISMATCH"],
                    "One chunk points to a different document version "
                    "(self-consistent chunk hash).",
                )
            )

            mutated = bundle.clone()
            mutated.chunks[index]["provision_id"] = None
            cases.append(
                self._case(
                    bundle, mutated, "provenance_tamper", "orphan_chunk",
                    "provenance", ["PROVISION_ID_MISSING"],
                    "One chunk loses its provision_id lineage link.",
                )
            )
        return cases


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_contract_checkpoints(
    contract: ExecutableDocumentContract,
    bundle: ContractBundle,
) -> Dict[str, Dict[str, Any]]:
    """Replay one bundle through all four pipeline checkpoints."""
    metadata = bundle.metadata
    return {
        "pre_parse": contract.validate_pre_parse(dict(metadata)),
        "bronze": contract.validate_bronze(dict(metadata)),
        "silver": contract.validate_silver(
            dict(metadata),
            content=bundle.content,
            tables=bundle.tables,
            provisions=bundle.provisions,
            related_documents=bundle.related_documents or None,
            relations=bundle.relations or None,
        ),
        "gold": contract.validate_gold(dict(metadata), bundle.chunks),
    }


def evaluate_case(
    contract: ExecutableDocumentContract,
    case: MutationCase,
) -> Dict[str, Any]:
    """Run a case and summarise it in the format read by run_contract_eval."""
    started = time.perf_counter()
    reports = run_contract_checkpoints(contract, case.bundle)
    elapsed_ms = (time.perf_counter() - started) * 1000

    stopped_at = next(
        (
            name
            for name in CHECKPOINT_ORDER
            if reports[name]["status"] != "PASSED"
        ),
        None,
    )
    issue_codes = sorted(
        {
            issue["code"]
            for name in CHECKPOINT_ORDER
            for issue in reports[name]["issues"]
        }
    )
    detected_layers = sorted(
        {
            issue["layer"]
            for name in CHECKPOINT_ORDER
            for issue in reports[name]["issues"]
        }
    )
    actual = "PASS" if stopped_at is None else "QUARANTINE"

    return {
        "case_id": case.case_id,
        "doc_id": case.doc_id,
        "mutation_type": case.mutation_type,
        "variant": case.variant,
        "severity": case.severity,
        "target_layer": case.target_layer,
        "in_scope": case.in_scope,
        "expected": case.expected,
        "actual": actual,
        "expected_codes": case.expected_codes,
        "issue_codes": issue_codes,
        "rule_hit": (
            any(code in issue_codes for code in case.expected_codes)
            if case.expected_codes
            else None
        ),
        "detected_layers": detected_layers,
        # Pipeline semantics: the first failing checkpoint blocks promotion.
        "stopped_at": stopped_at,
        "checkpoint_status": {
            name: reports[name]["status"] for name in CHECKPOINT_ORDER
        },
        "layer_status": {
            name: reports[name]["layer_status"] for name in CHECKPOINT_ORDER
        },
        "contract_time_ms": round(elapsed_ms, 3),
        "description": case.description,
        "bundle_source": case.bundle.source,
    }


def run_cases(
    contract: ExecutableDocumentContract,
    cases: Iterable[MutationCase],
) -> List[Dict[str, Any]]:
    return [evaluate_case(contract, case) for case in cases]
