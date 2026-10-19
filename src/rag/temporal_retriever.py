from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from src.core.config import SystemConfig, load_system_config
from src.core.interfaces import BaseRetriever


@dataclass
class RetrievedChunk:
    chunk_id: str
    article_id: str
    logical_doc_id: str
    text: str
    global_page: int
    score: float
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    provision_id: Optional[str] = None
    provision_path: Optional[str] = None
    effectivity_mode: Optional[str] = None
    trigger_reference: Optional[str] = None
    issued_date: Optional[str] = None
    date_basis: Optional[str] = None
    is_in_force_at_event: Optional[bool] = None


class TemporalRetriever(BaseRetriever):
    """Retrieve legal text using issuance, status, or effectivity.

    ``date_basis="issued"`` answers what text had been promulgated/issued by
    the requested date, even if it had not yet taken effect. ``date_basis=
    "effective"`` restricts candidates to provisions legally in force on that
    date. Temporal eligibility is applied before lexical ranking.

    ``date_basis="status"`` retrieves issued text, including a law whose
    effective date is still in the future, so the model can answer whether it
    was in force at the requested date.

    ``effective_to`` is an exclusive upper bound; a null value means
    open-ended validity unless an explicit supersession rule supplies an end
    date.

    ``supersession_rules`` can be a JSON file or a list of dictionaries. Each
    rule must identify a target document and either a provision path, provision
    ID, or chunk ID, plus the date on which that target stopped applying. This
    deliberately requires an explicit scope: a later document by itself does
    not prove that every provision in an earlier document was repealed.
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        gold_path: Optional[str | Path] = None,
        supersession_rules: Optional[str | Path | Iterable[Dict[str, Any]]] = None,
    ):
        resolved_config = config or load_system_config()
        super().__init__(config=resolved_config)
        self.gold_path = Path(gold_path or self.config.benchmark.gold_path)
        if not self.gold_path.exists():
            raise FileNotFoundError(f"Gold chunks not found: {self.gold_path}")

        self.df = pd.read_parquet(self.gold_path).reset_index(drop=True)
        required = {
            "chunk_id", "logical_doc_id", "text_content",
            "issued_date", "effective_from",
        }
        missing = required.difference(self.df.columns)
        if missing:
            raise ValueError(
                "Gold chunks are missing required columns: "
                + ", ".join(sorted(missing))
            )
        self.df["text_content"] = self.df["text_content"].fillna("").astype(str)

        if supersession_rules is None:
            configured_rules_path = self.config.retrieval.supersession_rules_path
            if configured_rules_path:
                rules_path = Path(configured_rules_path)
                if not rules_path.exists():
                    raise FileNotFoundError(
                        f"Configured supersession rules not found: {rules_path}"
                    )
                supersession_rules = rules_path
        self.supersession_rules = self._load_supersession_rules(supersession_rules)

    @staticmethod
    def _load_supersession_rules(
        rules: Optional[str | Path | Iterable[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        if rules is None:
            return []
        if isinstance(rules, (str, Path)):
            with Path(rules).open("r", encoding="utf-8") as source:
                rules = json.load(source)
        if isinstance(rules, dict):
            rules = rules.get("supersession_rules", [])
        normalized = []
        for rule in rules:
            if not isinstance(rule, dict):
                raise TypeError("Each supersession rule must be a JSON object.")
            if not rule.get("target_logical_doc_id"):
                raise ValueError("A supersession rule needs target_logical_doc_id.")
            if not any(rule.get(key) for key in (
                "target_chunk_id", "target_provision_id", "target_provision_path"
            )):
                raise ValueError(
                    "A supersession rule needs a chunk ID, provision ID, or provision path."
                )
            if not rule.get("effective_to"):
                raise ValueError("A supersession rule needs effective_to.")
            # Validate dates when rules are loaded, not during retrieval.
            date.fromisoformat(str(rule["effective_to"])[:10])
            normalized.append(rule)
        return normalized

    @staticmethod
    def extract_event_date(query: str) -> Optional[str]:
        """Extract common ISO, day/month/year, or Vietnamese written dates."""
        patterns = (
            r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)",
            r"(?<!\d)(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?!\d)",
            r"(?i)ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})",
        )
        match = re.search(patterns[0], query)
        if match:
            return date.fromisoformat(match.group(1)).isoformat()
        for pattern in patterns[1:]:
            match = re.search(pattern, query)
            if match:
                day, month, year = (int(value) for value in match.groups())
                return date(year, month, day).isoformat()
        return None

    @staticmethod
    def infer_date_basis(query: str) -> str:
        """Prefer effectivity for application questions, issuance for text lookup."""
        normalized = query.casefold()
        status_patterns = (
            r"đã.{0,30}có hiệu lực",
            r"có hiệu lực.{0,30}(?:chưa|không)",
            r"còn hiệu lực.{0,30}(?:chưa|không)",
            r"nằm trong mốc hiệu lực",
            r"mốc hiệu lực.{0,30}(?:đến|chưa)",
            r"được coi là.{0,30}hiệu lực",
        )
        if any(re.search(pattern, normalized) for pattern in status_patterns):
            return "status"

        effective_cues = (
            "có hiệu lực", "đang có hiệu lực", "còn hiệu lực", "hết hiệu lực",
            "áp dụng", "được áp dụng", "phải áp dụng", "quy định nào có hiệu lực",
        )
        if any(cue in normalized for cue in effective_cues):
            return "effective"

        content_patterns = (
            r"điều chỉnh.{0,60}(?:gì|nhóm|vấn đề|nội dung|phạm vi)",
            r"quy định.{0,60}(?:gì|nhóm|vấn đề|nội dung|phạm vi)",
            r"phạm vi điều chỉnh", r"nội dung chính", r"nội dung của",
            r"liệt kê", r"định nghĩa",
        )
        if any(re.search(pattern, normalized) for pattern in content_patterns):
            return "issued"

        # Legal applicability is the conservative default when intent is unclear.
        return "effective"

    @staticmethod
    def _as_date(value: Any) -> Optional[date]:
        if value is None or pd.isna(value):
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    @staticmethod
    def _tokens(text: str) -> List[str]:
        return re.findall(r"[\w]+", str(text).lower(), flags=re.UNICODE)

    @staticmethod
    def _mentions_date(text: str, event_date: date) -> bool:
        """Return whether legal text explicitly states the as-of date."""
        day, month, year = event_date.day, event_date.month, event_date.year
        date_patterns = (
            rf"(?<!\d){year}-{month:02d}-{day:02d}(?!\d)",
            rf"(?<!\d)0?{day}[/.]0?{month}[/.]{year}(?!\d)",
            rf"(?<!\d)0?{day}\s+tháng\s+0?{month}\s+năm\s+{year}(?!\d)",
        )
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in date_patterns)

    def _rule_end_date(self, row: pd.Series) -> Optional[date]:
        row_values = {
            "target_logical_doc_id": str(row.get("logical_doc_id", "")),
            "target_chunk_id": str(row.get("chunk_id", "")),
            "target_provision_id": str(row.get("provision_id", "")),
            "target_provision_path": str(row.get("provision_path", "")),
        }
        end_dates = []
        for rule in self.supersession_rules:
            if any(
                rule.get(key) and str(rule[key]) != row_values[key]
                for key in ("target_logical_doc_id",)
            ):
                continue
            scope_keys = (
                "target_chunk_id", "target_provision_id", "target_provision_path"
            )
            if any(
                rule.get(key) and str(rule[key]) != row_values[key]
                for key in scope_keys
            ):
                continue
            end_dates.append(date.fromisoformat(str(rule["effective_to"])[:10]))
        return min(end_dates) if end_dates else None

    def _score(self, query_tokens: List[str], document_tokens: List[str]) -> float:
        if not query_tokens or not document_tokens:
            return 0.0
        n_docs = len(self._eligible_indices)
        average_length = self._average_length
        term_frequency = Counter(document_tokens)
        length = len(document_tokens)
        k1 = self.config.retrieval.bm25_k1
        b = self.config.retrieval.bm25_b
        score = 0.0
        for term in query_tokens:
            frequency = term_frequency.get(term, 0)
            if not frequency:
                continue
            document_frequency = self._document_frequency.get(term, 0)
            inverse_frequency = math.log(
                1.0 + (n_docs - document_frequency + 0.5) / (document_frequency + 0.5)
            )
            denominator = frequency + k1 * (
                1.0 - b + b * length / max(average_length, 1.0)
            )
            score += inverse_frequency * frequency * (k1 + 1.0) / denominator

        # Reward exact multi-word matches. This helps distinguish legal
        # phrases such as "hiệu lực chung" and "đào tạo nghề" from documents
        # that only share generic words like "luật" or "điều khoản".
        document_phrases = self._phrase_cache.get(id(document_tokens), set())
        for phrase in self._query_phrases:
            if phrase not in document_phrases:
                continue
            document_frequency = self._phrase_frequency.get(phrase, 0)
            inverse_frequency = math.log(
                1.0 + (n_docs - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            score += (1.0 if len(phrase) == 2 else 1.4) * inverse_frequency
        return score

    def retrieve(
        self,
        query: str,
        t_event: Optional[str] = None,
        top_k: Optional[int] = None,
        date_basis: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        event_date = self._as_date(t_event or self.extract_event_date(query))
        if event_date is None:
            raise ValueError(
                "An as-of date is required. Pass t_event='YYYY-MM-DD' or include "
                "a date in the query."
            )

        basis = (date_basis or self.infer_date_basis(query)).strip().lower()
        if basis not in {"issued", "effective", "status"}:
            raise ValueError("date_basis must be 'issued', 'effective', or 'status'.")

        eligible: List[int] = []
        for idx, row in self.df.iterrows():
            if basis in {"issued", "status"}:
                issued = self._as_date(row.get("issued_date"))
                if issued is not None and issued <= event_date:
                    eligible.append(idx)
                continue

            start = self._as_date(row.get("effective_from"))
            if start is None or start > event_date:
                continue
            end = self._as_date(row.get("effective_to"))
            inferred_end = self._rule_end_date(row)
            if inferred_end is not None and (end is None or inferred_end < end):
                end = inferred_end
            if end is not None and event_date >= end:
                continue
            eligible.append(idx)

        if not eligible:
            return []

        self._eligible_indices = eligible
        self._token_cache = {
            idx: self._tokens(self.df.iloc[idx]["text_content"])
            for idx in eligible
        }
        self._phrase_cache = {}
        self._phrase_frequency = Counter()
        for idx, tokens in self._token_cache.items():
            phrases = {
                tuple(tokens[pos:pos + size])
                for size in (2, 3)
                for pos in range(len(tokens) - size + 1)
            }
            self._phrase_cache[id(tokens)] = phrases
            self._phrase_frequency.update(phrases)
        query_tokens = self._tokens(query)
        self._query_phrases = {
            tuple(query_tokens[pos:pos + size])
            for size in (2, 3)
            for pos in range(len(query_tokens) - size + 1)
        }
        frequencies = Counter()
        for tokens in self._token_cache.values():
            frequencies.update(set(tokens))
        self._document_frequency = frequencies
        self._average_length = (
            sum(len(tokens) for tokens in self._token_cache.values())
            / len(self._token_cache)
        )

        explicit_doc_ids = {
            value.casefold()
            for value in re.findall(
                r"(?<![\w])\d+/\d{4}/[A-ZĐ0-9]+(?:-[A-ZĐ0-9]+)*(?![\w])",
                query,
                flags=re.IGNORECASE,
            )
        }
        # For questions asking when a rule starts to apply, provisions that
        # explicitly state the requested date are stronger evidence than
        # topical provisions that only mention the same subject. This is a
        # generic date match; it does not depend on a particular law/article.
        asks_effectivity = any(
            cue in query.casefold()
            for cue in ("hiệu lực", "thi hành", "áp dụng", "bắt đầu")
        )
        scored = [
            (
                idx,
                self._score(query_tokens, self._token_cache[idx])
                + (8.0 if str(self.df.iloc[idx]["logical_doc_id"]).casefold() in explicit_doc_ids else 0.0)
                + (
                    20.0
                    if asks_effectivity and self._mentions_date(
                        str(self.df.iloc[idx]["text_content"]), event_date
                    )
                    else 0.0
                ),
            )
            for idx in eligible
        ]
        scored = [(idx, score) for idx, score in scored if score > 0]
        scored.sort(key=lambda item: (-item[1], str(self.df.iloc[item[0]]["chunk_id"])))
        limit = top_k if top_k is not None else self.config.retrieval.top_k
        if limit <= 0:
            return []

        results: List[RetrievedChunk] = []
        # Article-level PDFs can contain several adjacent chunks for the same
        # provision. Reserve the top-k slots for distinct provisions first so
        # one long article does not crowd out other relevant evidence.
        selected: List[tuple[int, float]] = []
        selected_provisions = set()
        for idx, score in scored:
            row = self.df.iloc[idx]
            provision_key = str(
                row.get("provision_id")
                if pd.notna(row.get("provision_id"))
                else row["chunk_id"]
            )
            if provision_key in selected_provisions:
                continue
            selected.append((idx, score))
            selected_provisions.add(provision_key)
            if len(selected) >= max(limit, 0):
                break

        for idx, score in selected:
            row = self.df.iloc[idx]
            start = self._as_date(row.get("effective_from"))
            end = self._as_date(row.get("effective_to"))
            inferred_end = self._rule_end_date(row)
            if inferred_end is not None and (end is None or inferred_end < end):
                end = inferred_end
            results.append(RetrievedChunk(
                chunk_id=str(row["chunk_id"]),
                article_id=str(row.get("article_id", "")),
                logical_doc_id=str(row["logical_doc_id"]),
                text=str(row["text_content"] or ""),
                global_page=int(row.get("global_page", 0) or 0),
                score=float(score),
                effective_from=start.isoformat() if start else None,
                effective_to=end.isoformat() if end else None,
                provision_id=None if pd.isna(row.get("provision_id")) else str(row.get("provision_id")),
                provision_path=None if pd.isna(row.get("provision_path")) else str(row.get("provision_path")),
                effectivity_mode=None if pd.isna(row.get("effectivity_mode")) else str(row.get("effectivity_mode")),
                trigger_reference=None if pd.isna(row.get("trigger_reference")) else str(row.get("trigger_reference")),
                issued_date=(
                    issued.isoformat()
                    if (issued := self._as_date(row.get("issued_date")))
                    else None
                ),
                date_basis=basis,
                is_in_force_at_event=(
                    None
                    if start is None
                    else start <= event_date and (end is None or event_date < end)
                ),
            ))
        return results
