import json
import hashlib
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from src.contracts.models import ProvisionRecord

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

        Create the current schema. Legacy schema upgrades and backfills
        are intentionally handled by rebuilding from source documents.
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

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS silver_provisions (
                provision_id VARCHAR PRIMARY KEY,
                logical_doc_id VARCHAR NOT NULL,
                article_id VARCHAR NOT NULL,
                parent_provision_id VARCHAR,
                level VARCHAR NOT NULL,
                provision_number VARCHAR NOT NULL,
                provision_path VARCHAR NOT NULL,
                title VARCHAR,
                content VARCHAR NOT NULL,
                issued_date DATE,
                effective_from DATE,
                effective_to DATE,
                effectivity_mode VARCHAR NOT NULL,
                trigger_reference VARCHAR,
                status VARCHAR,
                start_physical_file_id VARCHAR,
                start_physical_page INTEGER,
                end_physical_file_id VARCHAR,
                end_physical_page INTEGER,
                start_global_page INTEGER,
                end_global_page INTEGER,
                page_segments_json VARCHAR NOT NULL,
                text_start INTEGER,
                text_end INTEGER,
                UNIQUE(logical_doc_id, provision_path)
            );
            """
        )

        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS silver_effectivity_rules (
                rule_id VARCHAR PRIMARY KEY,
                logical_doc_id VARCHAR NOT NULL,
                target_path VARCHAR NOT NULL,
                mode VARCHAR NOT NULL,
                effective_from DATE,
                effective_to DATE,
                trigger_reference VARCHAR,
                source_text VARCHAR NOT NULL,
                source_article_id VARCHAR,
                source_pages_json VARCHAR
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
                provision_id VARCHAR,
                provision_path VARCHAR,
                effectivity_mode VARCHAR,
                trigger_reference VARCHAR,

                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        self._validate_current_schema()

        logger.info(
            "DuckDB lakehouse schema initialized successfully."
        )

    def _validate_current_schema(self) -> None:
        """Fail fast on an old database; this project rebuilds rather than migrates."""
        required_columns = {
            "silver_provisions": {
                "provision_id",
                "parent_provision_id",
                "level",
                "provision_path",
                "effectivity_mode",
            },
            "silver_effectivity_rules": {
                "source_article_id",
                "source_pages_json",
            },
            "gold_chunks": {
                "provision_id",
                "provision_path",
                "effectivity_mode",
                "trigger_reference",
            },
        }
        incompatible = {}
        for table, expected in required_columns.items():
            actual = {
                row[1]
                for row in self.conn.execute(
                    f"PRAGMA table_info('{table}')"
                ).fetchall()
            }
            missing = expected - actual
            if missing:
                incompatible[table] = sorted(missing)
        if incompatible:
            raise RuntimeError(
                "The configured DuckDB uses an incompatible legacy schema. "
                "This pipeline does not migrate old databases; rebuild the "
                "lakehouse from source documents. Missing columns: "
                f"{incompatible}"
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

    def insert_silver_provisions(
        self,
        provisions: List[Dict[str, Any]],
        effectivity_rules: List[Dict[str, Any]],
        logical_doc_id: str,
    ) -> None:
        """Persist hierarchical provisions and their source effectivity rules."""
        provision_ids = {row.get("provision_id") for row in provisions}
        paths = [row.get("path") for row in provisions]
        if None in provision_ids or len(provision_ids) != len(provisions):
            raise ValueError("Silver provisions require unique provision IDs.")
        if len(set(paths)) != len(paths):
            raise ValueError("Silver provision paths must be unique per document.")

        by_id = {row["provision_id"]: row for row in provisions}
        by_path = {row["path"]: row for row in provisions}
        allowed_levels = {"article", "clause", "point"}
        allowed_modes = {"inherit", "explicit_date", "external_trigger", "unknown"}
        for provision in provisions:
            ProvisionRecord.model_validate(provision)
            if provision.get("level") not in allowed_levels:
                raise ValueError(
                    f"Unsupported provision level: {provision.get('level')}"
                )
            parent_id = provision.get("parent_provision_id")
            if parent_id is not None and parent_id not in by_id:
                raise ValueError(
                    f"Missing parent provision {parent_id} for {provision['provision_id']}"
                )
            parent = by_id.get(parent_id) if parent_id else None
            if provision["level"] == "article":
                if parent is not None or provision["path"] != provision["number"]:
                    raise ValueError("Article provisions must be hierarchy roots.")
            elif provision["level"] == "clause":
                if (
                    parent is None
                    or parent["level"] != "article"
                    or provision["path"] != f"{parent['path']}.{provision['number']}"
                ):
                    raise ValueError("Clause provision has an invalid parent/path.")
            elif provision["level"] == "point":
                if (
                    parent is None
                    or parent["level"] != "clause"
                    or provision["path"] != f"{parent['path']}.{provision['number']}"
                ):
                    raise ValueError("Point provision has an invalid parent/path.")
            if provision.get("effectivity_mode") not in allowed_modes:
                raise ValueError(
                    f"Unsupported effectivity mode: {provision.get('effectivity_mode')}"
                )

        unresolved_targets = sorted(
            {
                rule.get("target_path")
                for rule in effectivity_rules
                if rule.get("target_path") != "*"
                and rule.get("target_path") not in by_path
            }
        )
        if unresolved_targets:
            raise ValueError(
                "Effectivity rule targets were not found in the extracted "
                f"provision tree: {unresolved_targets}"
            )

        rules_by_target: Dict[str, set] = {}
        for rule in effectivity_rules:
            mode = rule.get("mode")
            if mode == "explicit_date" and not rule.get("effective_from"):
                raise ValueError("Explicit effectivity rules require effective_from.")
            if mode == "external_trigger" and not rule.get("trigger_reference"):
                raise ValueError("External effectivity rules require a source trigger.")
            signature = (
                mode,
                str(rule.get("effective_from") or ""),
                str(rule.get("effective_to") or ""),
                str(rule.get("trigger_reference") or ""),
            )
            rules_by_target.setdefault(rule.get("target_path", "*"), set()).add(signature)
        conflicts = sorted(
            target for target, signatures in rules_by_target.items()
            if len(signatures) > 1
        )
        if conflicts:
            raise ValueError(
                "Conflicting effectivity rules require manual resolution for "
                f"provisions: {conflicts}"
            )

        for provision in provisions:
            segments = json.dumps(
                provision.get("page_segments") or [],
                ensure_ascii=False,
                default=str,
            )
            self.conn.execute(
                """
                INSERT INTO silver_provisions (
                    provision_id, logical_doc_id, article_id,
                    parent_provision_id, level, provision_number,
                    provision_path, title, content, issued_date,
                    effective_from, effective_to, effectivity_mode,
                    trigger_reference, status, start_physical_file_id,
                    start_physical_page, end_physical_file_id,
                    end_physical_page, start_global_page, end_global_page,
                    page_segments_json, text_start, text_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (provision_id) DO UPDATE SET
                    logical_doc_id = excluded.logical_doc_id,
                    article_id = excluded.article_id,
                    parent_provision_id = excluded.parent_provision_id,
                    level = excluded.level,
                    provision_number = excluded.provision_number,
                    provision_path = excluded.provision_path,
                    title = excluded.title,
                    content = excluded.content,
                    issued_date = excluded.issued_date,
                    effective_from = excluded.effective_from,
                    effective_to = excluded.effective_to,
                    effectivity_mode = excluded.effectivity_mode,
                    trigger_reference = excluded.trigger_reference,
                    status = excluded.status,
                    start_physical_file_id = excluded.start_physical_file_id,
                    start_physical_page = excluded.start_physical_page,
                    end_physical_file_id = excluded.end_physical_file_id,
                    end_physical_page = excluded.end_physical_page,
                    start_global_page = excluded.start_global_page,
                    end_global_page = excluded.end_global_page,
                    page_segments_json = excluded.page_segments_json,
                    text_start = excluded.text_start,
                    text_end = excluded.text_end
                """,
                (
                    provision["provision_id"], logical_doc_id,
                    provision["article_id"], provision.get("parent_provision_id"),
                    provision["level"], provision["number"], provision["path"],
                    provision.get("title"), provision.get("content", ""),
                    provision.get("issued_date"), provision.get("effective_from"),
                    provision.get("effective_to"), provision.get("effectivity_mode", "inherit"),
                    provision.get("trigger_reference"), provision.get("status"),
                    provision.get("start_physical_file_id"),
                    provision.get("start_physical_page"),
                    provision.get("end_physical_file_id"),
                    provision.get("end_physical_page"),
                    provision.get("start_global_page"),
                    provision.get("end_global_page"), segments,
                    provision.get("text_start"), provision.get("text_end"),
                ),
            )

        for rule in effectivity_rules:
            if rule.get("mode") not in {"explicit_date", "external_trigger", "unknown"}:
                raise ValueError(f"Unsupported effectivity rule mode: {rule.get('mode')}")
            source_text = str(rule.get("source_text") or "")
            rule_identity = "\n".join(
                (
                    logical_doc_id,
                    str(rule.get("target_path", "*")),
                    str(rule.get("mode", "unknown")),
                    str(rule.get("effective_from") or ""),
                    str(rule.get("effective_to") or ""),
                    source_text,
                )
            )
            rule_id = hashlib.sha256(rule_identity.encode("utf-8")).hexdigest()
            self.conn.execute(
                """
                INSERT OR REPLACE INTO silver_effectivity_rules (
                    rule_id, logical_doc_id, target_path, mode,
                    effective_from, effective_to, trigger_reference,
                    source_text, source_article_id, source_pages_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id, logical_doc_id, rule.get("target_path", "*"),
                    rule.get("mode", "unknown"), rule.get("effective_from"),
                    rule.get("effective_to"), rule.get("trigger_reference"),
                    source_text,
                    rule.get("source_article_id"),
                    json.dumps(rule.get("source_pages") or []),
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

            if not chunk.get("provision_id"):
                raise ValueError("Gold chunk requires provision_id.")
            if chunk.get("effectivity_mode") not in {
                "inherit", "explicit_date", "external_trigger", "unknown"
            }:
                raise ValueError("Gold chunk has an invalid effectivity_mode.")
            provision_exists = self.conn.execute(
                "SELECT 1 FROM silver_provisions WHERE provision_id = ?",
                [chunk["provision_id"]],
            ).fetchone()
            if provision_exists is None:
                raise ValueError(
                    "Gold chunk references a missing Silver provision: "
                    f"{chunk['provision_id']}"
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
                    metadata_json,
                    provision_id,
                    provision_path,
                    effectivity_mode,
                    trigger_reference
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
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
                    chunk["provision_id"],
                    chunk.get("provision_path"),
                    chunk.get("effectivity_mode"),
                    chunk.get("trigger_reference"),
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
                metadata_json,
                provision_id,
                provision_path,
                effectivity_mode,
                trigger_reference
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

                a.provision_number,
                a.title,

                g.provision_id,
                g.provision_path,
                g.effectivity_mode,
                g.trigger_reference,
                v.level,
                v.title,
                v.parent_provision_id,

                p.total_physical_pages,
                p.start_global_page

            FROM gold_chunks AS g

            LEFT JOIN silver_provisions AS a
                ON g.article_id = a.article_id
               AND a.level = 'article'

            LEFT JOIN silver_provisions AS v
                ON g.provision_id = v.provision_id

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
            "provision_id": row[12],
            "provision_path": row[13],
            "effectivity_mode": row[14],
            "trigger_reference": row[15],
            "provision_level": row[16],
            "provision_title": row[17],
            "parent_provision_id": row[18],
            "total_physical_pages_in_part": row[19],
            "part_start_global_page": row[20],
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
            "silver_provisions": self.silver_dir,
            "silver_effectivity_rules": self.silver_dir,
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
