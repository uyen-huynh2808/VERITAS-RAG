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
    # For relations that terminate the target (replaces / repeals), the
    # target must carry an effective_to no later than the source's
    # effective_from. A target that stays open-ended after being replaced
    # is a "revoked law still presented as in force".
    target_must_end_by_source_effective: bool = False

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
    # Structured-table check: share of empty grid cells across parsed
    # tables. Rows that lost cells or misaligned merges leave holes in the
    # parser's rectangular grid even when no Markdown table is emitted.
    check_structured_cells: bool = True
    max_empty_cells_ratio: float = 0.10
    # Numeric values in a "Năm" (year) column must be four-digit years.
    check_year_columns: bool = True

class StructureIntegrityRules(BaseModel):
    """Silver-level structural checks on the provision hierarchy."""

    enabled: bool = True
    reject_duplicate_article_numbers: bool = True
    reject_duplicate_provision_ids: bool = True
    reject_duplicate_article_content: bool = True
    # A line-start heading "Điều N." occurring twice in the Silver content.
    # The parser keeps only the first heading per number, so a repeated
    # article is otherwise invisible in the provision hierarchy.
    reject_repeated_article_headings: bool = True
    # Article bodies shorter than this (in whitespace tokens) are ignored by
    # the duplicate-content rule so that short boilerplate cannot collide.
    min_duplicate_content_tokens: int = 20


class QualityRules(BaseModel):
    max_null_ratio: float = 0.05
    min_diacritic_density: float = 0.10
    max_ocr_noise_ratio: float = 0.02
    # Share of word tokens damaged by OCR character substitution
    # (e.g. "2o26", "Đ0c", "C#Ủ", "d@").
    max_corrupted_token_ratio: float = 0.02

    table_integrity: TableIntegrityRules
    structure_integrity: StructureIntegrityRules = Field(
        default_factory=StructureIntegrityRules
    )

class ProvenanceRules(BaseModel):
    hash_algorithm: str
    require_storage_path: bool = True
    require_document_hash: bool = True
    require_article_id: bool = True
    require_chunk_page_number: bool = True
    require_chunk_hash: bool = True
    require_provision_id: bool = True
    # Gold "final integrity": a chunk identity may occur only once, and two
    # chunks of different provisions must not carry identical text.
    reject_duplicate_chunk_ids: bool = True
    reject_duplicate_chunk_content: bool = True
    # Legal texts repeat boilerplate clauses/points of 20-30 words across
    # articles; only longer identical chunks indicate duplicated content.
    min_duplicate_chunk_tokens: int = 40

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
