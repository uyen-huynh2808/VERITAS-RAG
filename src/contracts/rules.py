import json
import os
from typing import Dict, List

from pydantic import BaseModel, Field


class SchemaRules(BaseModel):
    doc_id_pattern: str
    required_fields: List[str]
    allowed_doc_types: List[str] = Field(
        default_factory=list
    )


class IntraTemporalRules(BaseModel):
    enabled: bool = True
    effective_from_on_or_after_issued: bool = True
    effective_to_after_effective_from: bool = True


class CrossDocumentRelationRules(BaseModel):
    issued_after_target: bool = True
    effective_after_target: bool = True


class CrossDocumentTemporalRules(BaseModel):
    enabled: bool = True

    relations: Dict[
        str,
        CrossDocumentRelationRules
    ] = Field(default_factory=dict)


class TemporalRules(BaseModel):
    intra_document: IntraTemporalRules
    cross_document: CrossDocumentTemporalRules


class TableIntegrityRules(BaseModel):
    require_headers: bool = True
    max_broken_cells_ratio: float = 0.05
    check_merged_cells: bool = True


class QualityRules(BaseModel):
    max_null_ratio: float = 0.05
    min_diacritic_ratio: float = 0.85
    max_ocr_noise_ratio: float = 0.02

    table_integrity: TableIntegrityRules


class ProvenanceRules(BaseModel):
    hash_algorithm: str = "sha256"
    require_storage_path: bool = True
    require_document_hash: bool = True
    require_chunk_page_number: bool = True
    require_chunk_hash: bool = True


class ContractRules(BaseModel):
    schema_contract: SchemaRules
    temporal_contract: TemporalRules
    quality_contract: QualityRules
    provenance_contract: ProvenanceRules


def load_contract_rules(
    path: str
) -> ContractRules:

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Contract rules not found: {path}"
        )

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as f:
        raw = json.load(f)

    return ContractRules.model_validate(raw)