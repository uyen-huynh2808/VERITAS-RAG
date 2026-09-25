from typing import Any, Dict, List

from src.contracts.models import (
    ContractIssue,
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

        violations: List[ContractIssue] = []

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
                violations.append(
                    ContractIssue(
                        layer="temporal",
                        code="SOURCE_DOCUMENT_MISSING",
                        message="Source document is missing.",
                        details={
                            "source_doc_id": relation.source_doc_id,
                            "target_doc_id": relation.target_doc_id,
                        },
                    )
                )
                continue

            if target is None:
                violations.append(
                    ContractIssue(
                        layer="temporal",
                        code="TARGET_DOCUMENT_MISSING",
                        message="Target document is missing.",
                        details={
                            "source_doc_id": relation.source_doc_id,
                            "target_doc_id": relation.target_doc_id,
                        },
                    )
                )
                continue

            if (
                relation_rules.issued_after_target
                and source.issued_date <= target.issued_date
            ):
                violations.append(
                    ContractIssue(
                        layer="temporal",
                        code="INVALID_ISSUANCE_ORDER",
                        message=(
                            "Related document must be issued "
                            "after the target document."
                        ),
                        details={
                            "source_doc_id": source.doc_id,
                            "target_doc_id": target.doc_id,
                        },
                    )
                )

            if (
                relation_rules.effective_after_target
                and source.effective_from <= target.effective_from
            ):
                violations.append(
                    ContractIssue(
                        layer="temporal",
                        code="INVALID_EFFECTIVE_ORDER",
                        message=(
                            "Related document must become effective "
                            "after the target document."
                        ),
                        details={
                            "source_doc_id": source.doc_id,
                            "target_doc_id": target.doc_id,
                        },
                    )
                )

        return {
            "status": (
                "PASSED"
                if not violations
                else "QUARANTINE"
            ),
            "violations": violations,
        }