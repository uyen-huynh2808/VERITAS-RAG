from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import faiss
import numpy as np
import pandas as pd
from FlagEmbedding import BGEM3FlagModel


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
        embedding_model: str = "BAAI/bge-m3",
        use_fp16: bool = False,
    ):

        self.gold_path = Path(gold_path)

        self.df = pd.read_parquet(self.gold_path)

        self.texts = (
            self.df["text_content"]
            .fillna("")
            .astype(str)
            .tolist()
        )

        self.model = BGEM3FlagModel(
            embedding_model,
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
            batch_size=16,
            max_length=512,
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
        top_k: int = 5,
    ) -> List[RetrievedChunk]:

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
                )
            )

        return results


# ============================================================
# Example
# ============================================================

if __name__ == "__main__":

    retriever = NaiveDenseRetriever(
        gold_path="data/gold/gold_chunks.parquet"
    )

    query = (
        "Nghị định 168/2024 có hiệu lực từ ngày nào?"
    )

    chunks = retriever.retrieve(
        query=query,
        top_k=3,
    )

    for chunk in chunks:
        print("=" * 80)
        print(chunk.chunk_id)
        print(f"Score: {chunk.score:.4f}")
        print(chunk.text[:250])