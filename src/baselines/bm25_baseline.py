import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional dependencies
# ---------------------------------------------------------------------------

try:
    from rank_bm25 import BM25Okapi
except ImportError as exc:
    BM25Okapi = None
    _BM25_IMPORT_ERROR = exc


TOKENIZER_ENGINE = "whitespace"

try:
    from pyvi import ViTokenizer

    TOKENIZER_ENGINE = "pyvi"
except ImportError:
    try:
        import underthesea

        TOKENIZER_ENGINE = "underthesea"
    except ImportError:
        pass


logger.info(
    "BM25 baseline initialized with tokenizer engine: '%s'",
    TOKENIZER_ENGINE,
)


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

def tokenize_vietnamese_text(text: str) -> List[str]:
    """
    Tokenize Vietnamese legal text into normalized word tokens.

    PyVi is preferred, followed by Underthesea. A regex-based tokenizer
    is used as a lightweight fallback when neither NLP package is available.
    """
    if not text:
        return []

    cleaned_text = text.lower().strip()

    if TOKENIZER_ENGINE == "pyvi":
        tokenized = ViTokenizer.tokenize(cleaned_text)
        return tokenized.split()

    if TOKENIZER_ENGINE == "underthesea":
        words = underthesea.word_tokenize(
            cleaned_text,
            format="text",
        )
        return words.split()

    return re.findall(r"\w+", cleaned_text, flags=re.UNICODE)


# ---------------------------------------------------------------------------
# BM25 baseline
# ---------------------------------------------------------------------------

class BM25Baseline:
    """
    Standard BM25 lexical retrieval baseline.

    This baseline intentionally does NOT perform:
    - contract validation
    - temporal as-of filtering
    - HART verification
    - lineage tracing

    It operates directly over the indexed Gold chunks and retrieves
    documents using lexical BM25 similarity only.
    """

    def __init__(
        self,
        k1: float = 1.5,
        b: float = 0.75,
    ):
        if BM25Okapi is None:
            raise ImportError(
                "The 'rank_bm25' package is required to run the BM25 "
                "baseline. Install it with: pip install rank-bm25"
            ) from _BM25_IMPORT_ERROR

        self.k1 = k1
        self.b = b

        self.bm25_index: Optional[BM25Okapi] = None
        self.corpus_chunks: List[Dict[str, Any]] = []
        self.tokenized_corpus: List[List[str]] = []

    def index_documents(
        self,
        chunks: List[Dict[str, Any]],
    ) -> None:
        """
        Build a BM25 index over legal text chunks.

        Expected chunk fields include:
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
                "BM25 indexer received an empty document list."
            )
            self.corpus_chunks = []
            self.tokenized_corpus = []
            self.bm25_index = None
            return

        self.corpus_chunks = [dict(chunk) for chunk in chunks]

        self.tokenized_corpus = [
            tokenize_vietnamese_text(
                chunk.get("text_content", "")
            )
            for chunk in self.corpus_chunks
        ]

        self.bm25_index = BM25Okapi(
            self.tokenized_corpus,
            k1=self.k1,
            b=self.b,
        )

        logger.info(
            "Indexed %d chunks with BM25.",
            len(self.corpus_chunks),
        )

    def index_from_lakehouse(
        self,
        lakehouse_manager: Any,
    ) -> None:
        """
        Load Gold chunks from the DuckDB lakehouse and build the BM25 index.

        No temporal filtering is applied because this is an intentionally
        non-temporal lexical baseline.
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

        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()

        chunks = [
            dict(zip(columns, row))
            for row in rows
        ]

        logger.info(
            "Loaded %d Gold chunks from lakehouse for BM25 indexing.",
            len(chunks),
        )

        self.index_documents(chunks)

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        t_event: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve the top-k chunks using lexical BM25 scoring.

        Args:
            query:
                User query.

            top_k:
                Number of chunks to return.

            t_event:
                Accepted for interface compatibility with temporal
                retrievers, but intentionally ignored by this baseline.

        Returns:
            Ranked chunk dictionaries with an additional `score` field.
        """
        if top_k <= 0:
            return []

        if t_event is not None:
            logger.debug(
                "BM25 baseline intentionally ignores t_event=%s.",
                t_event,
            )

        if self.bm25_index is None:
            logger.error(
                "BM25 index is empty. Call index_documents() first."
            )
            return []

        tokenized_query = tokenize_vietnamese_text(query)

        if not tokenized_query:
            return []

        scores = self.bm25_index.get_scores(tokenized_query)

        scored_results: List[Dict[str, Any]] = []

        for index, score in enumerate(scores):
            if score <= 0:
                continue

            chunk = dict(self.corpus_chunks[index])
            chunk["score"] = float(score)
            chunk["retrieval_method"] = "bm25_baseline"

            scored_results.append(chunk)

        scored_results.sort(
            key=lambda item: item["score"],
            reverse=True,
        )

        return scored_results[:top_k]
