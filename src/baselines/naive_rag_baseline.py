import logging
from typing import Any, Dict, List, Optional

import numpy as np

from src.core.config import SystemConfig
from src.core.interfaces import BaseRetriever


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional dependency
# ---------------------------------------------------------------------------

try:
    from sentence_transformers import SentenceTransformer

    HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    SentenceTransformer = None
    HAS_SENTENCE_TRANSFORMERS = False
    logger.warning(
        "Library 'sentence-transformers' is not installed. "
        "Dense embedding retrieval will not be available."
    )


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def cosine_similarity_matrix(
    a: np.ndarray,
    b: np.ndarray,
) -> np.ndarray:
    """
    Compute pairwise cosine similarity between two sets of vectors.

    Args:
        a: Shape (M, D)
        b: Shape (N, D)

    Returns:
        Similarity matrix of shape (M, N).
    """
    if a.size == 0 or b.size == 0:
        return np.empty((0, 0), dtype=np.float32)

    a_norm = a / (
        np.linalg.norm(a, axis=1, keepdims=True) + 1e-10
    )

    b_norm = b / (
        np.linalg.norm(b, axis=1, keepdims=True) + 1e-10
    )

    return np.dot(a_norm, b_norm.T)


# ---------------------------------------------------------------------------
# Naive Flat RAG baseline
# ---------------------------------------------------------------------------

