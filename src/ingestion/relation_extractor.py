"""
Extract cross-document relations stated in a legal document's own text.

Vietnamese legal instruments declare their relations to earlier documents
in a small number of fixed formulas:

    "... sửa đổi, bổ sung một số điều của Nghị định số 168/2024/NĐ-CP
     ngày 26 tháng 12 năm 2024"                          -> amends
    "... thay thế Thông tư số 10/2020/TT-BTC ..."         -> replaces
    "Bãi bỏ Quyết định số 12/2019/QĐ-UBND ..."             -> repeals
    "Nghị định số 145/2020/NĐ-CP ... hết hiệu lực kể từ ngày Nghị định này
     có hiệu lực thi hành"                                -> repeals

The extractor returns DocumentRelation-shaped dictionaries plus a partial
metadata stub for each referenced document (its number and, when the
citation states it, its issue date). These feed the cross-document part of
the Temporal contract layer, which can then check, for example, that an
amending decree is not issued before the decree it amends.

Passive mentions ("… đã được sửa đổi, bổ sung bởi Nghị định X") describe a
relation between two *other* documents and are ignored.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ExtractedRelation:
    source_doc_id: str
    target_doc_id: str
    relation_type: str
    target_issued_date: Optional[str]
    evidence: str

    def as_relation(self) -> Dict[str, Any]:
        return {
            "source_doc_id": self.source_doc_id,
            "target_doc_id": self.target_doc_id,
            "relation_type": self.relation_type,
            "evidence": self.evidence,
        }


def _fold_char(char: str) -> str:
    """Fold one character to lowercase ASCII-like form, keeping length 1."""
    if char in "đĐ":
        return "d"
    base = unicodedata.normalize("NFD", char)[:1] or char
    return base.lower() if len(base.lower()) == 1 else char


def _fold(text: str) -> str:
    return "".join(_fold_char(char) for char in text)


class RelationExtractor:
    """Rule-based extractor of amends / replaces / repeals relations."""

    _DOC_TYPES = (
        r"(?:bo\s+luat|luat|nghi\s+dinh|thong\s+tu(?:\s+lien\s+tich)?|"
        r"quyet\s+dinh|nghi\s+quyet(?:\s+lien\s+tich)?|phap\s+lenh|van\s+ban)"
    )
    # Reference in folded text: optional type, optional "số", the number,
    # optional "ngày dd tháng mm năm yyyy" right after it.
    _REFERENCE = re.compile(
        rf"(?:{_DOC_TYPES}\s+)?(?:so\s*:?\s*)?"
        r"(?P<id>\d+/\d{4}/[a-z0-9]+(?:-[a-z0-9]+)*)"
        r"(?:\s*,?\s*ngay\s+(?P<d>\d{1,2})\s+thang\s+(?P<m>\d{1,2})\s+nam\s+(?P<y>\d{4}))?"
    )
    _FORWARD_TRIGGERS: Tuple[Tuple[str, re.Pattern], ...] = (
        ("amends", re.compile(r"sua\s+doi(?:\s*,\s*|\s+)?(?:bo\s+sung)?")),
        ("replaces", re.compile(r"thay\s+the")),
        ("repeals", re.compile(r"bai\s+bo")),
    )
    _EXPIRY = re.compile(r"het\s+hieu\s+luc")
    _PASSIVE = re.compile(r"(?:duoc|bi|da\s+duoc)\s*$")
    _BY_AGENT = re.compile(r"\bboi\b")
    _BASIS = re.compile(r"^\s*can\s+cu\b")
    _WINDOW = 220

    def extract(
        self,
        text: str,
        source_doc_id: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Return ``(relations, related_documents)`` for one document.

        ``relations`` follow the DocumentRelation shape (with an extra
        ``evidence`` field); ``related_documents`` hold partial metadata
        (``doc_id``, ``issued_date`` when cited) for referenced documents.
        """

        found = self.extract_relations(text, source_doc_id)

        relations: List[Dict[str, Any]] = []
        seen = set()
        related: Dict[str, Dict[str, Any]] = {}
        for item in found:
            key = (item.target_doc_id, item.relation_type)
            if key not in seen:
                seen.add(key)
                relations.append(item.as_relation())
            stub = related.setdefault(
                item.target_doc_id,
                {"doc_id": item.target_doc_id, "issued_date": None,
                 "metadata_source": "citation"},
            )
            if item.target_issued_date and not stub["issued_date"]:
                stub["issued_date"] = item.target_issued_date

        return relations, list(related.values())

    def extract_relations(
        self,
        text: str,
        source_doc_id: str,
    ) -> List[ExtractedRelation]:
        # Drop parser HTML comments, collapse whitespace, then fold. Folding
        # keeps one character per character, so offsets map back to the
        # original text and document numbers keep their "Đ".
        clean = re.sub(r"<!--.*?-->", " ", text or "", flags=re.DOTALL)
        clean = re.sub(r"\s+", " ", unicodedata.normalize("NFC", clean))
        folded = _fold(clean)
        source_key = _fold(source_doc_id or "")

        results: List[ExtractedRelation] = []

        for sentence_start, sentence_end in self._sentences(folded):
            sentence = folded[sentence_start:sentence_end]
            if self._BASIS.search(sentence):
                continue  # "Căn cứ ..." cites the legal basis, not a relation

            for relation_type, trigger in self._FORWARD_TRIGGERS:
                for match in trigger.finditer(sentence):
                    if self._PASSIVE.search(sentence[: match.start()][-20:]):
                        continue
                    window = sentence[match.end(): match.end() + self._WINDOW]
                    for ref in self._REFERENCE.finditer(window):
                        between = window[: ref.start()]
                        if self._BY_AGENT.search(between):
                            break  # "... sửa đổi bởi X": X is the agent
                        relation = self._build(
                            clean, sentence_start + match.end(), ref,
                            relation_type, source_doc_id, source_key,
                            sentence_start, sentence_end,
                        )
                        if relation:
                            results.append(relation)

            for match in self._EXPIRY.finditer(sentence):
                before = sentence[: match.start()]
                for ref in self._REFERENCE.finditer(before):
                    relation = self._build(
                        clean, sentence_start, ref, "repeals",
                        source_doc_id, source_key,
                        sentence_start, sentence_end,
                    )
                    if relation:
                        results.append(relation)

        return results

    @staticmethod
    def _sentences(folded: str) -> List[Tuple[int, int]]:
        spans: List[Tuple[int, int]] = []
        start = 0
        for match in re.finditer(r"[;.](?=\s|$)", folded):
            spans.append((start, match.end()))
            start = match.end()
        if start < len(folded):
            spans.append((start, len(folded)))
        return spans

    @staticmethod
    def _build(
        original: str,
        offset: int,
        ref: re.Match,
        relation_type: str,
        source_doc_id: str,
        source_key: str,
        sentence_start: int,
        sentence_end: int,
    ) -> Optional[ExtractedRelation]:
        id_start = offset + ref.start("id")
        id_end = offset + ref.end("id")
        target = original[id_start:id_end].upper()
        if _fold(target) == source_key:
            return None

        issued = None
        if ref.group("y"):
            try:
                issued = date(
                    int(ref.group("y")), int(ref.group("m")), int(ref.group("d"))
                ).isoformat()
            except ValueError:
                issued = None

        return ExtractedRelation(
            source_doc_id=source_doc_id,
            target_doc_id=target,
            relation_type=relation_type,
            target_issued_date=issued,
            evidence=original[sentence_start:sentence_end].strip()[:300],
        )
