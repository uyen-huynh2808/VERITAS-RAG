from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List
from typing import Optional

import pandas as pd
from src.core.config import load_system_config


# ============================================================
# Data classes
# ============================================================

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

# ============================================================
# BM25 Retriever
# ============================================================

class BM25Retriever:
    """
    Baseline 1: Pure lexical BM25 retrieval.

    Input:
        Gold Parquet

    Output:
        Top-K RetrievedChunk

    Notes
    -----
    - No embedding
    - No temporal filtering
    - No data contracts
    """

    def __init__(
        self,
        gold_path: str | Path,
        k1: Optional[float] = None,
        b: Optional[float] = None,
    ):
        settings = load_system_config()
        self.gold_path = Path(gold_path)
        self.k1 = settings.retrieval.bm25_k1 if k1 is None else k1
        self.b = settings.retrieval.bm25_b if b is None else b

        self.df = pd.read_parquet(self.gold_path)

        self.documents = self.df["text_content"].fillna("").tolist()

        self.doc_len = []
        self.avgdl = 0.0

        self.term_freqs = []
        self.doc_freq = defaultdict(int)
        self.idf = {}

        self._build_index()

    # --------------------------------------------------------
    # Tokenizer
    # --------------------------------------------------------

    @staticmethod
    def tokenize(text: str) -> List[str]:
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        return text.split()

    # --------------------------------------------------------
    # Build BM25 index
    # --------------------------------------------------------

    def _build_index(self):

        total_len = 0

        for doc in self.documents:

            tokens = self.tokenize(doc)

            total_len += len(tokens)
            self.doc_len.append(len(tokens))

            tf = Counter(tokens)
            self.term_freqs.append(tf)

            for term in tf:
                self.doc_freq[term] += 1

        N = len(self.documents)
        self.avgdl = total_len / max(N, 1)

        for term, df in self.doc_freq.items():
            self.idf[term] = math.log(
                1 + (N - df + 0.5) / (df + 0.5)
            )

    # --------------------------------------------------------
    # Score one document
    # --------------------------------------------------------

    def _score(
        self,
        query_tokens: List[str],
        doc_index: int,
    ) -> float:

        score = 0.0
        tf = self.term_freqs[doc_index]
        dl = self.doc_len[doc_index]

        for term in query_tokens:

            if term not in tf:
                continue

            freq = tf[term]
            idf = self.idf.get(term, 0)

            numerator = freq * (self.k1 + 1)

            denominator = (
                freq
                + self.k1
                * (
                    1
                    - self.b
                    + self.b * dl / self.avgdl
                )
            )

            score += idf * numerator / denominator

        return score

    # --------------------------------------------------------
    # Retrieval
    # --------------------------------------------------------

    def retrieve(
        self,
        query: str,
        t_event: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> List[RetrievedChunk]:
        if top_k is None:
            top_k = load_system_config().retrieval.top_k

        query_tokens = self.tokenize(query)

        scores = []

        for idx in range(len(self.documents)):
            score = self._score(query_tokens, idx)

            if score > 0:
                scores.append((idx, score))

        scores.sort(key=lambda x: x[1], reverse=True)

        results: List[RetrievedChunk] = []

        for idx, score in scores[:top_k]:

            row = self.df.iloc[idx]

            results.append(
                RetrievedChunk(
                    chunk_id=row["chunk_id"],
                    article_id=row["article_id"],
                    logical_doc_id=row["logical_doc_id"],
                    text=row["text_content"],
                    global_page=int(row["global_page"]),
                    score=float(score),
                    effective_from=(
                        None
                        if pd.isna(row["effective_from"])
                        else row["effective_from"].isoformat()
                    ),
                    effective_to=(
                        None
                        if pd.isna(row["effective_to"])
                        else row["effective_to"].isoformat()
                    ),
                    provision_id=row.get("provision_id"),
                    provision_path=row.get("provision_path"),
                    effectivity_mode=row.get("effectivity_mode"),
                    trigger_reference=row.get("trigger_reference"),
                )
            )

        return results


# ============================================================
# Example
# ============================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the BM25 baseline.")
    parser.add_argument("--gold", required=True, help="Gold chunks parquet path.")
    parser.add_argument("--query", required=True, help="Query to retrieve.")
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args()

    retriever = BM25Retriever(args.gold)
    for c in retriever.retrieve(args.query, top_k=args.top_k):
        print("=" * 80)
        print(c.chunk_id)
        print(c.score)
        print(c.text[:250])
