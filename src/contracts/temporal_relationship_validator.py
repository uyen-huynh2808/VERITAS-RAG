from typing import Any, Dict, List

from src.contracts.models import (
    DocumentMetadata,
    DocumentRelation,
)
from src.contracts.rules import (
    CrossDocumentTemporalRules,
)


class CrossDocumentTemporalValidator:
    """
    Validates temporal constraints between legal documents.

    This validator operates at corpus level and therefore
    is intentionally separated from the document contract.
    """

    def __init__(
        self,
        rules: CrossDocumentTemporalRules,
    ):
        self.rules = rules

    def validate(
        self,
        documents: List[Dict[str, Any]],
        relations: List[Dict[str, Any]],
    ) -> Dict[str, Any]:

        if not self.rules.enabled:
            return {
                "status": "SKIPPED",
                "violations": [],
            }

        document_map = {}

        for raw_document in documents:
            metadata = DocumentMetadata.model_validate(
                raw_document
            )

            document_map[metadata.doc_id] = metadata

        violations = []

        for raw_relation in relations:

            relation = DocumentRelation.model_validate(
                raw_relation
            )

            relation_rules = self.rules.relations.get(
                relation.relation_type
            )

            if relation_rules is None:
                continue

            source = document_map.get(
                relation.source_doc_id
            )

            target = document_map.get(
                relation.target_doc_id
            )

            if source is None:
                violations.append({
                    "code": "SOURCE_DOCUMENT_MISSING",
                    "source_doc_id": relation.source_doc_id,
                    "target_doc_id": relation.target_doc_id,
                })
                continue

            if target is None:
                violations.append({
                    "code": "TARGET_DOCUMENT_MISSING",
                    "source_doc_id": relation.source_doc_id,
                    "target_doc_id": relation.target_doc_id,
                })
                continue

            if (
                relation_rules.issued_after_target
                and source.issued_date <= target.issued_date
            ):
                violations.append({
                    "code": "INVALID_ISSUANCE_ORDER",
                    "source_doc_id": source.doc_id,
                    "target_doc_id": target.doc_id,
                    "message": (
                        "Related document must be issued "
                        "after the target document."
                    ),
                })

            if (
                relation_rules.effective_after_target
                and source.effective_from <= target.effective_from
            ):
                violations.append({
                    "code": "INVALID_EFFECTIVE_ORDER",
                    "source_doc_id": source.doc_id,
                    "target_doc_id": target.doc_id,
                    "message": (
                        "Related document must become effective "
                        "after the target document."
                    ),
                })

        return {
            "status": (
                "PASSED"
                if not violations
                else "QUARANTINE"
            ),
            "violations": violations,
        }