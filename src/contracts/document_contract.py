import hashlib
import json
import os
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from pydantic import ValidationError

from src.core.config import SystemConfig
from src.core.interfaces import BaseContractGate

from src.contracts.models import (
    ContractIssue,
    ContractReport,
    DocumentChunk,
    DocumentMetadata,
    DocumentRelation,
    QualityMetrics,
)

from src.contracts.quality_metrics import (
    QualityMetricsCalculator,
)

from src.contracts.rules import (
    ContractRules,
    load_contract_rules,
)


_PAST_TENSE = {
    "amends": "amended",
    "replaces": "replaced",
    "repeals": "repealed",
}


class ExecutableDocumentContract(
    BaseContractGate
):
    """
    Four-layer executable document contract.

    Layer 1: Schema
    Layer 2: Temporal
    Layer 3: Quality
    Layer 4: Provenance

    Validation is enforced across the Medallion lifecycle
    through multiple checkpoints.

    Pre-parse:
        Schema + document-level provenance

    Bronze:
        Schema + document-level provenance
        (revalidated after Bronze persistence)

    Silver:
        Temporal + Quality

    Gold:
        Chunk-level provenance + final integrity

    The contract remains one executable four-layer contract.
    The layers are enforced at different pipeline checkpoints
    according to the data lifecycle.
    """

    LAYERS = ("schema", "temporal", "quality", "provenance")

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        disabled_layers: Optional[Iterable[str]] = None,
        write_quarantine_reports: bool = True,
    ):
        """
        Parameters
        ----------
        disabled_layers:
            Layers to switch off for ablation studies
            (subset of ``schema``, ``temporal``, ``quality``,
            ``provenance``). A disabled layer is reported as
            ``DISABLED`` and never contributes issues. The production
            pipeline leaves this empty.

        write_quarantine_reports:
            When False, failing reports are returned but not written to
            ``db.quarantine_dir``. Mutation and ablation runs use this to
            avoid flooding the quarantine area with synthetic failures.
        """
        super().__init__(config)

        self.rules: ContractRules = (
            load_contract_rules(
                self.config.contracts.rules_path
            )
        )

        disabled = {
            str(layer).strip().lower()
            for layer in (disabled_layers or [])
            if str(layer).strip()
        }
        unknown = disabled.difference(self.LAYERS)
        if unknown:
            raise ValueError(
                "Unknown contract layer(s) to disable: "
                + ", ".join(sorted(unknown))
            )

        self.disabled_layers = frozenset(disabled)
        self.write_quarantine_reports = write_quarantine_reports

    def _layer_enabled(self, layer: str) -> bool:
        return layer not in self.disabled_layers

    # ========================================================
    # Layer 1 — Schema
    # ========================================================

    def validate_metadata(
        self,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:

        issues: List[ContractIssue] = []

        try:
            required_fields = (
                self.rules
                .schema_contract
                .required_fields
            )

            missing = [
                field
                for field in required_fields
                if metadata.get(field) in (
                    None,
                    "",
                )
            ]

            if missing:
                raise ValueError(
                    "Missing required fields: "
                    + ", ".join(missing)
                )

            normalized = DocumentMetadata.model_validate(
                metadata
            )

            self._validate_doc_id(
                normalized.doc_id
            )

            self._validate_doc_type(
                normalized.doc_type
            )

            return {
                "status": "PASSED",
                "metadata": normalized.model_dump(),
                "issues": [],
            }

        except (
            ValidationError,
            ValueError,
        ) as exc:

            issues.append(
                ContractIssue(
                    layer="schema",
                    code="SCHEMA_VIOLATION",
                    message=str(exc),
                )
            )

            return {
                "status": "QUARANTINE",
                "metadata": None,
                "issues": issues,
            }

    def _validate_doc_id(
        self,
        doc_id: str,
    ) -> None:

        pattern = (
            self.rules
            .schema_contract
            .doc_id_pattern
        )

        if not re.fullmatch(
            pattern,
            doc_id,
        ):
            raise ValueError(
                f"doc_id '{doc_id}' does not "
                f"match configured pattern."
            )

    def _validate_doc_type(
        self,
        doc_type: Optional[str],
    ) -> None:

        allowed = (
            self.rules
            .schema_contract
            .allowed_doc_types
        )

        if (
            doc_type is not None
            and allowed
            and doc_type not in allowed
        ):
            raise ValueError(
                f"Unsupported doc_type: {doc_type}"
            )

    # ========================================================
    # Layer 2 — Temporal
    # ========================================================

    def _validate_temporal(
        self,
        metadata: DocumentMetadata,
    ) -> List[ContractIssue]:

        issues: List[ContractIssue] = []

        rules = (
            self.rules
            .temporal_contract
            .intra_document
        )

        if not rules.enabled:
            return issues

        if (
            rules.effective_from_on_or_after_issued
            and metadata.effective_from
            < metadata.issued_date
        ):
            issues.append(
                ContractIssue(
                    layer="temporal",
                    code="INVALID_START_DATE",
                    message=(
                        "effective_from must be "
                        "on or after issued_date."
                    ),
                )
            )

        if (
            rules.effective_to_after_effective_from
            and metadata.effective_to is not None
            and metadata.effective_to
            <= metadata.effective_from
        ):
            issues.append(
                ContractIssue(
                    layer="temporal",
                    code="INVALID_END_DATE",
                    message=(
                        "effective_to must be "
                        "after effective_from."
                    ),
                )
            )

        return issues

    # ========================================================
    # Layer 3 — Quality
    # ========================================================

    def _validate_quality(
        self,
        content: str,
        tables: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> tuple[
        QualityMetrics,
        List[ContractIssue]
    ]:

        rules = (
            self.rules
            .quality_contract
        )

        raw_metrics = (
            QualityMetricsCalculator.calculate(
                content,
                tables=tables,
            )
        )

        metrics = QualityMetrics.model_validate(
            raw_metrics
        )

        issues: List[ContractIssue] = []

        if (
            metrics.null_ratio
            > rules.max_null_ratio
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="NULL_RATIO_EXCEEDED",
                    message=(
                        f"null_ratio={metrics.null_ratio:.4f} "
                        f"> {rules.max_null_ratio:.4f}"
                    ),
                )
            )

        if (
            metrics.diacritic_density
            < rules.min_diacritic_density
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="DIACRITIC_DENSITY_LOW",
                    message=(
                        f"diacritic_density="
                        f"{metrics.diacritic_density:.4f} "
                        f"< "
                        f"{rules.min_diacritic_density:.4f}"
                    ),
                )
            )

        if (
            metrics.ocr_noise_ratio
            > rules.max_ocr_noise_ratio
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="OCR_NOISE_EXCEEDED",
                    message=(
                        f"ocr_noise_ratio="
                        f"{metrics.ocr_noise_ratio:.4f} "
                        f"> "
                        f"{rules.max_ocr_noise_ratio:.4f}"
                    ),
                )
            )

        if (
            metrics.corrupted_token_ratio
            > rules.max_corrupted_token_ratio
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="OCR_TOKEN_CORRUPTION",
                    message=(
                        f"corrupted_token_ratio="
                        f"{metrics.corrupted_token_ratio:.4f} "
                        f"> "
                        f"{rules.max_corrupted_token_ratio:.4f}"
                    ),
                )
            )

        table_rules = (
            rules.table_integrity
        )

        if (
            metrics.broken_cells_ratio
            > table_rules.max_broken_cells_ratio
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="TABLE_INTEGRITY_FAILED",
                    message=(
                        f"broken_cells_ratio="
                        f"{metrics.broken_cells_ratio:.4f} "
                        f"> "
                        f"{table_rules.max_broken_cells_ratio:.4f}"
                    ),
                )
            )

        if (
            table_rules.require_headers
            and metrics.missing_table_headers
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="TABLE_HEADER_MISSING",
                    message=(
                        "One or more Markdown tables "
                        "do not contain detectable headers."
                    ),
                )
            )

        if (
            table_rules.check_merged_cells
            and metrics.merged_cell_issues > 0
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="MERGED_CELL_ISSUE",
                    message=(
                        "Detected invalid merged-cell "
                        "structure in parsed tables."
                    ),
                )
            )

        if (
            table_rules.check_structured_cells
            and metrics.empty_cells_ratio
            > table_rules.max_empty_cells_ratio
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="TABLE_CELL_INTEGRITY_FAILED",
                    message=(
                        f"empty_cells_ratio="
                        f"{metrics.empty_cells_ratio:.4f} "
                        f"> "
                        f"{table_rules.max_empty_cells_ratio:.4f}"
                    ),
                )
            )

        if (
            table_rules.check_year_columns
            and metrics.invalid_year_cells > 0
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="TABLE_VALUE_INVALID",
                    message=(
                        f"{metrics.invalid_year_cells} value(s) in a year "
                        "column are not valid four-digit years."
                    ),
                )
            )

        return metrics, issues

    # ========================================================
    # Layer 4 — Provenance
    # ========================================================

    def _validate_document_provenance(
        self,
        metadata: DocumentMetadata,
    ) -> tuple[
        Optional[str],
        List[ContractIssue]
    ]:

        rules = (
            self.rules
            .provenance_contract
        )

        issues: List[ContractIssue] = []

        storage_path = metadata.storage_path

        if rules.require_storage_path and not storage_path:
            issues.append(
                ContractIssue(
                    layer="provenance",
                    code="STORAGE_PATH_MISSING",
                    message="storage_path is required.",
                )
            )
            return None, issues

        if not storage_path:
            return None, issues

        if not os.path.exists(storage_path):
            issues.append(
                ContractIssue(
                    layer="provenance",
                    code="SOURCE_FILE_NOT_FOUND",
                    message=(
                        f"Source file not found: {storage_path}"
                    ),
                )
            )
            return None, issues

        try:
            actual_hash = self._compute_hash(
                storage_path,
                rules.hash_algorithm,
            )
        except (
            OSError,
            ValueError,
        ) as exc:
            issues.append(
                ContractIssue(
                    layer="provenance",
                    code="SOURCE_HASH_FAILED",
                    message=str(exc),
                )
            )
            return None, issues

        if rules.require_document_hash:
            if not metadata.doc_version_hash:
                issues.append(
                    ContractIssue(
                        layer="provenance",
                        code="DOCUMENT_HASH_MISSING",
                        message=(
                            "doc_version_hash is required."
                        ),
                    )
                )
            elif (
                metadata.doc_version_hash.lower()
                != actual_hash
            ):
                issues.append(
                    ContractIssue(
                        layer="provenance",
                        code="DOCUMENT_HASH_MISMATCH",
                        message=(
                            "Provided document hash "
                            "does not match source PDF."
                        ),
                    )
                )

        return actual_hash, issues

    def _validate_chunk_provenance(
        self,
        metadata: DocumentMetadata,
        chunks: Optional[
            List[Dict[str, Any]]
        ],
        actual_hash: str,
    ) -> List[ContractIssue]:

        rules = (
            self.rules
            .provenance_contract
        )

        if not rules.require_chunk_page_number:
            return []

        if not chunks:
            return [
                ContractIssue(
                    layer="provenance",
                    code="CHUNKS_MISSING",
                    message=(
                        "Chunk-level provenance "
                        "is required."
                    ),
                )
            ]

        return self._validate_chunks(
            chunks,
            metadata.doc_id,
            actual_hash,
            rules.require_chunk_hash,
            rules.hash_algorithm,
            rules.require_provision_id,
        )

    @staticmethod
    def _compute_hash(
        file_path: str,
        algorithm: str,
    ) -> str:

        try:
            digest = hashlib.new(
                algorithm.lower()
            )
        except ValueError as exc:
            raise ValueError(
                f"Unsupported hash algorithm: {algorithm}"
            ) from exc

        with open(
            file_path,
            "rb",
        ) as f:

            while chunk := f.read(
                1024 * 1024
            ):
                digest.update(chunk)

        return digest.hexdigest()

    @staticmethod
    def _validate_chunks(
        chunks: List[Dict[str, Any]],
        doc_id: str,
        actual_hash: str,
        require_hash: bool,
        hash_algorithm: str,
        require_provision_id: bool,
    ) -> List[ContractIssue]:

        issues: List[ContractIssue] = []

        for index, raw_chunk in enumerate(
            chunks
        ):

            try:
                chunk = (
                    DocumentChunk.model_validate(
                        raw_chunk
                    )
                )

                if chunk.doc_id != doc_id:
                    issues.append(
                        ContractIssue(
                            layer="provenance",
                            code="CHUNK_DOC_ID_MISMATCH",
                            message=(
                                f"Chunk {index} belongs "
                                f"to another document."
                            ),
                        )
                    )

                if require_provision_id and not chunk.provision_id:
                    issues.append(
                        ContractIssue(
                            layer="provenance",
                            code="PROVISION_ID_MISSING",
                            message=f"Chunk {index} has no provision_id.",
                        )
                    )

                if (
                    require_hash
                    and chunk.doc_version_hash.lower()
                    != actual_hash
                ):
                    issues.append(
                        ContractIssue(
                            layer="provenance",
                            code="CHUNK_DOCUMENT_HASH_MISMATCH",
                            message=(
                                f"Chunk {index} references a different "
                                f"document version."
                            ),
                        )
                    )

                # ------------------------------------------------
                # Canonical chunk hash
                #
                # Must match chunk_builder.py exactly and use the
                # provenance contract algorithm:
                #   chunk_id
                #   doc_version_hash
                #   text
                # ------------------------------------------------
                payload = (
                    f"chunk_id={chunk.chunk_id}\n"
                    f"doc_version_hash={chunk.doc_version_hash}\n"
                    f"text={chunk.text}"
                )

                computed_chunk_hash = hashlib.new(
                    hash_algorithm,
                    payload.encode("utf-8"),
                ).hexdigest()

                if (
                    require_hash
                    and chunk.chunk_hash.lower()
                    != computed_chunk_hash
                ):
                    issues.append(
                        ContractIssue(
                            layer="provenance",
                            code="CHUNK_HASH_MISMATCH",
                            message=(
                                f"Chunk {index} has an invalid "
                                f"content hash."
                            ),
                        )
                    )

            except ValidationError as exc:

                issues.append(
                    ContractIssue(
                        layer="provenance",
                        code="CHUNK_PROVENANCE_INVALID",
                        message=(
                            f"Chunk {index}: {exc}"
                        ),
                    )
                )

        return issues

    # ========================================================
    # Layer 2 (cross-document) — Temporal relations
    # ========================================================

    @staticmethod
    def _optional_date(value: Any) -> Optional[date]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    def _validate_cross_document(
        self,
        metadata: DocumentMetadata,
        related_documents: Optional[
            Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]
        ] = None,
        relations: Optional[List[Dict[str, Any]]] = None,
    ) -> List[ContractIssue]:
        """
        Validate corpus-level relations that touch this document.

        ``relations`` uses the DocumentRelation shape
        (source_doc_id = newer document, target_doc_id = referenced
        document, relation_type in the configured relation rules).
        ``related_documents`` supplies metadata for the other end of each
        relation, either as a list of metadata dictionaries or as a
        mapping keyed by doc_id.

        Only relations in which the current document participates are
        evaluated, so the same corpus relation list can be passed to every
        document's Silver checkpoint.
        """

        rules = (
            self.rules
            .temporal_contract
            .cross_document
        )

        if not rules.enabled or not relations:
            return []

        issues: List[ContractIssue] = []

        catalog: Dict[str, Dict[str, Optional[date]]] = {
            metadata.doc_id: {
                "issued_date": metadata.issued_date,
                "effective_from": metadata.effective_from,
                "effective_to": metadata.effective_to,
            }
        }
        invalid_related: Dict[str, str] = {}

        raw_items: List[Dict[str, Any]] = []
        if isinstance(related_documents, dict):
            for doc_id, raw in related_documents.items():
                raw_items.append({"doc_id": doc_id, **(raw or {})})
        elif related_documents:
            raw_items.extend(
                item for item in related_documents
                if isinstance(item, dict) and item.get("doc_id")
            )

        # Related documents may be partial: a document cited in the text
        # ("Nghị định 168/2026/NĐ-CP ngày 30 tháng 12 năm 2026") is known
        # only by its number and issue date. Each relation rule runs only
        # when both dates it compares are known.
        for raw in raw_items:
            doc_id = str(raw["doc_id"])
            if doc_id == metadata.doc_id:
                continue
            try:
                catalog[doc_id] = {
                    key: self._optional_date(raw.get(key))
                    for key in ("issued_date", "effective_from", "effective_to")
                }
            except ValueError as exc:
                invalid_related[doc_id] = str(exc)

        for raw_relation in relations:
            try:
                relation = DocumentRelation.model_validate(raw_relation)
            except ValidationError as exc:
                issues.append(
                    ContractIssue(
                        layer="temporal",
                        code="RELATION_INVALID",
                        message=f"Invalid document relation: {exc}",
                    )
                )
                continue

            if metadata.doc_id not in (
                relation.source_doc_id,
                relation.target_doc_id,
            ):
                continue

            details = {
                "source_doc_id": relation.source_doc_id,
                "target_doc_id": relation.target_doc_id,
                "relation_type": relation.relation_type,
            }

            relation_rules = rules.relations.get(relation.relation_type)
            if relation_rules is None:
                issues.append(
                    ContractIssue(
                        layer="temporal",
                        code="RELATION_TYPE_UNSUPPORTED",
                        message=(
                            "No temporal rule is configured for relation "
                            f"type '{relation.relation_type}'."
                        ),
                        details=details,
                    )
                )
                continue

            source = catalog.get(relation.source_doc_id)
            target = catalog.get(relation.target_doc_id)

            # An endpoint known only by its number (cited without a date and
            # not present in the corpus) cannot be checked; that is not a
            # defect of the current document, so it raises no issue.
            if source is None or target is None:
                continue

            # Which document carries the defect:
            # - date-order rules: the SOURCE, which claims to amend/replace
            #   a document that is newer than itself;
            # - "still in force after being replaced": the TARGET, whose
            #   validity interval was never closed.
            is_source = metadata.doc_id == relation.source_doc_id
            is_target = metadata.doc_id == relation.target_doc_id

            # Ties are allowed: the National Assembly can pass related
            # instruments on the same day, and an amendment may take effect
            # on the same date as the provision it changes.
            def known(*values: Optional[date]) -> bool:
                return all(value is not None for value in values)

            if (
                is_source
                and relation_rules.issued_after_target
                and known(source["issued_date"], target["issued_date"])
                and source["issued_date"] < target["issued_date"]
            ):
                issues.append(
                    ContractIssue(
                        layer="temporal",
                        code="RELATION_ISSUED_ORDER_INVALID",
                        message=(
                            f"{relation.source_doc_id} "
                            f"{relation.relation_type} "
                            f"{relation.target_doc_id} but was issued "
                            f"({source['issued_date'].isoformat()}) before "
                            f"it ({target['issued_date'].isoformat()})."
                        ),
                        details=details,
                    )
                )

            if (
                is_source
                and relation_rules.effective_after_target
                and known(source["effective_from"], target["effective_from"])
                and source["effective_from"] < target["effective_from"]
            ):
                issues.append(
                    ContractIssue(
                        layer="temporal",
                        code="RELATION_EFFECTIVE_ORDER_INVALID",
                        message=(
                            f"{relation.source_doc_id} takes effect before "
                            f"the document it {relation.relation_type}."
                        ),
                        details=details,
                    )
                )

            # Only meaningful when the target's validity is actually known
            # (it has an effective_from); a bare citation stub cannot be
            # judged "still in force".
            if (
                is_target
                and relation_rules.target_must_end_by_source_effective
                and known(source["effective_from"], target["effective_from"])
                and (
                    target["effective_to"] is None
                    or target["effective_to"] > source["effective_from"]
                )
            ):
                issues.append(
                    ContractIssue(
                        layer="temporal",
                        code="REVOKED_TARGET_STILL_EFFECTIVE",
                        message=(
                            f"{relation.target_doc_id} is "
                            f"{_PAST_TENSE.get(relation.relation_type, relation.relation_type)} by "
                            f"{relation.source_doc_id} from "
                            f"{source['effective_from'].isoformat()} but its "
                            "effective_to is "
                            + (
                                target["effective_to"].isoformat()
                                if target["effective_to"]
                                else "open-ended"
                            )
                            + "."
                        ),
                        details=details,
                    )
                )

        return issues

    # ========================================================
    # Layer 3 (structure) — Silver provision hierarchy
    # ========================================================

    # Line-start article heading: "Điều 12." / "**Điều 12:**" / "## Điều 12."
    # The number must be followed by "." or ":" so that a wrapped line such
    # as "Điều 5 của Luật này ..." is not mistaken for a heading.
    _ARTICLE_HEADING = re.compile(
        r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*[ \t]*)?"
        r"dieu[ \t]+(\d+[a-z]?)[ \t]*(?:\*\*)?[ \t]*[.:]"
    )

    @staticmethod
    def _fold_diacritics(text: str) -> str:
        decomposed = unicodedata.normalize("NFD", text or "")
        stripped = "".join(
            char for char in decomposed
            if unicodedata.category(char) != "Mn"
        )
        return stripped.replace("đ", "d").replace("Đ", "D").lower()

    def _repeated_article_headings(self, content: str) -> List[str]:
        text = re.sub(r"<!--.*?-->", " ", content or "", flags=re.DOTALL)
        counts = Counter(
            match.group(1)
            for match in self._ARTICLE_HEADING.finditer(
                self._fold_diacritics(text)
            )
        )
        return sorted(
            (number for number, count in counts.items() if count > 1),
            key=lambda value: (len(value), value),
        )

    def _validate_structure(
        self,
        provisions: Optional[List[Dict[str, Any]]],
        content: Optional[str] = None,
    ) -> List[ContractIssue]:
        """
        Detect duplicated articles in the Silver provision hierarchy.

        Accepts both parser-shaped rows (``number``/``path``) and
        Silver-table rows (``provision_number``/``provision_path``).
        Returns no issue when provisions are not supplied, so callers that
        do not pass the hierarchy keep their previous behaviour.
        """

        rules = (
            self.rules
            .quality_contract
            .structure_integrity
        )

        if not rules.enabled or not provisions:
            return []

        issues: List[ContractIssue] = []

        if rules.reject_repeated_article_headings and content:
            repeated = self._repeated_article_headings(content)
            if repeated:
                issues.append(
                    ContractIssue(
                        layer="quality",
                        code="DUPLICATE_ARTICLE_HEADING",
                        message=(
                            "Article heading(s) appear more than once in "
                            "the Silver content: Điều "
                            + ", ".join(repeated[:20])
                        ),
                        details={"article_numbers": repeated[:20]},
                    )
                )

        if rules.reject_duplicate_provision_ids:
            counts = Counter(
                str(row.get("provision_id"))
                for row in provisions
                if row.get("provision_id")
            )
            duplicates = sorted(
                key for key, count in counts.items() if count > 1
            )
            if duplicates:
                issues.append(
                    ContractIssue(
                        layer="quality",
                        code="DUPLICATE_PROVISION_ID",
                        message=(
                            f"{len(duplicates)} provision_id value(s) "
                            "occur more than once."
                        ),
                        details={"provision_ids": duplicates[:20]},
                    )
                )

        articles = [
            row
            for row in provisions
            if str(row.get("level", "")).lower() == "article"
        ]

        if rules.reject_duplicate_article_numbers:
            counts = Counter(
                str(
                    row.get("number")
                    or row.get("provision_number")
                    or row.get("path")
                    or row.get("provision_path")
                    or ""
                ).strip().lower()
                for row in articles
            )
            counts.pop("", None)
            duplicates = sorted(
                key for key, count in counts.items() if count > 1
            )
            if duplicates:
                issues.append(
                    ContractIssue(
                        layer="quality",
                        code="DUPLICATE_ARTICLE_NUMBER",
                        message=(
                            "Article number(s) occur more than once: "
                            + ", ".join(duplicates[:20])
                        ),
                        details={"article_numbers": duplicates[:20]},
                    )
                )

        if rules.reject_duplicate_article_content:
            bodies: Dict[str, List[str]] = defaultdict(list)
            order: List[str] = []
            for row in provisions:
                article_key = str(
                    row.get("article_id") or row.get("provision_id") or ""
                )
                if not article_key:
                    continue
                if article_key not in bodies:
                    order.append(article_key)
                bodies[article_key].append(str(row.get("content") or ""))

            seen: Dict[str, str] = {}
            duplicate_pairs: List[List[str]] = []
            for article_key in order:
                body = " ".join(" ".join(bodies[article_key]).split())
                if len(body.split()) < rules.min_duplicate_content_tokens:
                    continue
                previous = seen.get(body)
                if previous is None:
                    seen[body] = article_key
                elif previous != article_key:
                    duplicate_pairs.append([previous, article_key])

            if duplicate_pairs:
                issues.append(
                    ContractIssue(
                        layer="quality",
                        code="DUPLICATE_ARTICLE_CONTENT",
                        message=(
                            f"{len(duplicate_pairs)} article(s) repeat the "
                            "full text of an earlier article."
                        ),
                        details={"article_pairs": duplicate_pairs[:20]},
                    )
                )

        return issues

    # ========================================================
    # Layer 4 (final integrity) — Gold chunk uniqueness
    # ========================================================

    def _validate_chunk_integrity(
        self,
        chunks: Optional[List[Dict[str, Any]]],
    ) -> List[ContractIssue]:

        rules = (
            self.rules
            .provenance_contract
        )

        if not chunks:
            return []

        issues: List[ContractIssue] = []

        if rules.reject_duplicate_chunk_ids:
            counts = Counter(
                str(chunk.get("chunk_id"))
                for chunk in chunks
                if chunk.get("chunk_id")
            )
            duplicates = sorted(
                key for key, count in counts.items() if count > 1
            )
            if duplicates:
                issues.append(
                    ContractIssue(
                        layer="provenance",
                        code="DUPLICATE_CHUNK_ID",
                        message=(
                            f"{len(duplicates)} chunk_id value(s) occur "
                            "more than once."
                        ),
                        details={"chunk_ids": duplicates[:20]},
                    )
                )

        if rules.reject_duplicate_chunk_content:
            seen: Dict[str, str] = {}
            duplicate_pairs: List[List[str]] = []
            for chunk in chunks:
                text = " ".join(str(chunk.get("text") or "").split())
                if len(text.split()) < rules.min_duplicate_chunk_tokens:
                    continue
                owner = str(
                    chunk.get("provision_id") or chunk.get("chunk_id") or ""
                )
                previous = seen.get(text)
                if previous is None:
                    seen[text] = owner
                elif previous != owner:
                    duplicate_pairs.append([previous, owner])

            if duplicate_pairs:
                issues.append(
                    ContractIssue(
                        layer="provenance",
                        code="DUPLICATE_CHUNK_CONTENT",
                        message=(
                            f"{len(duplicate_pairs)} chunk(s) of different "
                            "provisions carry identical text."
                        ),
                        details={"provision_pairs": duplicate_pairs[:20]},
                    )
                )

        return issues

    # ========================================================
    # Checkpoint engine
    # ========================================================

    def _run_checkpoint(
        self,
        metadata: Dict[str, Any],
        *,
        layers: Tuple[str, ...],
        content: Optional[str] = None,
        tables: Optional[List[Dict[str, Any]]] = None,
        chunks: Optional[List[Dict[str, Any]]] = None,
        provisions: Optional[List[Dict[str, Any]]] = None,
        related_documents: Optional[
            Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]
        ] = None,
        relations: Optional[List[Dict[str, Any]]] = None,
        check_chunks: bool = False,
        skip_chunks_on_document_failure: bool = False,
    ) -> Dict[str, Any]:
        """
        Execute the requested contract layers for one checkpoint.

        ``layers`` lists the non-schema layers enforced at this checkpoint.
        Schema normalization always runs first because every other layer
        needs normalized dates and identifiers. A schema failure stops the
        checkpoint and marks the remaining layers ``NOT_CHECKED``.

        When a layer is disabled for ablation, it is marked ``DISABLED``
        and contributes no issue. With the schema layer disabled, metadata
        that cannot be normalized cannot be checked by later layers either,
        so it passes the checkpoint with those layers ``NOT_CHECKED``.
        """

        report = ContractReport(
            status="PASSED",
            timestamp=datetime.now(timezone.utc).isoformat(),
            doc_id=metadata.get("doc_id"),
        )

        # ----------------------------------------------------
        # Layer 1 — Schema
        # ----------------------------------------------------
        normalized_metadata: Optional[DocumentMetadata] = None

        if self._layer_enabled("schema"):
            schema_result = self.validate_metadata(metadata)

            if schema_result["status"] == "QUARANTINE":
                report.status = "QUARANTINE"
                report.layer_status["schema"] = "FAILED"
                for layer in layers:
                    report.layer_status[layer] = "NOT_CHECKED"
                report.issues.extend(schema_result["issues"])
                self._write_quarantine_report(report)
                return report.model_dump()

            report.layer_status["schema"] = "PASSED"
            normalized_metadata = DocumentMetadata.model_validate(
                schema_result["metadata"]
            )
        else:
            report.layer_status["schema"] = "DISABLED"
            try:
                normalized_metadata = DocumentMetadata.model_validate(
                    metadata
                )
            except ValidationError:
                for layer in layers:
                    report.layer_status[layer] = (
                        "DISABLED"
                        if not self._layer_enabled(layer)
                        else "NOT_CHECKED"
                    )
                return report.model_dump()

        # ----------------------------------------------------
        # Layer 2 — Temporal (intra + cross document)
        # ----------------------------------------------------
        if "temporal" in layers:
            if self._layer_enabled("temporal"):
                temporal_issues = self._validate_temporal(normalized_metadata)
                temporal_issues.extend(
                    self._validate_cross_document(
                        normalized_metadata,
                        related_documents=related_documents,
                        relations=relations,
                    )
                )
                report.layer_status["temporal"] = (
                    "FAILED" if temporal_issues else "PASSED"
                )
                report.issues.extend(temporal_issues)
            else:
                report.layer_status["temporal"] = "DISABLED"

        # ----------------------------------------------------
        # Layer 3 — Quality (text, tables, structure)
        # ----------------------------------------------------
        if "quality" in layers:
            if self._layer_enabled("quality"):
                quality_metrics, quality_issues = self._validate_quality(
                    content or "",
                    tables=tables,
                )
                quality_issues.extend(
                    self._validate_structure(provisions, content)
                )
                report.quality_metrics = quality_metrics
                report.layer_status["quality"] = (
                    "FAILED" if quality_issues else "PASSED"
                )
                report.issues.extend(quality_issues)
            else:
                report.layer_status["quality"] = "DISABLED"

        # ----------------------------------------------------
        # Layer 4 — Provenance (document, chunks, final integrity)
        # ----------------------------------------------------
        if "provenance" in layers:
            if self._layer_enabled("provenance"):
                actual_hash, document_issues = (
                    self._validate_document_provenance(normalized_metadata)
                )
                report.computed_doc_version_hash = actual_hash
                report.issues.extend(document_issues)
                provenance_failed = bool(document_issues)

                if check_chunks:
                    run_chunks = actual_hash is not None and not (
                        skip_chunks_on_document_failure and document_issues
                    )
                    if run_chunks:
                        chunk_issues = self._validate_chunk_provenance(
                            normalized_metadata,
                            chunks,
                            actual_hash,
                        )
                        chunk_issues.extend(
                            self._validate_chunk_integrity(chunks)
                        )
                        report.issues.extend(chunk_issues)
                        provenance_failed = (
                            provenance_failed or bool(chunk_issues)
                        )
                    elif skip_chunks_on_document_failure:
                        # Gold cannot certify chunks without a verified
                        # source document version.
                        provenance_failed = True

                report.layer_status["provenance"] = (
                    "FAILED" if provenance_failed else "PASSED"
                )
            else:
                report.layer_status["provenance"] = "DISABLED"

        if report.issues:
            report.status = "QUARANTINE"
            self._write_quarantine_report(report)

        return report.model_dump()

    # ========================================================
    # Pre-parse checkpoint
    # ========================================================

    def validate_pre_parse(
        self,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Validate the parser-derived document metadata before
        Bronze materialization.

        Enforced layers:
            - Schema
            - Document-level Provenance

        A failure at this checkpoint means the document must be
        quarantined and must not enter Bronze.
        """

        return self._run_checkpoint(
            metadata,
            layers=("provenance",),
        )

    # ========================================================
    # Bronze checkpoint
    # ========================================================

    def validate_bronze(
        self,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Validate data after Bronze materialization.

        Enforced layers:
            - Schema
            - Document-level Provenance

        Temporal and Quality validation are intentionally deferred to
        the Silver checkpoint because Bronze remains a parsed, near-raw
        representation.
        """

        return self._run_checkpoint(
            metadata,
            layers=("provenance",),
        )

    # ========================================================
    # Silver checkpoint
    # ========================================================

    def validate_silver(
        self,
        metadata: Dict[str, Any],
        content: str,
        tables: Optional[
            List[Dict[str, Any]]
        ] = None,
        provisions: Optional[
            List[Dict[str, Any]]
        ] = None,
        related_documents: Optional[
            Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]
        ] = None,
        relations: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Validate the normalized Silver representation.

        Enforced layers:
            - Temporal (intra-document; cross-document when
              ``relations`` are supplied)
            - Quality (text and tables; provision-structure integrity
              when ``provisions`` are supplied)

        Chunk-level provenance is intentionally not evaluated here
        because chunks do not yet exist. A failure means the document
        must not be promoted from Silver to Gold.
        """

        return self._run_checkpoint(
            metadata,
            layers=("temporal", "quality"),
            content=content,
            tables=tables,
            provisions=provisions,
            related_documents=related_documents,
            relations=relations,
        )

    # ========================================================
    # Gold checkpoint
    # ========================================================

    def validate_gold(
        self,
        metadata: Dict[str, Any],
        chunks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Validate the final Gold-ready chunks.

        Enforced layer:
            - Provenance: source document re-check, chunk-level
              provenance and final integrity (unique chunk IDs, no
              duplicated chunk text across provisions)

        A failure means the chunks must not be materialized as trusted
        Gold data.
        """

        return self._run_checkpoint(
            metadata,
            layers=("provenance",),
            chunks=chunks,
            check_chunks=True,
            skip_chunks_on_document_failure=True,
        )

    # ========================================================
    # Compatibility: post-parse checkpoint
    # ========================================================

    def validate_post_parse(
        self,
        metadata: Dict[str, Any],
        content: str,
        chunks: Optional[
            List[Dict[str, Any]]
        ] = None,
        tables: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Backward-compatible single post-parse validation step.

        New orchestration should use validate_pre_parse(),
        validate_bronze(), validate_silver() and validate_gold().
        """

        return self._run_checkpoint(
            metadata,
            layers=("temporal", "quality", "provenance"),
            content=content,
            tables=tables,
            chunks=chunks,
            check_chunks=True,
        )

    # ========================================================
    # Full contract validation
    # ========================================================

    def validate(
        self,
        metadata: Dict[str, Any],
        content: str,
        chunks: Optional[
            List[Dict[str, Any]]
        ] = None,
        tables: Optional[
            List[Dict[str, Any]]
        ] = None,
        provisions: Optional[
            List[Dict[str, Any]]
        ] = None,
        related_documents: Optional[
            Union[Dict[str, Dict[str, Any]], List[Dict[str, Any]]]
        ] = None,
        relations: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Execute the complete four-layer contract in one call.

        Retained for callers that need full validation at once. The
        Medallion-aware pipeline should normally use the per-layer
        checkpoints.
        """

        return self._run_checkpoint(
            metadata,
            layers=("temporal", "quality", "provenance"),
            content=content,
            tables=tables,
            chunks=chunks,
            provisions=provisions,
            related_documents=related_documents,
            relations=relations,
            check_chunks=True,
        )

    # ========================================================
    # Quarantine persistence
    # ========================================================

    def _write_quarantine_report(
        self,
        report: ContractReport,
    ) -> None:

        if not self.write_quarantine_reports:
            return

        quarantine_dir = (
            self.config
            .db
            .quarantine_dir
        )

        os.makedirs(
            quarantine_dir,
            exist_ok=True,
        )

        safe_doc_id = re.sub(
            r"[^\w.-]+",
            "_",
            report.doc_id
            or "unknown_document",
        )

        output_path = os.path.join(
            quarantine_dir,
            f"{safe_doc_id}_contract_report.json",
        )

        with open(
            output_path,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                report.model_dump(),
                f,
                ensure_ascii=False,
                indent=2,
            )
