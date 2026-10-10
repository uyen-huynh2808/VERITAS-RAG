from datetime import date
from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_validator,
)

class DocumentMetadata(BaseModel):
    """
    Canonical metadata representation for a legal document.
    """

    doc_id: str
    title: str

    issued_date: date
    effective_from: date
    effective_to: Optional[date] = None

    doc_type: Optional[str] = None
    storage_path: Optional[str] = None
    doc_version_hash: Optional[str] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError(
                "title must not be empty."
            )

        return value

class DocumentChunk(BaseModel):
    """
    Page-aware parsed text chunk.

    Provenance is intentionally stored at chunk level,
    not only at document level.
    """

    chunk_id: str
    article_id: str
    provision_id: Optional[str] = None
    provision_path: Optional[str] = None

    text: str
    doc_id: str

    page_number: int

    doc_version_hash: str
    chunk_hash: str

    @field_validator("page_number")
    @classmethod
    def validate_page_number(cls, value: int) -> int:
        if value < 1:
            raise ValueError("page_number must be >= 1.")
        return value


class ProvisionRecord(BaseModel):
    """Validated node in the Silver legal-provision hierarchy."""

    provision_id: str
    logical_doc_id: str
    article_id: str
    parent_provision_id: Optional[str] = None
    level: Literal["article", "clause", "point"]
    number: str
    path: str
    effectivity_mode: Literal[
        "inherit", "explicit_date", "external_trigger", "unknown"
    ]
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None
    trigger_reference: Optional[str] = None

    @model_validator(mode="after")
    def validate_temporal_state(self):
        if self.effectivity_mode == "explicit_date" and self.effective_from is None:
            raise ValueError("explicit_date provisions require effective_from.")
        if self.effectivity_mode == "external_trigger" and not self.trigger_reference:
            raise ValueError("external_trigger provisions require trigger_reference.")
        return self

class DocumentRelation(BaseModel):
    """
    Corpus-level relationship between two legal documents.

    source_doc_id: the newer/derived document
    target_doc_id: the referenced document
    """

    source_doc_id: str
    target_doc_id: str
    relation_type: str


class QualityMetrics(BaseModel):
    null_ratio: float = 0.0
    diacritic_density: float = 0.0
    ocr_noise_ratio: float = 0.0
    broken_cells_ratio: float = 0.0
    missing_table_headers: bool = False
    merged_cell_issues: int = 0


class ContractIssue(BaseModel):
    layer: str
    code: str
    message: str
    details: Dict[str, Any] = Field(
        default_factory=dict
    )


class ContractReport(BaseModel):
    status: str
    timestamp: str

    doc_id: Optional[str] = None

    layer_status: Dict[str, str] = Field(
        default_factory=dict
    )

    issues: List[ContractIssue] = Field(
        default_factory=list
    )

    quality_metrics: Optional[QualityMetrics] = None

    computed_doc_version_hash: Optional[str] = None
