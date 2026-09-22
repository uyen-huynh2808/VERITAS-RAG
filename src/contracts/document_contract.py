import hashlib
import json
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
    """
    Document-level schema contract.

    These fields describe the logical legal document, not individual
    physical PDF parts.
    """

    doc_id: str
    title: str
    issued_date: str
    effective_date: str
    doc_type: Optional[str] = None


class TemporalContractModel(BaseModel):
    """
    Intra-document temporal contract.
    """

    issued_date: date
    effective_date: date
    end_date: Optional[date] = None


class ChunkProvenanceModel(BaseModel):
    """
    Provenance information required for every parsed chunk.

    page_number:
        Continuous page number in the logical legal document.

    source_page:
        Physical page number inside the source PDF part.

    part_number:
        Ordered physical PDF part number.

    source_file:
        Physical PDF file from which the chunk originated.
    """

    page_number: int = Field(ge=1)
    source_page: int = Field(ge=1)
    part_number: int = Field(ge=1)
    source_file: str


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

    Multipart legal documents are supported.

    Example:

        Logical document:
            Part 1 PDF -> pages 1..50
            Part 2 PDF -> pages 51..100

        Physical provenance:
            part_number=1, source_page=1 -> page_number=1
            part_number=2, source_page=1 -> page_number=51

    This class enforces rules only.
    It does not perform parsing, retrieval, lineage,
    or cross-document relation discovery.
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
    ):
        super().__init__(config=config)

        self.rules = self._load_rules()
        self.quality_calculator = QualityMetricsCalculator()

    # ========================================================
    # Backward-compatible public API
    # ========================================================

    def validate(
        self,
        metadata: Dict[str, Any],
        content: str,
    ) -> Dict[str, Any]:
        """
        Backward-compatible validation entry point.

        New code should prefer:
            validate_metadata()
            validate_parsed_document()

        For backward compatibility, parsed chunks and PDF paths can be
        supplied in metadata under:
            metadata["_chunks"]
            metadata["_pdf_paths"]
        """

        pdf_paths = metadata.get("_pdf_paths", [])
        chunks = metadata.get("_chunks", [])

        if isinstance(pdf_paths, str):
            pdf_paths = [pdf_paths]

        if not pdf_paths:
            # At minimum, perform the schema gate so callers that only
            # possess metadata still receive a meaningful validation report.
            return self.validate_metadata(metadata)

        return self.validate_parsed_document(
            metadata=metadata,
            content=content,
            chunks=chunks,
            pdf_paths=pdf_paths,
        )

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

        Used before the logical document enters Bronze.
        """

        report = self._base_report()

        try:
            self._validate_schema(metadata)

            report["status"] = "PASSED"
            report["validated_layers"] = ["schema"]

        except ContractViolation as exc:
            return self._quarantine(
                report=report,
                layer=exc.layer,
                error=str(exc),
            )

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
        pdf_paths: List[str],
    ) -> Dict[str, Any]:
        """
        Post-parsing Data Contract gate.

        Executes:
            Layer 2: Temporal
            Layer 3: Quality
            Layer 4: Provenance

        A logical document may be represented by one or multiple
        physical PDF files.
        """

        report = self._base_report()

        try:
            # --------------------------------------------------
            # Layer 2 — Temporal
            # --------------------------------------------------
            self._validate_temporal(metadata)
            report["validated_layers"].append("temporal")

            # --------------------------------------------------
            # Layer 3 — Quality
            # --------------------------------------------------
            quality_metrics = self.quality_calculator.calculate(
                content
            )

            self._validate_quality(quality_metrics)

            report["quality_metrics"] = (
                self._metrics_to_dict(quality_metrics)
            )
            report["validated_layers"].append("quality")

            # --------------------------------------------------
            # Layer 4 — Provenance
            # --------------------------------------------------
            provenance_report = self._validate_provenance(
                metadata=metadata,
                chunks=chunks,
                pdf_paths=pdf_paths,
            )

            report["provenance"] = provenance_report
            report["validated_layers"].append("provenance")

            report["status"] = "PASSED"

        except ContractViolation as exc:
            return self._quarantine(
                report=report,
                layer=exc.layer,
                error=str(exc),
            )

        except Exception as exc:
            return self._quarantine(
                report=report,
                layer="provenance",
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
                message=f"Missing required fields: {missing}",
            )

        doc_id = str(metadata["doc_id"])

        pattern = rules["doc_id_pattern"]

        if not re.fullmatch(
            pattern,
            doc_id,
        ):
            raise ContractViolation(
                layer="schema",
                message=(
                    "Invalid Vietnamese legal document ID: "
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

        if rules.get(
            "start_on_or_after_issued",
            True,
        ):
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
                    "Null/corruption ratio "
                    f"{metrics.null_ratio:.4f} exceeds "
                    "maximum "
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
                    "Vietnamese diacritic integrity "
                    f"{metrics.diacritic_ratio:.4f} is below "
                    "minimum "
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
                    "OCR noise ratio "
                    f"{metrics.ocr_noise_ratio:.4f} exceeds "
                    "maximum "
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
                message=(
                    "Markdown table is missing a valid "
                    "header separator."
                ),
            )

        if (
            metrics.broken_cells_ratio
            > table_rules["max_broken_cells_ratio"]
        ):
            raise ContractViolation(
                layer="quality",
                message=(
                    "Broken table-cell ratio "
                    f"{metrics.broken_cells_ratio:.4f} exceeds "
                    "maximum "
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
        pdf_paths: List[str],
    ) -> Dict[str, Any]:
        """
        Validate provenance for one logical document.

        For multipart documents:
            PDF part 1 -> SHA-256 A
            PDF part 2 -> SHA-256 B

        A deterministic logical-document hash is then created from the
        ordered part hashes.

        For a single PDF, the logical-document hash remains the original
        file SHA-256 for backward compatibility.
        """

        rules = self.rules["provenance_contract"]

        normalized_paths = self._normalize_pdf_paths(
            pdf_paths
        )

        # ------------------------------------------------------
        # 1. Validate source paths
        # ------------------------------------------------------
        if not normalized_paths:
            raise ContractViolation(
                layer="provenance",
                message="At least one source PDF is required.",
            )

        self._validate_storage_paths(
            metadata=metadata,
            rules=rules,
        )

        for pdf_path in normalized_paths:
            if not pdf_path.exists():
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        f"Source PDF does not exist: {pdf_path}"
                    ),
                )

            if not pdf_path.is_file():
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        f"Source path is not a file: {pdf_path}"
                    ),
                )

        # ------------------------------------------------------
        # 2. Calculate SHA-256 for every physical PDF
        # ------------------------------------------------------
        part_hashes = [
            self._compute_sha256(str(pdf_path))
            for pdf_path in normalized_paths
        ]

        # ------------------------------------------------------
        # 3. Validate optionally declared per-part hashes
        # ------------------------------------------------------
        declared_part_hashes = metadata.get(
            "part_hashes"
        )

        if declared_part_hashes is not None:

            if not isinstance(
                declared_part_hashes,
                list,
            ):
                raise ContractViolation(
                    layer="provenance",
                    message="part_hashes must be a list.",
                )

            if len(declared_part_hashes) != len(
                part_hashes
            ):
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        "Number of declared part_hashes does "
                        "not match the number of source PDFs."
                    ),
                )

            for index, (
                declared_hash,
                actual_hash,
            ) in enumerate(
                zip(
                    declared_part_hashes,
                    part_hashes,
                ),
                start=1,
            ):
                if declared_hash != actual_hash:
                    raise ContractViolation(
                        layer="provenance",
                        message=(
                            f"SHA-256 mismatch for PDF part "
                            f"{index}."
                        ),
                    )

        # ------------------------------------------------------
        # 4. Build logical-document version hash
        # ------------------------------------------------------
        document_hash = (
            part_hashes[0]
            if len(part_hashes) == 1
            else self._compute_logical_document_hash(
                part_hashes
            )
        )

        declared_document_hash = metadata.get(
            "doc_version_hash"
        )

        if declared_document_hash:
            if declared_document_hash != document_hash:
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        "Declared doc_version_hash does not "
                        "match the logical-document SHA-256."
                    ),
                )

        # ------------------------------------------------------
        # 5. Validate chunks
        # ------------------------------------------------------
        if not chunks:
            raise ContractViolation(
                layer="provenance",
                message="Parsed document contains no chunks.",
            )

        self._validate_chunk_provenance(
            chunks=chunks,
            pdf_paths=normalized_paths,
        )

        # ------------------------------------------------------
        # 6. Store normalized provenance in metadata
        # ------------------------------------------------------
        metadata["part_hashes"] = part_hashes
        metadata["doc_version_hash"] = document_hash
        metadata["source_files"] = [
            str(path)
            for path in normalized_paths
        ]
        metadata["part_count"] = len(
            normalized_paths
        )

        return {
            "doc_version_hash": document_hash,
            "part_count": len(normalized_paths),
            "part_hashes": part_hashes,
            "source_files": [
                str(path)
                for path in normalized_paths
            ],
        }

    # ========================================================
    # Provenance helpers
    # ========================================================

    @staticmethod
    def _normalize_pdf_paths(
        pdf_paths: List[str],
    ) -> List[Path]:
        if isinstance(
            pdf_paths,
            str,
        ):
            pdf_paths = [pdf_paths]

        normalized_paths: List[Path] = []

        for pdf_path in pdf_paths:
            path = Path(pdf_path)

            normalized_paths.append(
                path.resolve()
            )

        return normalized_paths

    @staticmethod
    def _validate_storage_paths(
        metadata: Dict[str, Any],
        rules: Dict[str, Any],
    ) -> None:
        """
        Multipart-aware storage-path validation.

        Accepted forms:
            storage_path: ".../document.pdf"

        or:
            storage_paths: [
                ".../part1.pdf",
                ".../part2.pdf"
            ]

        For parser-produced metadata, source_files are also accepted
        as the physical provenance paths.
        """

        if not rules.get(
            "require_storage_path",
            True,
        ):
            return

        storage_paths = metadata.get(
            "storage_paths"
        )

        storage_path = metadata.get(
            "storage_path"
        )

        source_files = metadata.get(
            "source_files"
        )

        if storage_paths:
            return

        if storage_path:
            return

        if source_files:
            return

        raise ContractViolation(
            layer="provenance",
            message="storage_path is required.",
        )

    def _validate_chunk_provenance(
        self,
        chunks: List[Dict[str, Any]],
        pdf_paths: List[Path],
    ) -> None:
        """
        Validate chunk-level provenance.

        Every chunk must identify:
            - logical page_number
            - physical source_page
            - part_number
            - source_file

        For multipart documents, page_number must continue across parts.
        """

        allowed_source_files = {
            str(path.resolve())
            for path in pdf_paths
        }

        part_to_chunks: Dict[
            int,
            List[Dict[str, Any]]
        ] = {}

        previous_document_page = 0

        for index, chunk in enumerate(chunks):

            try:
                provenance = ChunkProvenanceModel(
                    page_number=chunk.get(
                        "page_number"
                    ),
                    source_page=chunk.get(
                        "source_page"
                    ),
                    part_number=chunk.get(
                        "part_number"
                    ),
                    source_file=chunk.get(
                        "source_file"
                    ),
                )

            except ValidationError as exc:
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        f"Invalid provenance for chunk "
                        f"{index}: {exc}"
                    ),
                ) from exc

            source_file = str(
                Path(
                    provenance.source_file
                ).resolve()
            )

            if source_file not in allowed_source_files:
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        f"Chunk {index} references unknown "
                        f"source_file: "
                        f"{provenance.source_file}"
                    ),
                )

            if (
                provenance.part_number
                > len(pdf_paths)
            ):
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        f"Chunk {index} has part_number="
                        f"{provenance.part_number}, but only "
                        f"{len(pdf_paths)} PDF part(s) were supplied."
                    ),
                )

            if (
                provenance.page_number
                < previous_document_page
            ):
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        f"Logical page numbering is not "
                        f"monotonic at chunk {index}."
                    ),
                )

            previous_document_page = (
                provenance.page_number
            )

            part_to_chunks.setdefault(
                provenance.part_number,
                [],
            ).append(
                {
                    "page_number": provenance.page_number,
                    "source_page": provenance.source_page,
                }
            )

        # ------------------------------------------------------
        # Validate part continuity and page offset.
        #
        # Example:
        #   Part 1: source pages 1..50 -> logical pages 1..50
        #   Part 2: source pages 1..50 -> logical pages 51..100
        # ------------------------------------------------------
        sorted_parts = sorted(
            part_to_chunks.keys()
        )

        expected_parts = list(
            range(
                1,
                len(pdf_paths) + 1,
            )
        )

        if sorted_parts != expected_parts:
            raise ContractViolation(
                layer="provenance",
                message=(
                    "PDF parts represented in chunks are not "
                    "contiguous. Expected parts "
                    f"{expected_parts}, got {sorted_parts}."
                ),
            )

        previous_part_last_page = 0

        for part_number in expected_parts:
            part_chunks = part_to_chunks[
                part_number
            ]

            part_chunks.sort(
                key=lambda item: item["source_page"]
            )

            first_source_page = part_chunks[0][
                "source_page"
            ]

            first_document_page = part_chunks[0][
                "page_number"
            ]

            # Every part should start from physical page 1.
            if first_source_page != 1:
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        f"PDF part {part_number} does not "
                        "start at source page 1."
                    ),
                )

            if part_number == 1:
                expected_document_start = 1
            else:
                expected_document_start = (
                    previous_part_last_page + 1
                )

            if (
                first_document_page
                != expected_document_start
            ):
                raise ContractViolation(
                    layer="provenance",
                    message=(
                        f"Logical page numbering is discontinuous "
                        f"at PDF part {part_number}: expected "
                        f"page {expected_document_start}, got "
                        f"{first_document_page}."
                    ),
                )

            previous_part_last_page = max(
                item["page_number"]
                for item in part_chunks
            )

    # ========================================================
    # Hash helpers
    # ========================================================

    @staticmethod
    def _compute_sha256(
        pdf_path: str,
        chunk_size: int = 1024 * 1024,
    ) -> str:
        """
        Compute SHA-256 using streaming I/O.
        """

        digest = hashlib.sha256()

        with open(
            pdf_path,
            "rb",
        ) as f:

            while True:
                chunk = f.read(
                    chunk_size
                )

                if not chunk:
                    break

                digest.update(chunk)

        return digest.hexdigest()

    @staticmethod
    def _compute_logical_document_hash(
        part_hashes: List[str],
    ) -> str:
        """
        Build a deterministic hash for a multipart logical document.

        The order of the physical PDF parts matters.

        Example:
            hash(part1) + hash(part2)
                ↓
            SHA-256
                ↓
            logical document version hash
        """

        digest = hashlib.sha256()

        for part_hash in part_hashes:
            digest.update(
                part_hash.encode("ascii")
            )

        return digest.hexdigest()

    # ========================================================
    # Helpers
    # ========================================================

    @staticmethod
    def _base_report() -> Dict[str, Any]:
        return {
            "status": "PENDING",
            "timestamp": datetime.now(
                timezone.utc
            ).isoformat(),
            "validated_layers": [],
            "quality_metrics": None,
            "provenance": None,
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
            "broken_cells_ratio": (
                metrics.broken_cells_ratio
            ),
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