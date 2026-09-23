import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import duckdb

    HAS_DUCKDB = True
except ImportError:
    duckdb = None
    HAS_DUCKDB = False


logger = logging.getLogger(__name__)


class LakehouseManager:
    """
    Manager for the Bronze, Silver, and Gold layers of the
    VERITAS-RAG lakehouse.

    Storage architecture:

        Bronze:
            Physical PDF metadata and provenance.

        Silver:
            Parsed pages and structured legal articles.

        Gold:
            Retrieval-ready chunks with embeddings and complete
            page-level provenance.

    The manager preserves the distinction between:

        logical_doc_id
            One logical legal document.

        physical_file_id
            SHA-256 hash of one physical PDF file.

        physical_page
            Page number inside one physical PDF.

        global_page
            Page number across the complete logical document.
    """

    def __init__(
        self,
        db_path: str = "data/silver_parquet/lakehouse.duckdb",
        parquet_dir: str = "data/silver_parquet",
    ):
        if not HAS_DUCKDB or duckdb is None:
            raise RuntimeError(
                "DuckDB is not installed. "
                "Please install the 'duckdb' package."
            )

        self.db_path = Path(db_path)
        self.parquet_dir = Path(parquet_dir)

        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self.parquet_dir.mkdir(
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
        Initialize Bronze, Silver, and Gold tables.

        The schema follows the project's logical/physical document
        lineage model.
        """

        logger.info(
            "Initializing VERITAS-RAG DuckDB lakehouse schema..."
        )

        # --------------------------------------------------------------
        # BRONZE
        # --------------------------------------------------------------
        #
        # Physical PDF identity and storage provenance.
        #
        # The actual PDF remains under data/bronze_pdf.
        # DuckDB stores its metadata and lineage identity.
        #
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
                    physical_file_id
                )
            );
            """
        )

        # --------------------------------------------------------------
        # SILVER: parsed pages
        # --------------------------------------------------------------
        #
        # This schema directly corresponds to ParsedPage.
        #
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
                    physical_file_id,
                    physical_page
                )
            );
            """
        )

        # --------------------------------------------------------------
        # SILVER: legal articles
        # --------------------------------------------------------------
        #
        # Article-level structure is needed for:
        #
        #   chunk -> article -> document -> physical PDF -> page
        #
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

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        # --------------------------------------------------------------
        # GOLD: retrieval chunks
        # --------------------------------------------------------------
        #
        # This intentionally keeps a single physical/global page
        # reference because the current DocumentChunk model exposes
        # one page_number.
        #
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gold_chunks (
                chunk_id VARCHAR PRIMARY KEY,

                article_id VARCHAR NOT NULL,
                logical_doc_id VARCHAR NOT NULL,

                physical_file_id VARCHAR NOT NULL,
                file_path VARCHAR NOT NULL,

                part_no INTEGER NOT NULL,
                physical_page INTEGER NOT NULL,
                global_page INTEGER NOT NULL,

                chunk_index INTEGER NOT NULL,
                text_content VARCHAR NOT NULL,

                embedding FLOAT[],

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
    # BRONZE
    # ------------------------------------------------------------------

    def insert_bronze_parts(
        self,
        parts_data: List[Dict[str, Any]],
    ) -> None:
        """
        Insert physical PDF parts into Bronze.

        Expected fields correspond to PhysicalPDFPart plus
        logical_doc_id and physical_file_id.
        """

        for part in parts_data:
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
                    part["logical_doc_id"],
                    part["physical_file_id"],
                    part["file_path"],
                    part.get("part_no", 1),
                    part.get("total_physical_pages", 0),
                    part.get("start_global_page", 1),
                ),
            )

    # ------------------------------------------------------------------
    # SILVER: pages
    # ------------------------------------------------------------------

    def insert_silver_pages(
        self,
        pages_data: List[Dict[str, Any]],
    ) -> None:
        """
        Insert ParsedPage-compatible records into Silver.
        """

        for page in pages_data:
            tables_json = json.dumps(
                page.get("tables", []),
                ensure_ascii=False,
                default=str,
            )

            metadata_json = json.dumps(
                page.get("metadata", {}),
                ensure_ascii=False,
                default=str,
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
                    page["logical_doc_id"],
                    page["physical_file_id"],
                    page["file_path"],
                    page["part_no"],
                    page["physical_page"],
                    page["global_page"],
                    page.get("raw_text", ""),
                    page.get("markdown_content", ""),
                    tables_json,
                    metadata_json,
                ),
            )

    # ------------------------------------------------------------------
    # SILVER: articles
    # ------------------------------------------------------------------

    def insert_silver_articles(
        self,
        articles_data: List[Dict[str, Any]],
    ) -> None:
        """
        Insert structured legal articles into Silver.

        Article-level extraction is intentionally separate from
        PDF parsing because PDFParser is responsible for page-level
        extraction and provenance.
        """

        for article in articles_data:
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
                    end_global_page
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                );
                """,
                (
                    article["article_id"],
                    article["logical_doc_id"],
                    article["article_number"],
                    article.get("article_title", ""),
                    article["content"],
                    article.get("issued_date"),
                    article.get("effective_from"),
                    article.get("effective_to"),
                    article.get("status", "active"),
                    article["start_physical_file_id"],
                    article["start_physical_page"],
                    article["end_physical_file_id"],
                    article["end_physical_page"],
                    article["start_global_page"],
                    article["end_global_page"],
                ),
            )

    # ------------------------------------------------------------------
    # GOLD
    # ------------------------------------------------------------------

    def insert_gold_chunks(
        self,
        chunks_data: List[Dict[str, Any]],
    ) -> None:
        """
        Insert retrieval-ready chunks into Gold.

        Expected fields are compatible with the current
        DocumentChunk lineage model:

            text
            doc_id/logical_doc_id
            page_number
            doc_version_hash/physical_file_id

        Additional lineage fields are retained for the lakehouse.
        """

        for chunk in chunks_data:
            metadata_json = json.dumps(
                chunk.get("metadata", {}),
                ensure_ascii=False,
                default=str,
            )

            # Support both the current internal naming and the
            # existing DocumentChunk naming where possible.
            logical_doc_id = chunk.get(
                "logical_doc_id",
                chunk.get("doc_id"),
            )

            physical_file_id = chunk.get(
                "physical_file_id",
                chunk.get("doc_version_hash"),
            )

            physical_page = chunk.get(
                "physical_page",
                chunk.get("page_number"),
            )

            text_content = chunk.get(
                "text_content",
                chunk.get("text"),
            )

            if logical_doc_id is None:
                raise ValueError(
                    "Gold chunk requires logical_doc_id/doc_id."
                )

            if physical_file_id is None:
                raise ValueError(
                    "Gold chunk requires "
                    "physical_file_id/doc_version_hash."
                )

            if physical_page is None:
                raise ValueError(
                    "Gold chunk requires "
                    "physical_page/page_number."
                )

            if text_content is None:
                raise ValueError(
                    "Gold chunk requires text_content/text."
                )

            self.conn.execute(
                """
                INSERT OR REPLACE INTO gold_chunks (
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
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                );
                """,
                (
                    chunk["chunk_id"],
                    chunk["article_id"],
                    logical_doc_id,
                    physical_file_id,
                    chunk["file_path"],
                    chunk.get("part_no", 1),
                    physical_page,
                    chunk.get(
                        "global_page",
                        physical_page,
                    ),
                    chunk.get("chunk_index", 0),
                    text_content,
                    chunk.get("embedding", []),
                    chunk.get("issued_date"),
                    chunk.get("effective_from"),
                    chunk.get("effective_to"),
                    chunk.get("status", "active"),
                    metadata_json,
                ),
            )

    # ------------------------------------------------------------------
    # TEMPORAL AS-OF QUERY
    # ------------------------------------------------------------------

    def query_gold_chunks_as_of_date(
        self,
        t_event: str,
        logical_doc_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve chunks that are temporally valid at t_event.

        Temporal validity is determined by:

            effective_from <= t_event
            AND
            (
                effective_to IS NULL
                OR effective_to >= t_event
            )

        Current status is NOT used as the primary temporal filter.

        This is important because a document may currently be
        'replaced' or 'revoked' while still being historically valid
        for an earlier t_event.
        """

        query = """
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
            WHERE effective_from <= ?::DATE
              AND (
                    effective_to IS NULL
                    OR effective_to >= ?::DATE
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

            params.extend(logical_doc_ids)

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

            Chunk
              ↓
            Article
              ↓
            Logical Document
              ↓
            Physical PDF
              ↓
            Physical Page / Global Page
        """

        query = """
            SELECT
                g.chunk_id,
                g.article_id,
                g.logical_doc_id,
                g.physical_file_id,
                g.file_path,
                g.part_no,
                g.physical_page,
                g.global_page,

                a.article_number,
                a.article_title,

                p.total_physical_pages

            FROM gold_chunks AS g

            LEFT JOIN silver_articles AS a
                ON g.article_id = a.article_id

            LEFT JOIN bronze_pdf_parts AS p
                ON g.logical_doc_id = p.logical_doc_id
               AND g.physical_file_id = p.physical_file_id

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
            "physical_file_id": row[3],
            "file_path": row[4],
            "part_no": row[5],
            "physical_page": row[6],
            "global_page": row[7],
            "article_number": row[8],
            "article_title": row[9],
            "total_physical_pages_in_part": row[10],
        }

    # ------------------------------------------------------------------
    # PARQUET EXPORT
    # ------------------------------------------------------------------

    def export_tables_to_parquet(self) -> None:
        """
        Export DuckDB tables to Parquet.

        DuckDB remains the query/management engine while Parquet
        provides the lakehouse storage representation.
        """

        tables = [
            "bronze_pdf_parts",
            "silver_pages",
            "silver_articles",
            "gold_chunks",
        ]

        for table in tables:
            output_file = (
                self.parquet_dir
                / f"{table}.parquet"
            )

            # Escape single quotes for DuckDB SQL literals.
            output_path = str(
                output_file
            ).replace("'", "''")

            self.conn.execute(
                f"""
                COPY {table}
                TO '{output_path}'
                (FORMAT PARQUET);
                """
            )

            logger.info(
                "Exported table '%s' to Parquet: %s",
                table,
                output_file,
            )

    # ------------------------------------------------------------------
    # CONNECTION
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the DuckDB connection safely."""

        if getattr(self, "conn", None) is not None:
            self.conn.close()
            self.conn = None

            logger.info(
                "DuckDB connection closed."
            )