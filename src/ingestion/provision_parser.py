"""Parse hierarchical legal provisions and provision-level effectivity."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple


class ProvisionParser:
    """Split parsed articles into source-backed article/clause/point nodes."""

    _CLAUSE_HEADING = re.compile(
        r"(?m)^[ \t]*(?:(?:Kho\u1ea3n|Khoan)\s+)?"
        r"(?P<number>\d+)[.)][ \t]+",
        re.IGNORECASE,
    )
    _POINT_HEADING = re.compile(
        r"(?m)^[ \t]*(?P<number>[a-zđ])[.)][ \t]+",
        re.IGNORECASE,
    )
    _POINT_REFERENCE = re.compile(
        r"diem\s+(?P<point>[a-zđ])\s+khoan\s+"
        r"(?P<clause>\d+)\s+dieu\s+(?P<article>\d+[a-z]?)",
        re.IGNORECASE,
    )
    _CLAUSE_REFERENCE = re.compile(
        r"khoan\s+(?P<clause>\d+)\s+dieu\s+"
        r"(?P<article>\d+[a-z]?)",
        re.IGNORECASE,
    )
    _ARTICLE_REFERENCE = re.compile(
        r"(?<!khoan\s)(?<!diem\s)"
        r"dieu\s+(?P<article>\d+[a-z]?)",
        re.IGNORECASE,
    )
    _DATE_PATTERNS = (
        re.compile(r"ngay\s+(\d{4})-(\d{1,2})-(\d{1,2})", re.IGNORECASE),
        re.compile(
            r"ngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+(\d{4})",
            re.IGNORECASE,
        ),
        re.compile(r"ngay\s+(\d{1,2})[./-](\d{1,2})[./-](\d{4})", re.IGNORECASE),
    )
    # Match an effectiveness clause, not every use of "có hiệu lực" (for
    # example, "bản án có hiệu lực pháp luật"). A commencement marker must
    # follow the phrase so ordinary legal prose cannot create fake targets.
    _EFFECTIVE_PHRASE = re.compile(
        r"co\s+hieu\s+luc(?:\s+thi\s+hanh)?"
        r"(?=\s+(?:(?:ke\s+tu|tu|vao)\s+ngay))",
        re.IGNORECASE,
    )
    _DOCUMENT_SUBJECT = re.compile(
        r"(?:nghi\s+dinh|thong\s+tu|luat|quyet\s+dinh|nghi\s+quyet|van\s+ban)\s+nay",
        re.IGNORECASE,
    )

    def parse_article(self, article: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Return flat rows whose parent IDs form the article hierarchy."""
        content = str(article.get("content") or "")
        article_number = str(article["article_number"])
        logical_doc_id = str(article["logical_doc_id"])
        article_id = str(article["article_id"])

        clause_candidates = list(self._CLAUSE_HEADING.finditer(content))
        clauses = []
        seen_clause_numbers = set()
        for candidate in clause_candidates:
            number = candidate.group("number")
            if number in seen_clause_numbers:
                continue
            seen_clause_numbers.add(number)
            clauses.append(candidate)
        rows: List[Dict[str, Any]] = []
        article_path = article_number.lower()
        article_end = clauses[0].start() if clauses else len(content)
        rows.append(
            self._row(
                article,
                provision_id=article_id,
                parent_provision_id=None,
                level="article",
                number=article_number,
                path=article_path,
                title=str(article.get("article_title") or ""),
                start=0,
                end=article_end,
            )
        )

        for clause_index, clause_match in enumerate(clauses):
            clause_start = clause_match.start()
            clause_end = (
                clauses[clause_index + 1].start()
                if clause_index + 1 < len(clauses)
                else len(content)
            )
            clause_number = clause_match.group("number")
            clause_path = f"{article_path}.{clause_number}"
            clause_id = f"{logical_doc_id}:provision:{clause_path}"
            point_candidates = [
                match
                for match in self._POINT_HEADING.finditer(content, clause_start, clause_end)
            ]
            points = []
            seen_point_numbers = set()
            for candidate in point_candidates:
                number = candidate.group("number").lower()
                if number in seen_point_numbers:
                    continue
                seen_point_numbers.add(number)
                points.append(candidate)
            own_clause_end = points[0].start() if points else clause_end
            rows.append(
                self._row(
                    article,
                    provision_id=clause_id,
                    parent_provision_id=article_id,
                    level="clause",
                    number=clause_number,
                    path=clause_path,
                    title=f"{article.get('article_title', '')}, khoản {clause_number}",
                    start=clause_start,
                    end=own_clause_end,
                )
            )

            for point_index, point_match in enumerate(points):
                point_start = point_match.start()
                point_end = (
                    points[point_index + 1].start()
                    if point_index + 1 < len(points)
                    else clause_end
                )
                point_number = point_match.group("number").lower()
                point_path = f"{clause_path}.{point_number}"
                rows.append(
                    self._row(
                        article,
                        provision_id=f"{logical_doc_id}:provision:{point_path}",
                        parent_provision_id=clause_id,
                        level="point",
                        number=point_number,
                        path=point_path,
                        title=(
                            f"{article.get('article_title', '')}, "
                            f"khoản {clause_number}, điểm {point_number}"
                        ),
                        start=point_start,
                        end=point_end,
                    )
                )

        return rows

    @staticmethod
    def apply_effectivity_rules(
        provisions: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
    ) -> None:
        """Resolve dates down the hierarchy while preserving exceptions."""
        by_path = {rule["target_path"]: rule for rule in rules}
        by_id = {provision["provision_id"]: provision for provision in provisions}

        for provision in provisions:
            rule = by_path.get(provision["path"])
            parent = by_id.get(provision.get("parent_provision_id"))

            if rule is not None:
                provision["effectivity_mode"] = rule["mode"]
                provision["effective_from"] = rule["effective_from"]
                provision["effective_to"] = rule["effective_to"]
                provision["trigger_reference"] = rule["trigger_reference"]
                continue

            if parent is not None:
                parent_mode = parent["effectivity_mode"]
                if parent_mode in {"explicit_date", "inherit"} and parent.get("effective_from"):
                    provision["effectivity_mode"] = "inherit"
                    provision["effective_from"] = parent["effective_from"]
                    provision["effective_to"] = parent.get("effective_to")
                    provision["trigger_reference"] = None
                elif parent_mode == "external_trigger":
                    provision["effectivity_mode"] = "external_trigger"
                    provision["effective_from"] = None
                    provision["effective_to"] = None
                    provision["trigger_reference"] = parent.get("trigger_reference")
                else:
                    provision["effectivity_mode"] = "unknown"
                    provision["effective_from"] = None
                    provision["effective_to"] = None
                    provision["trigger_reference"] = None
                continue

            document_rule = by_path.get("*")
            if document_rule and document_rule["mode"] == "explicit_date":
                provision["effectivity_mode"] = "inherit"
                provision["effective_from"] = document_rule["effective_from"]
                provision["effective_to"] = document_rule["effective_to"]
                provision["trigger_reference"] = None
            elif document_rule and document_rule["mode"] == "external_trigger":
                provision["effectivity_mode"] = "external_trigger"
                provision["effective_from"] = None
                provision["effective_to"] = None
                provision["trigger_reference"] = document_rule["trigger_reference"]
            else:
                provision["effectivity_mode"] = "unknown"
                provision["effective_from"] = None
                provision["effective_to"] = None
                provision["trigger_reference"] = None

    @staticmethod
    def _row(
        article: Dict[str, Any],
        *,
        provision_id: str,
        parent_provision_id: Optional[str],
        level: str,
        number: str,
        path: str,
        title: str,
        start: int,
        end: int,
    ) -> Dict[str, Any]:
        content = str(article.get("content") or "")
        page_segments: List[Dict[str, Any]] = []
        for segment in article.get("page_segments") or []:
            segment_start = segment.get("text_start")
            segment_end = segment.get("text_end")
            if segment_start is None or segment_end is None:
                continue
            overlap_start = max(start, int(segment_start))
            overlap_end = min(end, int(segment_end))
            if overlap_start >= overlap_end:
                continue
            text = content[overlap_start:overlap_end].strip()
            if not text:
                continue
            page_segments.append(
                {
                    key: segment.get(key)
                    for key in (
                        "physical_file_id", "file_path", "part_no",
                        "physical_page", "global_page",
                    )
                }
                | {"text": text}
            )

        return {
            "provision_id": provision_id,
            "logical_doc_id": article["logical_doc_id"],
            "article_id": article["article_id"],
            "parent_provision_id": parent_provision_id,
            "level": level,
            "number": number,
            "path": path,
            "title": title,
            "content": content[start:end].strip(),
            "issued_date": article.get("issued_date"),
            "effective_from": article.get("effective_from"),
            "effective_to": article.get("effective_to"),
            "status": article.get("status", "active"),
            "start_physical_file_id": article.get("start_physical_file_id"),
            "start_physical_page": article.get("start_physical_page"),
            "end_physical_file_id": article.get("end_physical_file_id"),
            "end_physical_page": article.get("end_physical_page"),
            "start_global_page": article.get("start_global_page"),
            "end_global_page": article.get("end_global_page"),
            "page_segments": page_segments,
            "text_start": start,
            "text_end": end,
        }

    @classmethod
    def extract_effectivity_rules(
        cls,
        text: str,
        parse_date,
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract document-wide and scoped effectivity clauses dynamically."""
        source_segments = re.split(r";|(?<=\.)\s+", re.sub(r"\s+", " ", text))
        segments = [cls._normalize(segment) for segment in source_segments]
        document_rule: Optional[Dict[str, Any]] = None
        scoped_rules: List[Dict[str, Any]] = []

        for source_segment, segment in zip(source_segments, segments):
            effective_match = cls._EFFECTIVE_PHRASE.search(segment)
            if effective_match is None:
                continue

            # A sentence can mention an unrelated date before saying that an
            # older law expires when the current law becomes effective (for
            # example, the date of a referenced resolution). Only a date that
            # follows the current effectiveness phrase can define this rule.
            date_segment = segment[effective_match.end():]
            matched_date = next(
                (
                    (index, match)
                    for index, pattern in enumerate(cls._DATE_PATTERNS)
                    if (match := pattern.search(date_segment))
                ),
                None,
            )
            if matched_date is None:
                effective_date = None
            else:
                pattern_index, date_match = matched_date
                date_parts = date_match.groups()
                effective_date = (
                    parse_date(date_parts[2], date_parts[1], date_parts[0])
                    if pattern_index == 0
                    else parse_date(*date_parts)
                )
            refs: List[Tuple[str, str]] = []
            occupied: List[Tuple[int, int]] = []

            for match in cls._POINT_REFERENCE.finditer(segment):
                refs.append(
                    (
                        f"{match.group('article')}.{match.group('clause')}."
                        f"{match.group('point').lower()}",
                        match.group(0),
                    )
                )
                occupied.append(match.span())

            for match in cls._CLAUSE_REFERENCE.finditer(segment):
                if any(start <= match.start() < end for start, end in occupied):
                    continue
                refs.append(
                    (f"{match.group('article')}.{match.group('clause')}", match.group(0))
                )
                occupied.append(match.span())

            for match in cls._ARTICLE_REFERENCE.finditer(segment):
                if any(start <= match.start() < end for start, end in occupied):
                    continue
                refs.append((match.group("article"), match.group(0)))

            if refs:
                for target_path, reference in refs:
                    scoped_rules.append(
                        {
                            "target_path": target_path,
                            "mode": "explicit_date" if effective_date else "external_trigger",
                            "effective_from": effective_date,
                            "effective_to": None,
                            "trigger_reference": None if effective_date else source_segment,
                            "source_text": source_segment,
                        }
                    )
            elif cls._DOCUMENT_SUBJECT.search(segment):
                candidate_document_rule = {
                    "target_path": "*",
                    "mode": "explicit_date" if effective_date else "external_trigger",
                    "effective_from": effective_date,
                    "effective_to": None,
                    "trigger_reference": None if effective_date else source_segment,
                    "source_text": source_segment,
                }
                # Preserve an explicit document-wide date when later clauses
                # merely refer to the current law's effective date to describe
                # the expiration of an earlier law.
                if (
                    document_rule is None
                    or (
                        document_rule["mode"] != "explicit_date"
                        and candidate_document_rule["mode"] == "explicit_date"
                    )
                ):
                    document_rule = candidate_document_rule

        return document_rule, scoped_rules

    @staticmethod
    def _normalize(text: str) -> str:
        normalized = unicodedata.normalize("NFD", text)
        normalized = "".join(
            char for char in normalized
            if unicodedata.category(char) != "Mn"
        )
        return normalized.replace(chr(273), "d").replace(chr(272), "D").lower()
