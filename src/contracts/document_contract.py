import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from src.core.config import SystemConfig
from src.core.interfaces import BaseContractGate

from src.contracts.models import (
    ContractIssue,
    ContractReport,
    DocumentChunk,
    DocumentMetadata,
    QualityMetrics,
)

from src.contracts.quality_metrics import (
    QualityMetricsCalculator,
)

from src.contracts.rules import (
    ContractRules,
    load_contract_rules,
)


class ExecutableDocumentContract(
    BaseContractGate
):
    """
    Four-layer executable document contract.

    Layer 1: Schema
    Layer 2: Temporal
    Layer 3: Quality
    Layer 4: Provenance
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
    ):
        super().__init__(config)

        self.rules: ContractRules = (
            load_contract_rules(
                self.config.contracts.rules_path
            )
        )

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
            metrics.diacritic_ratio
            < rules.min_diacritic_ratio
        ):
            issues.append(
                ContractIssue(
                    layer="quality",
                    code="DIACRITIC_RATIO_LOW",
                    message=(
                        f"diacritic_ratio="
                        f"{metrics.diacritic_ratio:.4f} "
                        f"< "
                        f"{rules.min_diacritic_ratio:.4f}"
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

        return metrics, issues

    # ========================================================
    # Layer 4 — Provenance
    # ========================================================

    def _validate_provenance(
        self,
        metadata: DocumentMetadata,
        chunks: Optional[
            List[Dict[str, Any]]
        ],
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

        actual_hash = (
            self._compute_hash(
                storage_path,
                rules.hash_algorithm,
            )
        )

        if rules.require_document_hash:
            if not metadata.doc_version_hash:
                issues.append(
                    ContractIssue(
                        layer="provenance",
                        code="DOCUMENT_HASH_MISSING",
                        message=(
                            "doc_version_hash is required "
                            "by the provenance contract."
                        ),
                    )
                )
            elif metadata.doc_version_hash.lower() != actual_hash:
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

        if rules.require_chunk_page_number:
            if not chunks:
                issues.append(
                    ContractIssue(
                        layer="provenance",
                        code="CHUNKS_MISSING",
                        message=(
                            "Chunk-level provenance "
                            "is required."
                        ),
                    )
                )

            else:
                issues.extend(
                    self._validate_chunks(
                        chunks,
                        metadata.doc_id,
                        actual_hash,
                        rules.require_chunk_hash,
                    )
                )

        return actual_hash, issues

    @staticmethod
    def _compute_hash(
        file_path: str,
        algorithm: str,
    ) -> str:

        if algorithm.lower() != "sha256":
            raise ValueError(
                f"Unsupported hash algorithm: {algorithm}"
            )

        digest = hashlib.sha256()

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
    ) -> List[ContractIssue]:

        issues = []

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

                if (
                    require_hash
                    and chunk.doc_version_hash.lower()
                    != actual_hash
                ):
                    issues.append(
                        ContractIssue(
                            layer="provenance",
                            code="CHUNK_HASH_MISMATCH",
                            message=(
                                f"Chunk {index} has "
                                f"incorrect document hash."
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
    # Main document-level validation
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
    ) -> Dict[str, Any]:

        report = ContractReport(
            status="PASSED",
            timestamp=datetime.now(
                timezone.utc
            ).isoformat(),
            doc_id=metadata.get(
                "doc_id"
            ),
        )

        # ----------------------------------------------------
        # Layer 1 — Schema
        # ----------------------------------------------------

        schema_result = (
            self.validate_metadata(
                metadata
            )
        )

        if (
            schema_result["status"]
            == "QUARANTINE"
        ):

            report.status = "QUARANTINE"
            report.layer_status[
                "schema"
            ] = "FAILED"

            report.issues.extend(
                schema_result["issues"]
            )

            self._write_quarantine_report(
                report
            )

            return report.model_dump()

        report.layer_status[
            "schema"
        ] = "PASSED"

        normalized_metadata = (
            DocumentMetadata.model_validate(
                schema_result["metadata"]
            )
        )

        # ----------------------------------------------------
        # Layer 2 — Temporal
        # ----------------------------------------------------

        temporal_issues = (
            self._validate_temporal(
                normalized_metadata
            )
        )

        report.layer_status[
            "temporal"
        ] = (
            "FAILED"
            if temporal_issues
            else "PASSED"
        )

        report.issues.extend(
            temporal_issues
        )

        # ----------------------------------------------------
        # Layer 3 — Quality
        # ----------------------------------------------------

        quality_metrics, quality_issues = (
            self._validate_quality(
                content,
                tables=tables,
            )
        )

        report.quality_metrics = (
            quality_metrics
        )

        report.layer_status[
            "quality"
        ] = (
            "FAILED"
            if quality_issues
            else "PASSED"
        )

        report.issues.extend(
            quality_issues
        )

        # ----------------------------------------------------
        # Layer 4 — Provenance
        # ----------------------------------------------------

        actual_hash, provenance_issues = (
            self._validate_provenance(
                normalized_metadata,
                chunks,
            )
        )

        report.layer_status[
            "provenance"
        ] = (
            "FAILED"
            if provenance_issues
            else "PASSED"
        )

        report.computed_doc_version_hash = (
            actual_hash
        )

        report.issues.extend(
            provenance_issues
        )

        # ----------------------------------------------------
        # Final decision
        # ----------------------------------------------------

        if report.issues:
            report.status = "QUARANTINE"

            self._write_quarantine_report(
                report
            )

        return report.model_dump()

    # ========================================================
    # Quarantine
    # ========================================================

    def _write_quarantine_report(
        self,
        report: ContractReport,
    ) -> None:

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