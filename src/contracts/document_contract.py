import hashlib
import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError

from src.contracts.quality_metrics import (
    QualityMetrics,
    QualityMetricsCalculator,
)
from src.core.config import SystemConfig
from src.core.interfaces import BaseContractGate


# ============================================================
# Contract models
# ============================================================

class SchemaContractModel(BaseModel):
    doc_id: str
    title: str
    issued_date: str
    effective_date: str
    doc_type: Optional[str] = None


class TemporalContractModel(BaseModel):
    issued_date: date
    effective_date: date
    end_date: Optional[date] = None


class ProvenanceContractModel(BaseModel):
    doc_version_hash: str
    storage_path: str
    page_number: int = Field(ge=1)


# ============================================================
# Executable Contract
# ============================================================

class ExecutableDocumentContract(BaseContractGate):
    """
    Four-layer executable Data Contract gate.

    Responsibilities:
        1. Schema Contract
        2. Temporal Contract
        3. Quality Contract
        4. Provenance Contract

    This class enforces rules only.
    It does not perform parsing, retrieval, lineage, or
    cross-document relation discovery.
    """

    def __init__(self, config: Optional[SystemConfig] = None):
        super().__init__(config=config)

        self.rules = self._load_rules()
        self.quality_calculator = QualityMetricsCalculator()

    # ========================================================
    # Rules
    # ========================================================

    def _load_rules(self) -> Dict[str, Any]:

        rules_path = Path(self.config.contracts.rules_path)

        if not rules_path.exists():
            raise FileNotFoundError(
                f"Contract rules not found: {rules_path}"
            )

        with rules_path.open(
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    # ========================================================
    # Public API
    # ========================================================

    def validate_metadata(
        self,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Pre-ingestion Schema Contract gate.

        Used before the PDF enters Bronze.
        """

        report = self._base_report()

        try:
            self._validate_schema(metadata)

            report["status"] = "PASSED"
            report["validated_layers"] = ["schema"]

        except Exception as exc:

            return self._quarantine(
                report=report,
                layer="schema",
                error=str(exc),
            )

        return report

    def validate_parsed_document(
        self,
        metadata: Dict[str, Any],
        content: str,
        chunks: List[Dict[str, Any]],
        pdf_path: str,
    ) -> Dict[str, Any]:
        """
        Post-parsing Data Contract gate.

        Executes:
            Layer 2: Temporal
            Layer 3: Quality
            Layer 4: Provenance
        """

        report = self._base_report()

        try:
            self._validate_temporal(metadata)
            report["validated_layers"].append("temporal")

            quality_metrics = (
                self.quality_calculator.calculate(content)
            )

            self._validate_quality(quality_metrics)
            report["quality_metrics"] = (
                self._metrics_to_dict(quality_metrics)
            )
            report["validated_layers"].append("quality")

            self._validate_provenance(
                metadata=metadata,
                chunks=chunks,
                pdf_path=pdf_path,
            )
            report["validated_layers"].append("provenance")

            report["status"] = "PASSED"

        except ContractViolation as exc:

            return self._quarantine(
                report=report,
                layer=exc.layer,
                error=str(exc),
            )

        return report

    # ========================================================
    # Layer 1 — Schema
    # ========================================================

    def _validate_schema(
        self,
        metadata: Dict[str, Any],
    ) -> None:

        rules = self.rules["schema_contract"]

        required_fields = rules["required_fields"]

        missing = [
            field
            for field in required_fields
            if not metadata.get(field)
        ]

        if missing:
            raise ContractViolation(
                layer="schema",
                message=(
                    f"Missing required fields: {missing}"
                ),
            )

        doc_id = str(metadata["doc_id"])

        pattern = rules["doc_id_pattern"]

        if not re.fullmatch(pattern, doc_id):
            raise ContractViolation(
                layer="schema",
                message=(
                    f"Invalid Vietnamese legal document ID: "
                    f"{doc_id}"
                ),
            )

        allowed_types = rules.get(
            "allowed_doc_types",
            [],
        )

        doc_type = metadata.get("doc_type")

        if doc_type and doc_type not in allowed_types:
            raise ContractViolation(
                layer="schema",
                message=(
                    f"Unsupported document type: {doc_type}"
                ),
            )

        try:
            SchemaContractModel(
                doc_id=metadata["doc_id"],
                title=metadata["title"],
                issued_date=metadata["issued_date"],
                effective_date=metadata["effective_date"],
                doc_type=metadata.get("doc_type"),
            )
        except ValidationError as exc:
            raise ContractViolation(
                layer="schema",
                message=str(exc),
            ) from exc

    # ========================================================
    # Layer 2 — Temporal
    # ========================================================

    def _validate_temporal(
        self,
        metadata: Dict[str, Any],
    ) -> None:

        rules = self.rules["temporal_contract"]["rules"]

        try:
            model = TemporalContractModel(
                issued_date=metadata["issued_date"],
                effective_date=metadata["effective_date"],
                end_date=metadata.get("end_date"),
            )
        except ValidationError as exc:
            raise ContractViolation(
                layer="temporal",
                message=str(exc),
            ) from exc

        if rules.get("start_on_or_after_issued", True):

            if model.effective_date < model.issued_date:
                raise ContractViolation(
                    layer="temporal",
                    message=(
                        "effective_date must be greater than "
                        "or equal to issued_date."
                    ),
                )

        if (
            rules.get("end_after_start", True)
            and model.end_date is not None
        ):

            if model.end_date <= model.effective_date:
                raise ContractViolation(
                    layer="temporal",
                    message=(
                        "end_date must be strictly greater "
                        "than effective_date."
                    ),
                )

    # ========================================================
    # Layer 3 — Quality
    # ========================================================

    def _validate_quality(
        self,
        metrics: QualityMetrics,
    ) -> None:

        rules = self.rules["quality_contract"]

        if (
            metrics.null_ratio
            > rules["max_null_ratio"]
        ):
            raise ContractViolation(
                layer="quality",
                message=(
                    f"Null/corruption ratio "
                    f"{metrics.null_ratio:.4f} exceeds "
                    f"maximum "
                    f"{rules['max_null_ratio']:.4f}."
                ),
            )

        if (
            metrics.diacritic_ratio
            < rules["min_diacritic_ratio"]
        ):
            raise ContractViolation(
                layer="quality",
                message=(
                    f"Vietnamese diacritic integrity "
                    f"{metrics.diacritic_ratio:.4f} is below "
                    f"minimum "
                    f"{rules['min_diacritic_ratio']:.4f}."
                ),
            )

        if (
            metrics.ocr_noise_ratio
            > rules["max_ocr_noise_ratio"]
        ):
            raise ContractViolation(
                layer="quality",
                message=(
                    f"OCR noise ratio "
                    f"{metrics.ocr_noise_ratio:.4f} exceeds "
                    f"maximum "
                    f"{rules['max_ocr_noise_ratio']:.4f}."
                ),
            )

        table_rules = rules["table_integrity"]

        if (
            table_rules["require_headers"]
            and metrics.missing_table_headers
        ):
            raise ContractViolation(
                layer="quality",
                message="Markdown table is missing a valid header separator.",
            )

        if (
            metrics.broken_cells_ratio
            > table_rules["max_broken_cells_ratio"]
        ):
            raise ContractViolation(
                layer="quality",
                message=(
                    f"Broken table-cell ratio "
                    f"{metrics.broken_cells_ratio:.4f} exceeds "
                    f"maximum "
                    f"{table_rules['max_broken_cells_ratio']:.4f}."
                ),
            )

    # ========================================================
    # Layer 4 — Provenance
    # ========================================================

    def _validate_provenance(
        self,
        metadata: Dict[str, Any],
        chunks: List[Dict[str, Any]],
        pdf_path: str,
    ) -> None:

        rules = self.rules["provenance_contract"]

        if not metadata.get("storage_path"):
            if rules["require_storage_path"]:
                raise ContractViolation(
                    layer="provenance",
                    message="storage_path is required.",
                )

        actual_hash = self._compute_sha256(pdf_path)

        declared_hash = metadata.get(
            "doc_version_hash"
        )

        if declared_hash:
            if declared_hash != actual_hash:
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        "Declared doc_version_hash does not "
                        "match the SHA-256 hash of the original PDF."
                    ),
                )

        if not chunks:
            raise ContractViolation(
                layer="provenance",
                message="Parsed document contains no chunks.",
            )

        if rules.get(
            "require_chunk_page_number",
            True,
        ):

            for index, chunk in enumerate(chunks):

                page_number = chunk.get("page_number")

                if (
                    not isinstance(page_number, int)
                    or page_number < 1
                ):
                    raise ContractViolation(
                        layer="provenance",
                        message=(
                            f"Chunk {index} is missing a valid "
                            f"page_number."
                        ),
                    )

        if metadata.get("doc_version_hash") is None:
            metadata["doc_version_hash"] = actual_hash

    # ========================================================
    # Helpers
    # ========================================================

    @staticmethod
    def _compute_sha256(
        pdf_path: str,
        chunk_size: int = 1024 * 1024,
    ) -> str:

        digest = hashlib.sha256()

        with open(pdf_path, "rb") as f:

            while True:
                chunk = f.read(chunk_size)

                if not chunk:
                    break

                digest.update(chunk)

        return digest.hexdigest()

    @staticmethod
    def _base_report() -> Dict[str, Any]:

        return {
            "status": "PENDING",
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
            "validated_layers": [],
            "quality_metrics": None,
            "failed_layer": None,
            "error": None,
        }

    @staticmethod
    def _metrics_to_dict(
        metrics: QualityMetrics,
    ) -> Dict[str, Any]:

        return {
            "null_ratio": metrics.null_ratio,
            "diacritic_ratio": metrics.diacritic_ratio,
            "ocr_noise_ratio": metrics.ocr_noise_ratio,
            "broken_cells_ratio": metrics.broken_cells_ratio,
            "missing_table_headers": (
                metrics.missing_table_headers
            ),
        }

    @staticmethod
    def _quarantine(
        report: Dict[str, Any],
        layer: str,
        error: str,
    ) -> Dict[str, Any]:

        report["status"] = "QUARANTINE"
        report["failed_layer"] = layer
        report["error"] = error

        return report


class ContractViolation(Exception):
    def __init__(
        self,
        layer: str,
        message: str,
    ):
        self.layer = layer
        super().__init__(message)