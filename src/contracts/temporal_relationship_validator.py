from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional


@dataclass
class TemporalRelationshipViolation:
    source_doc_id: str
    target_doc_id: str
    message: str


class CrossDocumentTemporalValidator:
    """
    Validates temporal relationships between legal documents.

    This validator works at corpus/document-relation level.
    It is intentionally separate from the single-document contract gate.
    """

    def validate(
        self,
        document: Dict,
        related_documents: List[Dict],
    ) -> List[TemporalRelationshipViolation]:

        violations = []

        source_doc_id = document.get("doc_id")

        for related in related_documents:

            target_doc_id = related.get("doc_id")

            if not source_doc_id or not target_doc_id:
                continue

            violation = self._validate_order(
                document,
                related,
            )

            if violation:
                violations.append(violation)

        return violations

    def _validate_order(
        self,
        source: Dict,
        target: Dict,
    ) -> Optional[TemporalRelationshipViolation]:

        source_effective = self._parse_date(
            source.get("effective_date")
        )

        target_issued = self._parse_date(
            target.get("issued_date")
        )

        if source_effective is None or target_issued is None:
            return None

        if target_issued < source_effective:
            return TemporalRelationshipViolation(
                source_doc_id=source["doc_id"],
                target_doc_id=target["doc_id"],
                message=(
                    "Related document is issued before the effective "
                    "date of the document it modifies/replaces."
                ),
            )

        return None

    @staticmethod
    def _parse_date(value) -> Optional[date]:

        if isinstance(value, date):
            return value

        if not isinstance(value, str):
            return None

        try:
            return date.fromisoformat(value)
        except ValueError:
            return None