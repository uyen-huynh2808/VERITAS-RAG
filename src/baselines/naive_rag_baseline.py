from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import faiss
import numpy as np
import pandas as pd
from FlagEmbedding import BGEM3FlagModel
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
# Naive Dense RAG Retriever
# ============================================================

class NaiveDenseRetriever:
    """
    Baseline 2: Dense Retrieval using BGE-M3 + FAISS.

    Input
    -----
    gold_chunks.parquet

    Output
    ------
    Top-K RetrievedChunk

    Notes
    -----
    - Dense embedding
    - Flat retrieval
    - No temporal filtering
    - No contracts
    """

    def __init__(
        self,
        gold_path: str | Path,
        embedding_model: Optional[str] = None,
        batch_size: Optional[int] = None,
        max_length: Optional[int] = None,
        use_fp16: bool = False,
    ):
        settings = load_system_config()
        self.batch_size = (
            settings.models.embedding_batch_size
            if batch_size is None
            else batch_size
        )
        self.max_length = (
            settings.models.embedding_max_length
            if max_length is None
            else max_length
        )

        self.gold_path = Path(gold_path)

        self.df = pd.read_parquet(self.gold_path)

        self.texts = (
            self.df["text_content"]
            .fillna("")
            .astype(str)
            .tolist()
        )

        self.model = BGEM3FlagModel(
            embedding_model or settings.models.embedding_model,
            use_fp16=use_fp16,
        )

        self.index = None

        self._build_index()

    # --------------------------------------------------------
    # Embedding
    # --------------------------------------------------------

    def _encode(
        self,
        texts: List[str],
    ) -> np.ndarray:

        output = self.model.encode(
            texts,
            batch_size=self.batch_size,
            max_length=self.max_length,
        )

        embeddings = output["dense_vecs"]

        embeddings = embeddings.astype("float32")

        faiss.normalize_L2(embeddings)

        return embeddings

    # --------------------------------------------------------
    # Build FAISS index
    # --------------------------------------------------------

    def _build_index(self):

        embeddings = self._encode(self.texts)

        dimension = embeddings.shape[1]

        self.index = faiss.IndexFlatIP(dimension)

        self.index.add(embeddings)

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

        query_embedding = self._encode([query])

        scores, indices = self.index.search(
            query_embedding,
            top_k,
        )

        results: List[RetrievedChunk] = []

        for score, idx in zip(scores[0], indices[0]):

            row = self.df.iloc[int(idx)]

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

    parser = argparse.ArgumentParser(description="Run the dense baseline.")
    parser.add_argument("--gold", required=True, help="Gold chunks parquet path.")
    parser.add_argument("--query", required=True, help="Query to retrieve.")
    parser.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args()

    retriever = NaiveDenseRetriever(args.gold)
    for chunk in retriever.retrieve(args.query, top_k=args.top_k):
        print("=" * 80)
        print(chunk.chunk_id)
        print(f"Score: {chunk.score:.4f}")
        print(chunk.text[:250])