class NaiveRAGBaseline(BaseRetriever):
    """
    Naive Flat RAG baseline using dense vector retrieval and direct LLM
    generation.

    This baseline intentionally does NOT perform:
    - contract validation
    - temporal as-of filtering
    - HART verification
    - lineage tracing
    - article-level temporal reasoning

    It represents a standard flat RAG pipeline:
        chunks -> embeddings -> cosine retrieval -> LLM generation
    """

    def __init__(
        self,
        config: Optional[SystemConfig] = None,
        model_name: str = "BAAI/bge-m3",
        embedding_dim: int = 1024,
        llm_client: Optional[Any] = None,
    ):
        super().__init__(config=config)

        self.model_name = model_name
        self.embedding_dim = embedding_dim
        self.llm_client = llm_client

        self.embedder: Optional[Any] = None

        self.corpus_chunks: List[Dict[str, Any]] = []
        self.corpus_embeddings: Optional[np.ndarray] = None

        if not HAS_SENTENCE_TRANSFORMERS:
            raise ImportError(
                "The 'sentence-transformers' package is required to run "
                "the Naive Flat RAG baseline. "
                "Install it with: pip install sentence-transformers"
            )

        try:
            self.embedder = SentenceTransformer(
                self.model_name
            )

            logger.info(
                "Initialized SentenceTransformer with model: %s",
                self.model_name,
            )

        except Exception as exc:
            raise RuntimeError(
                f"Failed to load embedding model '{self.model_name}'."
            ) from exc

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def encode_texts(
        self,
        texts: List[str],
    ) -> np.ndarray:
        """
        Encode text strings into dense embeddings using BGE-M3.
        """
        if not texts:
            return np.empty(
                (0, self.embedding_dim),
                dtype=np.float32,
            )

        embeddings = self.embedder.encode(
            texts,
            show_progress_bar=False,
            convert_to_numpy=True,
        )

        return np.asarray(
            embeddings,
            dtype=np.float32,
        )

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_documents(
        self,
        chunks: List[Dict[str, Any]],
    ) -> None:
        """
        Index flat legal-document chunks.

        Expected fields include:
            chunk_id
            article_id
            logical_doc_id
            physical_file_id
            file_path
            part_no
            physical_page
            global_page
            text_content
        """
        if not chunks:
            logger.warning(
                "Naive Flat RAG indexer received an empty chunk list."
            )

            self.corpus_chunks = []
            self.corpus_embeddings = None
            return

        self.corpus_chunks = [
            dict(chunk)
            for chunk in chunks
        ]

        # Reuse pre-computed Gold embeddings when every chunk has one.
        has_all_embeddings = all(
            chunk.get("embedding") is not None
            and len(chunk.get("embedding")) > 0
            for chunk in self.corpus_chunks
        )

        if has_all_embeddings:
            logger.info(
                "Using pre-computed embeddings for %d chunks.",
                len(self.corpus_chunks),
            )

            self.corpus_embeddings = np.asarray(
                [
                    chunk["embedding"]
                    for chunk in self.corpus_chunks
                ],
                dtype=np.float32,
            )

        else:
            logger.info(
                "Encoding %d chunks using %s.",
                len(self.corpus_chunks),
                self.model_name,
            )

            texts = [
                chunk.get("text_content", "")
                for chunk in self.corpus_chunks
            ]

            self.corpus_embeddings = self.encode_texts(
                texts
            )

        if len(self.corpus_embeddings) != len(
            self.corpus_chunks
        ):
            raise ValueError(
                "Number of embeddings does not match number of chunks."
            )

    def index_from_lakehouse(
        self,
        lakehouse_manager: Any,
    ) -> None:
        """
        Load Gold chunks from DuckDB and build the flat dense index.

        No temporal filtering is applied because this is an intentionally
        non-temporal baseline.
        """
        cursor = lakehouse_manager.conn.execute(
            """
            SELECT
                chunk_id,
                article_id,
                logical_doc_id,
                physical_file_id,
                file_path,
                part_no,
                physical_page,
                global_page,
                chunk_index,
                text_content,
                embedding,
                issued_date,
                effective_from,
                effective_to,
                status,
                metadata_json
            FROM gold_chunks
            WHERE text_content IS NOT NULL
              AND text_content <> '';
            """
        )

        columns = [
            description[0]
            for description in cursor.description
        ]

        rows = cursor.fetchall()

        chunks = [
            dict(zip(columns, row))
            for row in rows
        ]

        logger.info(
            "Loaded %d Gold chunks from lakehouse.",
            len(chunks),
        )

        self.index_documents(chunks)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str,
        t_event: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve top-k chunks using cosine similarity.

        `t_event` is intentionally ignored to preserve the definition
        of the Naive Flat RAG baseline.
        """
        if t_event is not None:
            logger.debug(
                "Naive Flat RAG intentionally ignores t_event=%s.",
                t_event,
            )

        if top_k <= 0:
            return []

        if (
            not self.corpus_chunks
            or self.corpus_embeddings is None
            or len(self.corpus_embeddings) == 0
        ):
            logger.error(
                "Naive RAG index is empty. "
                "Call index_documents() first."
            )
            return []

        query_vector = self.encode_texts([query])

        similarity_matrix = cosine_similarity_matrix(
            query_vector,
            self.corpus_embeddings,
        )

        if similarity_matrix.size == 0:
            return []

        # query_vector contains exactly one query.
        scores = similarity_matrix[0]

        top_k = min(
            top_k,
            len(scores),
        )

        top_indices = np.argsort(scores)[::-1][:top_k]

        results: List[Dict[str, Any]] = []

        for index in top_indices:
            chunk = dict(
                self.corpus_chunks[index]
            )

            chunk["score"] = float(
                scores[index]
            )

            chunk["retrieval_method"] = (
                "naive_flat_rag_dense"
            )

            results.append(chunk)

        return results

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate_answer(
        self,
        query: str,
        retrieved_chunks: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Generate an answer directly from retrieved chunks.

        No HART verification or provenance-aware citation generation
        is performed in this baseline.
        """
        if not retrieved_chunks:
            return {
                "query": query,
                "answer_text": (
                    "Không tìm thấy văn bản phù hợp "
                    "để trả lời câu hỏi."
                ),
                "retrieved_chunks": [],
                "pipeline": "naive_flat_rag_baseline",
            }

        context_blocks = []

        for chunk in retrieved_chunks:
            context_blocks.append(
                (
                    f"[Source: "
                    f"{chunk.get('logical_doc_id', 'Doc')} "
                    f"- Article "
                    f"{chunk.get('article_id', '')}]\n"
                    f"{chunk.get('text_content', '')}"
                )
            )

        context_str = "\n\n".join(
            context_blocks
        )

        prompt = (
            "Dựa vào các đoạn văn bản dưới đây, "
            "hãy trả lời câu hỏi pháp lý.\n\n"
            f"VĂN BẢN:\n{context_str}\n\n"
            f"CÂU HỎI: {query}\n\n"
            "CÂU TRẢ LỜI:"
        )

        if self.llm_client is not None:
            response = self.llm_client.generate(
                prompt=prompt,
            )

            if isinstance(response, dict):
                answer_text = response.get(
                    "text",
                    "",
                )
            else:
                answer_text = str(response)

        else:
            first_doc = retrieved_chunks[0].get(
                "logical_doc_id",
                "Văn bản",
            )

            answer_text = (
                f"[Naive RAG Output] "
                f"Dựa trên {first_doc}, "
                "quy định như sau: ..."
            )

        return {
            "query": query,
            "answer_text": answer_text,
            "retrieved_chunks": retrieved_chunks,
            "pipeline": "naive_flat_rag_baseline",
        }