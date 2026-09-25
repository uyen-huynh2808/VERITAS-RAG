import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

try:
    import duckdb

    HAS_DUCKDB = True
except ImportError:
    duckdb = None
    HAS_DUCKDB = False

try:
    import faiss

    HAS_FAISS = True
except ImportError:
    faiss = None
    HAS_FAISS = False


logger = logging.getLogger(__name__)


class LakehouseManager:
    """
    Persistence manager for the Bronze, Silver, and Gold layers
    of VERITAS-RAG.

    Logical hierarchy:

        Logical Document
            |
            +-- Physical PDF Part(s)
                    |
                    +-- Physical Page(s)
                            |
                            +-- Legal Article
                                    |
                                    +-- Page-aware Gold Chunk

    Identity:

        logical_doc_id
            Stable identity of one logical legal document.

        physical_file_id
            SHA-256 hash of one physical PDF.

        doc_version_hash
            Deterministic SHA-256 hash of the ordered physical PDF hashes.

        chunk_hash
            Deterministic SHA-256 hash covering chunk identity,
            source document version, and chunk text.

        physical_page
            Page number inside one physical PDF.

        global_page
            Page number across all physical PDF parts.

    Layer lifecycle:

        RAW PDF
            ↓
        Bronze persistence
            ↓
        Bronze Contract checkpoint
            ↓
        Silver persistence
            ↓
        Silver Contract checkpoint
            ↓
        Gold chunk construction
            ↓
        Gold Contract checkpoint
            ↓
        Gold persistence

    Responsibilities:
    - Persist physical PDF parts into Bronze.
    - Persist parsed pages and normalized articles into Silver.
    - Persist contract-validated Gold chunks.
    - Execute temporal as-of-date queries.
    - Provide page-level lineage queries.
    - Export lakehouse tables and vector metadata.

    Not responsible for:
    - Data Contract validation.
    - Quarantine decisions.
    - PDF parsing.
    - Article extraction.
    - Chunk generation.
    - Embedding generation.
    - Vector retrieval.
    - HART / NLI validation.
    - Pipeline orchestration.

    Contract checkpoints are intentionally handled by the
    orchestration layer. This manager only persists the data it
    receives and provides the storage/query interfaces required
    by the pipeline.
    """

    def __init__(
        self,
        db_path: str,
        bronze_dir: str,
        silver_dir: str,
        gold_dir: str,
    ):
        if not HAS_DUCKDB or duckdb is None:
            raise RuntimeError(
                "DuckDB is not installed. "
                "Please install the 'duckdb' package."
            )

        self.db_path = Path(db_path)
        self.bronze_dir = Path(bronze_dir)
        self.silver_dir = Path(silver_dir)
        self.gold_dir = Path(gold_dir)

        for path in (
            self.db_path.parent,
            self.bronze_dir,
            self.silver_dir,
            self.gold_dir,
        ):
            path.mkdir(
                parents=True,
                exist_ok=True,
            )

        self.conn = duckdb.connect(
            str(self.db_path)
        )

        self._init_lakehouse_schema()

    # ------------------------------------------------------------------
    # Schema initialization
    # ------------------------------------------------------------------

    def _init_lakehouse_schema(self) -> None:
        """
        Initialize the Bronze/Silver/Gold relational schema.

        Existing tables are not destructively migrated here. A clean
        database should be used when changing the schema definition.
        """

        logger.info(
            "Initializing VERITAS-RAG DuckDB lakehouse schema..."
        )

        # --------------------------------------------------------------
        # BRONZE
        # --------------------------------------------------------------

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bronze_pdf_parts (
                logical_doc_id VARCHAR NOT NULL,
                physical_file_id VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,
                part_no INTEGER NOT NULL,
                total_physical_pages INTEGER NOT NULL,
                start_global_page INTEGER NOT NULL,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (
                    logical_doc_id,
                    part_no
                )
            );
            """
        )

        # --------------------------------------------------------------
        # SILVER: pages
        # --------------------------------------------------------------

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS silver_pages (
                logical_doc_id VARCHAR NOT NULL,
                physical_file_id VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,
                part_no INTEGER NOT NULL,
                physical_page INTEGER NOT NULL,
                global_page INTEGER NOT NULL,
                raw_text VARCHAR,
                markdown_content VARCHAR,
                tables_json VARCHAR,
                metadata_json VARCHAR,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                PRIMARY KEY (
                    logical_doc_id,
                    part_no,
                    physical_page
                )
            );
            """
        )

        # --------------------------------------------------------------
        # SILVER: articles
        # --------------------------------------------------------------

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS silver_articles (
                article_id VARCHAR PRIMARY KEY,
                logical_doc_id VARCHAR NOT NULL,

                article_number VARCHAR NOT NULL,
                article_title VARCHAR,
                content VARCHAR NOT NULL,

                issued_date DATE,
                effective_from DATE,
                effective_to DATE,

                status VARCHAR DEFAULT 'active',

                start_physical_file_id VARCHAR NOT NULL,
                start_physical_page INTEGER NOT NULL,

                end_physical_file_id VARCHAR NOT NULL,
                end_physical_page INTEGER NOT NULL,

                start_global_page INTEGER NOT NULL,
                end_global_page INTEGER NOT NULL,

                page_segments_json VARCHAR NOT NULL,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # --------------------------------------------------------------
        # GOLD: chunks
        # --------------------------------------------------------------

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gold_chunks (
                chunk_id VARCHAR PRIMARY KEY,

                article_id VARCHAR NOT NULL,
                logical_doc_id VARCHAR NOT NULL,

                doc_version_hash VARCHAR NOT NULL,
                chunk_hash VARCHAR NOT NULL,

                physical_file_id VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,

                part_no INTEGER NOT NULL,
                physical_page INTEGER NOT NULL,
                global_page INTEGER NOT NULL,

                chunk_index INTEGER NOT NULL,
                text_content VARCHAR NOT NULL,

                issued_date DATE,
                effective_from DATE,
                effective_to DATE,

                status VARCHAR DEFAULT 'active',

                metadata_json VARCHAR,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        logger.info(
            "DuckDB lakehouse schema initialized successfully."
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bronze_destination(
        self,
        logical_doc_id: str,
        part_no: int,
        source: Path,
        physical_file_id: str,
    ) -> Path:
        """
        Build a collision-safe Bronze path.

        Bronze preserves the physical source PDF so that downstream
        Silver and Gold layers can be rebuilt from the retained source.
        """

        document_dir = (
            self.bronze_dir / logical_doc_id
        )

        document_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        return (
            document_dir
            / (
                f"part_{part_no:03d}_"
                f"{physical_file_id[:12]}_"
                f"{source.name}"
            )
        )

    def _get_bronze_file_path(
        self,
        logical_doc_id: str,
        part_no: int,
    ) -> str:
        row = self.conn.execute(
            """
            SELECT file_path
            FROM bronze_pdf_parts
            WHERE logical_doc_id = ?
              AND part_no = ?
            """,
            [
                logical_doc_id,
                part_no,
            ],
        ).fetchone()

        if row is None:
            raise ValueError(
                "Bronze PDF part not found for "
                f"logical_doc_id={logical_doc_id}, "
                f"part_no={part_no}."
            )

        return str(row[0])

    @staticmethod
    def _normalize_metadata_json(
        metadata: Any,
    ) -> str:
        return json.dumps(
            metadata or {},
            ensure_ascii=False,
            default=str,
        )

    # ------------------------------------------------------------------
    # BRONZE
    # ------------------------------------------------------------------

    def insert_bronze_parts(
        self,
        parts_data: List[Dict[str, Any]],
    ) -> None:
        """
        Persist physical PDF parts into Bronze.

        Bronze stores the retained physical source PDFs together with
        their document-level provenance.

        Required fields:

            logical_doc_id
            physical_file_id
            file_path

        Part number is the physical ordering identity inside a
        logical document.

        Contract validation is performed outside this manager.
        In particular, this method does not decide whether a source
        document should be quarantined.
        """

        for part in parts_data:
            logical_doc_id = str(
                part["logical_doc_id"]
            )

            physical_file_id = str(
                part["physical_file_id"]
            )

            source = Path(
                part["file_path"]
            )

            if not source.exists():
                raise FileNotFoundError(
                    f"Source PDF not found: {source}"
                )

            part_no = int(
                part.get("part_no", 1)
            )

            destination = self._bronze_destination(
                logical_doc_id=logical_doc_id,
                part_no=part_no,
                source=source,
                physical_file_id=physical_file_id,
            )

            if source.resolve() != destination.resolve():
                shutil.copy2(
                    source,
                    destination,
                )

            self.conn.execute(
                """
                INSERT OR REPLACE INTO bronze_pdf_parts (
                    logical_doc_id,
                    physical_file_id,
                    file_path,
                    part_no,
                    total_physical_pages,
                    start_global_page
                )
                VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    logical_doc_id,
                    physical_file_id,
                    str(destination),
                    part_no,
                    int(
                        part.get(
                            "total_physical_pages",
                            0,
                        )
                    ),
                    int(
                        part.get(
                            "start_global_page",
                            1,
                        )
                    ),
                ),
            )

        logger.info(
            "Persisted %d physical PDF part(s) into Bronze.",
            len(parts_data),
        )

    # ------------------------------------------------------------------
    # SILVER: pages
    # ------------------------------------------------------------------

    def insert_silver_pages(
        self,
        pages_data: List[Dict[str, Any]],
    ) -> None:
        """
        Persist parsed page-level data into Silver.

        Silver uses the retained Bronze PDF path as the canonical
        physical storage reference.

        This method assumes the caller has already completed the
        Bronze-stage checkpoint and intentionally decided to promote
        the parsed data into Silver.
        """

        for page in pages_data:
            logical_doc_id = page[
                "logical_doc_id"
            ]

            part_no = int(
                page["part_no"]
            )

            bronze_path = self._get_bronze_file_path(
                logical_doc_id=logical_doc_id,
                part_no=part_no,
            )

            tables_json = self._normalize_metadata_json(
                page.get("tables", [])
            )

            metadata_json = self._normalize_metadata_json(
                page.get("metadata", {})
            )

            self.conn.execute(
                """
                INSERT OR REPLACE INTO silver_pages (
                    logical_doc_id,
                    physical_file_id,
                    file_path,
                    part_no,
                    physical_page,
                    global_page,
                    raw_text,
                    markdown_content,
                    tables_json,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    logical_doc_id,
                    page["physical_file_id"],
                    bronze_path,
                    part_no,
                    int(page["physical_page"]),
                    int(page["global_page"]),
                    page.get("raw_text", ""),
                    page.get("markdown_content", ""),
                    tables_json,
                    metadata_json,
                ),
            )

        logger.info(
            "Persisted %d parsed page(s) into Silver.",
            len(pages_data),
        )

    # ------------------------------------------------------------------
    # SILVER: articles
    # ------------------------------------------------------------------

    def insert_silver_articles(
        self,
        articles_data: List[Dict[str, Any]],
    ) -> None:
        """
        Persist normalized legal articles and their page segments
        into Silver.

        Silver is the normalized representation consumed by the
        ChunkBuilder.

        Contract validation is performed by the orchestration layer
        before Gold chunk construction.
        """

        for article in articles_data:
            page_segments = article.get(
                "page_segments",
                [],
            )

            serializable_segments = []

            for segment in page_segments:
                if hasattr(
                    segment,
                    "__dict__",
                ):
                    segment = vars(segment)

                serializable_segments.append(
                    dict(segment)
                )

            page_segments_json = json.dumps(
                serializable_segments,
                ensure_ascii=False,
                default=str,
            )

            self.conn.execute(
                """
                INSERT OR REPLACE INTO silver_articles (
                    article_id,
                    logical_doc_id,
                    article_number,
                    article_title,
                    content,
                    issued_date,
                    effective_from,
                    effective_to,
                    status,
                    start_physical_file_id,
                    start_physical_page,
                    end_physical_file_id,
                    end_physical_page,
                    start_global_page,
                    end_global_page,
                    page_segments_json
                )
                VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?
                );
                """,
                (
                    article["article_id"],
                    article["logical_doc_id"],
                    article["article_number"],
                    article.get(
                        "article_title",
                        "",
                    ),
                    article["content"],
                    article.get("issued_date"),
                    article.get("effective_from"),
                    article.get("effective_to"),
                    article.get(
                        "status",
                        "active",
                    ),
                    article[
                        "start_physical_file_id"
                    ],
                    int(
                        article[
                            "start_physical_page"
                        ]
                    ),
                    article[
                        "end_physical_file_id"
                    ],
                    int(
                        article[
                            "end_physical_page"
                        ]
                    ),
                    int(
                        article[
                            "start_global_page"
                        ]
                    ),
                    int(
                        article[
                            "end_global_page"
                        ]
                    ),
                    page_segments_json,
                ),
            )

        logger.info(
            "Persisted %d article(s) into Silver.",
            len(articles_data),
        )

    # ------------------------------------------------------------------
    # GOLD
    # ------------------------------------------------------------------

    def insert_gold_chunks(
        self,
        chunks_data: List[Dict[str, Any]],
    ) -> None:
        """
        Persist already-built Gold chunks.

        Expected lifecycle:

            Silver
                ↓
            Silver Contract PASS
                ↓
            ChunkBuilder
                ↓
            Gold Contract PASS
                ↓
            insert_gold_chunks()

        This method does not build chunks and does not perform Data
        Contract validation. It persists exactly the chunk records
        supplied by the caller.

        Gold requires:

            chunk_id
            article_id
            logical_doc_id
            doc_version_hash
            chunk_hash
            physical_file_id
            part_no
            physical_page
            global_page
            text_content
        """

        for chunk in chunks_data:
            logical_doc_id = chunk.get(
                "logical_doc_id",
                chunk.get("doc_id"),
            )

            if logical_doc_id is None:
                raise ValueError(
                    "Gold chunk requires logical_doc_id/doc_id."
                )

            doc_version_hash = chunk.get(
                "doc_version_hash"
            )

            if doc_version_hash is None:
                raise ValueError(
                    "Gold chunk requires doc_version_hash."
                )

            chunk_hash = chunk.get(
                "chunk_hash"
            )

            if chunk_hash is None:
                raise ValueError(
                    "Gold chunk requires chunk_hash."
                )

            physical_file_id = chunk.get(
                "physical_file_id"
            )

            if physical_file_id is None:
                raise ValueError(
                    "Gold chunk requires physical_file_id."
                )

            physical_page = chunk.get(
                "physical_page",
                chunk.get("page_number"),
            )

            if physical_page is None:
                raise ValueError(
                    "Gold chunk requires physical_page/page_number."
                )

            global_page = chunk.get(
                "global_page",
                physical_page,
            )

            text_content = chunk.get(
                "text_content",
                chunk.get("text"),
            )

            if text_content is None:
                raise ValueError(
                    "Gold chunk requires text_content/text."
                )

            part_no = int(
                chunk.get(
                    "part_no",
                    1,
                )
            )

            bronze_path = self._get_bronze_file_path(
                logical_doc_id=logical_doc_id,
                part_no=part_no,
            )

            metadata_json = self._normalize_metadata_json(
                chunk.get("metadata", {})
            )

            self.conn.execute(
                """
                INSERT OR REPLACE INTO gold_chunks (
                    chunk_id,
                    article_id,
                    logical_doc_id,
                    doc_version_hash,
                    chunk_hash,
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
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                );
                """,
                (
                    chunk["chunk_id"],
                    chunk["article_id"],
                    logical_doc_id,
                    doc_version_hash,
                    chunk_hash,
                    physical_file_id,
                    bronze_path,
                    part_no,
                    int(physical_page),
                    int(global_page),
                    int(
                        chunk.get(
                            "chunk_index",
                            0,
                        )
                    ),
                    text_content,
                    chunk.get("issued_date"),
                    chunk.get("effective_from"),
                    chunk.get("effective_to"),
                    chunk.get(
                        "status",
                        "active",
                    ),
                    metadata_json,
                ),
            )

        logger.info(
            "Persisted %d validated Gold chunk(s).",
            len(chunks_data),
        )

    # ------------------------------------------------------------------
    # TEMPORAL AS-OF QUERY
    # ------------------------------------------------------------------

    def query_gold_chunks_as_of_date(
        self,
        t_event: str,
        logical_doc_ids: Optional[
            List[str]
        ] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve Gold chunks temporally valid at t_event.

        Validity:

            effective_from <= t_event
            AND
            effective_to > t_event

        A NULL effective_to represents an open-ended validity interval.

        Status is intentionally not used as the temporal predicate.
        """

        query = """
            SELECT
                chunk_id,
                article_id,
                logical_doc_id,
                doc_version_hash,
                chunk_hash,
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
            WHERE effective_from <= ?::DATE
            AND (
                effective_to IS NULL
                OR effective_to > ?::DATE
            )
        """

        params: List[Any] = [
            t_event,
            t_event,
        ]

        if logical_doc_ids:
            placeholders = ", ".join(
                ["?"] * len(logical_doc_ids)
            )

            query += (
                " AND logical_doc_id "
                f"IN ({placeholders})"
            )

            params.extend(
                logical_doc_ids
            )

        query += """
            ORDER BY
                logical_doc_id,
                global_page,
                chunk_index;
        """

        cursor = self.conn.execute(
            query,
            params,
        )

        columns = [
            description[0]
            for description in cursor.description
        ]

        rows = cursor.fetchall()

        results: List[Dict[str, Any]] = []

        for row in rows:
            record = dict(
                zip(columns, row)
            )

            if record.get("metadata_json"):
                record["metadata"] = json.loads(
                    record["metadata_json"]
                )

            results.append(record)

        return results

    # ------------------------------------------------------------------
    # LINEAGE
    # ------------------------------------------------------------------

    def get_full_lineage(
        self,
        chunk_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Trace:

            Gold Chunk
                ↓
            Article
                ↓
            Logical Document
                ↓
            Physical PDF Part
                ↓
            Physical Page

        The lineage query uses the persisted Gold, Silver, and Bronze
        relationships and does not perform any contract validation.
        """

        query = """
            SELECT
                g.chunk_id,
                g.article_id,
                g.logical_doc_id,
                g.doc_version_hash,
                g.chunk_hash,
                g.physical_file_id,
                g.file_path,
                g.part_no,
                g.physical_page,
                g.global_page,

                a.article_number,
                a.article_title,

                p.total_physical_pages,
                p.start_global_page

            FROM gold_chunks AS g

            LEFT JOIN silver_articles AS a
                ON g.article_id = a.article_id

            LEFT JOIN bronze_pdf_parts AS p
                ON g.logical_doc_id = p.logical_doc_id
               AND g.part_no = p.part_no

            WHERE g.chunk_id = ?;
        """

        row = self.conn.execute(
            query,
            [chunk_id],
        ).fetchone()

        if row is None:
            return None

        return {
            "chunk_id": row[0],
            "article_id": row[1],
            "logical_doc_id": row[2],
            "doc_version_hash": row[3],
            "chunk_hash": row[4],
            "physical_file_id": row[5],
            "file_path": row[6],
            "part_no": row[7],
            "physical_page": row[8],
            "global_page": row[9],
            "article_number": row[10],
            "article_title": row[11],
            "total_physical_pages_in_part": row[12],
            "part_start_global_page": row[13],
        }

    # ------------------------------------------------------------------
    # PARQUET EXPORT
    # ------------------------------------------------------------------

    def export_tables_to_parquet(self) -> None:
        """
        Export relational lakehouse layers to Parquet.
        """

        table_mapping = {
            "bronze_pdf_parts": self.bronze_dir,
            "silver_pages": self.silver_dir,
            "silver_articles": self.silver_dir,
            "gold_chunks": self.gold_dir,
        }

        for table, out_dir in table_mapping.items():
            out_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            output = (
                out_dir
                / f"{table}.parquet"
            )

            escaped_output = (
                str(output)
                .replace("'", "''")
            )

            self.conn.execute(
                f"""
                COPY {table}
                TO '{escaped_output}'
                (FORMAT PARQUET);
                """
            )

            logger.info(
                "Exported %s -> %s",
                table,
                output,
            )

    # ------------------------------------------------------------------
    # VECTOR EXPORT
    # ------------------------------------------------------------------

    def export_gold_vectors(
        self,
        chunks: List[Dict[str, Any]],
        embeddings: Any,
        faiss_index: Any = None,
    ) -> None:
        """
        Persist embeddings and the exact chunk ordering used to create
        them.

        The order of chunks in chunk_metadata.json must correspond
        exactly to the first dimension of embeddings.npy and the FAISS
        index.

        Chunk metadata preserves the same provenance identifiers as
        Gold, including doc_version_hash and chunk_hash.
        """

        if len(chunks) != len(embeddings):
            raise ValueError(
                "Number of chunks and embeddings must match."
            )

        embeddings_array = np.asarray(
            embeddings,
            dtype=np.float32,
        )

        if embeddings_array.ndim != 2:
            raise ValueError(
                "Embeddings must be a 2-dimensional array."
            )

        np.save(
            self.gold_dir / "embeddings.npy",
            embeddings_array,
        )

        metadata = []

        for index, chunk in enumerate(chunks):
            if chunk.get("chunk_hash") is None:
                raise ValueError(
                    "Vector metadata requires chunk_hash."
                )

            metadata.append(
                {
                    "vector_index": index,
                    "chunk_id": chunk["chunk_id"],
                    "logical_doc_id": chunk[
                        "logical_doc_id"
                    ],
                    "doc_version_hash": chunk.get(
                        "doc_version_hash"
                    ),
                    "chunk_hash": chunk[
                        "chunk_hash"
                    ],
                    "article_id": chunk[
                        "article_id"
                    ],
                    "physical_file_id": chunk.get(
                        "physical_file_id"
                    ),
                    "part_no": chunk.get(
                        "part_no"
                    ),
                    "physical_page": chunk[
                        "physical_page"
                    ],
                    "global_page": chunk[
                        "global_page"
                    ],
                    "text_content": chunk[
                        "text_content"
                    ],
                }
            )

        with open(
            self.gold_dir / "chunk_metadata.json",
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                metadata,
                file,
                ensure_ascii=False,
                indent=2,
            )

        if HAS_FAISS and faiss_index is not None:
            if faiss_index.ntotal != len(chunks):
                raise ValueError(
                    "FAISS index size does not match "
                    "the number of chunks."
                )

            faiss.write_index(
                faiss_index,
                str(
                    self.gold_dir
                    / "faiss.index"
                ),
            )

    # ------------------------------------------------------------------
    # CONNECTION
    # ------------------------------------------------------------------

    def close(self) -> None:
        """
        Close DuckDB safely.
        """

        if getattr(
            self,
            "conn",
            None,
        ) is not None:
            self.conn.close()
            self.conn = None

            logger.info(
                "DuckDB connection closed."
            )