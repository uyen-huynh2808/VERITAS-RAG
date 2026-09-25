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
        )

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
                # Must match chunk_builder.py exactly:
                #   chunk_id
                #   doc_version_hash
                #   text
                # ------------------------------------------------
                payload = (
                    f"chunk_id={chunk.chunk_id}\n"
                    f"doc_version_hash={chunk.doc_version_hash}\n"
                    f"text={chunk.text}"
                )

                computed_chunk_hash = hashlib.sha256(
                    payload.encode("utf-8")
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
    # Pre-parse checkpoint
    # ========================================================

    def validate_pre_parse(
        self,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Validate the document before parsing.

        Enforced layers:
            - Schema
            - Document-level Provenance

        A failure at this checkpoint means the document must be
        quarantined and must not enter Bronze.
        """

        report = ContractReport(
            status="PASSED",
            timestamp=datetime.now(
                timezone.utc
            ).isoformat(),
            doc_id=metadata.get(
                "doc_id"
            ),
        )

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

            report.layer_status[
                "provenance"
            ] = "NOT_CHECKED"

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

        actual_hash, provenance_issues = (
            self._validate_document_provenance(
                normalized_metadata
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

        if report.issues:
            report.status = "QUARANTINE"

            self._write_quarantine_report(
                report
            )

        return report.model_dump()

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

        This checkpoint verifies that the persisted Bronze
        representation still satisfies the document-level
        contract.

        Temporal and Quality validation are intentionally
        deferred to the Silver checkpoint because Bronze
        remains a parsed, near-raw representation.
        """

        report = ContractReport(
            status="PASSED",
            timestamp=datetime.now(
                timezone.utc
            ).isoformat(),
            doc_id=metadata.get(
                "doc_id"
            ),
        )

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

            report.layer_status[
                "provenance"
            ] = "NOT_CHECKED"

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

        actual_hash, provenance_issues = (
            self._validate_document_provenance(
                normalized_metadata
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

        if report.issues:
            report.status = "QUARANTINE"

            self._write_quarantine_report(
                report
            )

        return report.model_dump()

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
    ) -> Dict[str, Any]:
        """
        Validate the normalized Silver representation.

        Enforced layers:
            - Temporal
            - Quality

        Chunk-level provenance is intentionally not evaluated
        here because chunks do not yet exist.

        A failure means the document must not be promoted
        from Silver to Gold.
        """

        report = ContractReport(
            status="PASSED",
            timestamp=datetime.now(
                timezone.utc
            ).isoformat(),
            doc_id=metadata.get(
                "doc_id"
            ),
        )

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

            report.layer_status[
                "temporal"
            ] = "NOT_CHECKED"

            report.layer_status[
                "quality"
            ] = "NOT_CHECKED"

            report.issues.extend(
                schema_result["issues"]
            )

            self._write_quarantine_report(
                report
            )

            return report.model_dump()

        normalized_metadata = (
            DocumentMetadata.model_validate(
                schema_result["metadata"]
            )
        )

        report.layer_status[
            "schema"
        ] = "PASSED"

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

        if report.issues:
            report.status = "QUARANTINE"

            self._write_quarantine_report(
                report
            )

        return report.model_dump()

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
            - Chunk-level Provenance

        Gold is the first checkpoint at which chunk-level
        provenance can be evaluated because chunks are created
        only after Silver normalization.

        A failure means the chunks must not be materialized
        as trusted Gold data.
        """

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
        # Schema normalization
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

            report.layer_status[
                "provenance"
            ] = "NOT_CHECKED"

            report.issues.extend(
                schema_result["issues"]
            )

            self._write_quarantine_report(
                report
            )

            return report.model_dump()

        normalized_metadata = (
            DocumentMetadata.model_validate(
                schema_result["metadata"]
            )
        )

        report.layer_status[
            "schema"
        ] = "PASSED"

        # ----------------------------------------------------
        # Layer 4 — Document Provenance
        #
        # Re-check the source document before validating
        # chunk-level provenance.
        # ----------------------------------------------------

        actual_hash, document_provenance_issues = (
            self._validate_document_provenance(
                normalized_metadata
            )
        )

        report.computed_doc_version_hash = (
            actual_hash
        )

        report.issues.extend(
            document_provenance_issues
        )

        if document_provenance_issues:
            report.layer_status[
                "provenance"
            ] = "FAILED"

        elif actual_hash is not None:
            chunk_provenance_issues = (
                self._validate_chunk_provenance(
                    normalized_metadata,
                    chunks,
                    actual_hash,
                )
            )

            report.issues.extend(
                chunk_provenance_issues
            )

            report.layer_status[
                "provenance"
            ] = (
                "FAILED"
                if chunk_provenance_issues
                else "PASSED"
            )

        else:
            report.layer_status[
                "provenance"
            ] = "FAILED"

        # ----------------------------------------------------
        # Final Gold decision
        # ----------------------------------------------------

        if report.issues:
            report.status = "QUARANTINE"

            self._write_quarantine_report(
                report
            )

        return report.model_dump()

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
        Backward-compatible post-parse validation.

        The new pipeline should NOT use this method as its main
        orchestration checkpoint.

        New orchestration should use:

            validate_pre_parse()
            validate_bronze()
            validate_silver()
            validate_gold()

        This compatibility method preserves the previous
        behavior for existing callers that still expect a
        single post-parse validation step.
        """

        report = ContractReport(
            status="PASSED",
            timestamp=datetime.now(
                timezone.utc
            ).isoformat(),
            doc_id=metadata.get(
                "doc_id"
            ),
        )

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

        normalized_metadata = (
            DocumentMetadata.model_validate(
                schema_result["metadata"]
            )
        )

        report.layer_status[
            "schema"
        ] = "PASSED"

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

        actual_hash, document_provenance_issues = (
            self._validate_document_provenance(
                normalized_metadata
            )
        )

        report.layer_status[
            "provenance"
        ] = (
            "FAILED"
            if document_provenance_issues
            else "PASSED"
        )

        report.computed_doc_version_hash = (
            actual_hash
        )

        report.issues.extend(
            document_provenance_issues
        )

        if actual_hash is not None:
            chunk_provenance_issues = (
                self._validate_chunk_provenance(
                    normalized_metadata,
                    chunks,
                    actual_hash,
                )
            )

            report.issues.extend(
                chunk_provenance_issues
            )

            if chunk_provenance_issues:
                report.layer_status[
                    "provenance"
                ] = "FAILED"

        if report.issues:
            report.status = "QUARANTINE"

            self._write_quarantine_report(
                report
            )

        return report.model_dump()

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
    ) -> Dict[str, Any]:
        """
        Execute the complete four-layer contract.

        This method is retained for compatibility with callers
        that need full validation in one call.

        The Medallion-aware pipeline should normally use:

            validate_pre_parse()
            validate_bronze()
            validate_silver()
            validate_gold()
        """

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

        actual_hash, document_provenance_issues = (
            self._validate_document_provenance(
                normalized_metadata
            )
        )

        report.layer_status[
            "provenance"
        ] = (
            "FAILED"
            if document_provenance_issues
            else "PASSED"
        )

        report.computed_doc_version_hash = (
            actual_hash
        )

        report.issues.extend(
            document_provenance_issues
        )

        if actual_hash is not None:
            chunk_provenance_issues = (
                self._validate_chunk_provenance(
                    normalized_metadata,
                    chunks,
                    actual_hash,
                )
            )

            report.issues.extend(
                chunk_provenance_issues
            )

            if chunk_provenance_issues:
                report.layer_status[
                    "provenance"
                ] = "FAILED"

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