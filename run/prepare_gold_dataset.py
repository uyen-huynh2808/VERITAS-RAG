from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from src.core import load_system_config, setup_environment
from src.contracts import ExecutableDocumentContract
from src.database.lakehouse_manager import LakehouseManager
from src.ingestion.pdf_parser import PDFParser
from src.rag.chunk_builder import ChunkBuilder


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BenchmarkDatasetBuilder:
    """
    Materialize benchmark documents through the VERITAS-RAG ingestion
    pipeline up to Gold.

    Pipeline:

        RAW PDF
            ↓
        PRE-PARSE CONTRACT
            ├── Schema
            └── Document Provenance
            ↓
        FAIL → QUARANTINE / STOP

        PASS
            ↓
        PDFParser
            ↓
        BRONZE
            ↓
        BRONZE CONTRACT CHECKPOINT
            ├── Schema
            └── Document Provenance
            ↓
        FAIL → BLOCK SILVER PROMOTION

        PASS
            ↓
        SILVER PAGES
        SILVER ARTICLES
            ↓
        SILVER CONTRACT CHECKPOINT
            ├── Temporal
            └── Quality
            ↓
        FAIL → BLOCK GOLD PROMOTION

        PASS
            ↓
        CHUNK BUILDER
            ↓
        GOLD CONTRACT CHECKPOINT
            └── Chunk Provenance / Final Integrity
            ↓
        FAIL → BLOCK TRUSTED GOLD

        PASS
            ↓
        GOLD
            ↓
        Benchmark Skeleton

    Important:
    - The Data Contract is enforced progressively across the
      Medallion lifecycle.
    - Bronze preserves the parsed/source representation needed for
      replay and audit.
    - Silver is the validated representation used as the direct
      input to chunk generation.
    - Gold contains only chunks that have passed the Gold checkpoint.
    - No synthetic benchmark chunks are created.
    - Benchmark answers and citation annotations remain manual
      annotation tasks.
    """

    def __init__(self) -> None:
        self.config = load_system_config()
        setup_environment(self.config)

        self.contract = ExecutableDocumentContract(
            self.config
        )

        self.rules = self.contract.rules
        hash_algorithm = self.rules.provenance_contract.hash_algorithm
        self.parser = PDFParser(
            prefer_docling=self.config.parser.prefer_docling,
            hash_algorithm=hash_algorithm,
            header_ocr_engine=self.config.parser.header_ocr_engine,
        )

        self.lakehouse = LakehouseManager(
            db_path=self.config.db.duckdb_path,
            bronze_dir=self.config.db.bronze_dir,
            silver_dir=self.config.db.silver_dir,
            gold_dir=self.config.db.gold_dir,
        )

        self.chunk_builder = ChunkBuilder(
            lakehouse=self.lakehouse,
            chunk_size=self.config.chunking.max_tokens,
            chunk_overlap=self.config.chunking.overlap,
            hash_algorithm=hash_algorithm,
        )

    # ------------------------------------------------------------------
    # DOCUMENT VERSION HASH
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_file_hash(
        storage_path: str,
        algorithm: str,
    ) -> str:
        """
        Compute the physical PDF hash before parsing.

        The hash is computed from the exact file referenced by
        metadata.storage_path so that the pre-parse provenance contract
        can validate document identity before Bronze persistence.
        """

        path = Path(storage_path)

        if not path.exists():
            raise FileNotFoundError(
                f"Storage path does not exist: {storage_path}"
            )

        if not path.is_file():
            raise ValueError(
                f"Storage path is not a file: {storage_path}"
            )

        try:
            hasher = hashlib.new(algorithm)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported hash algorithm: {algorithm}"
            ) from exc

        with path.open("rb") as file:
            for block in iter(
                lambda: file.read(1024 * 1024),
                b"",
            ):
                hasher.update(block)

        return hasher.hexdigest()

    @staticmethod
    def _get_doc_version_hash(
        parsed: Any,
    ) -> str:
        """
        Return the document version hash from the parsed physical part.

        The current benchmark pipeline supports exactly one physical
        PDF part per logical document because DocumentMetadata currently
        contains one canonical storage_path and document provenance hash.
        """

        if not parsed.parts:
            raise ValueError(
                "Parsed logical document contains no physical PDF parts."
            )

        if len(parsed.parts) != 1:
            raise ValueError(
                "BenchmarkDatasetBuilder currently requires exactly one "
                "physical PDF part per logical document because the current "
                "DocumentMetadata provenance contract uses a single "
                "storage_path."
            )

        return parsed.parts[0].file_hash

    # ------------------------------------------------------------------
    # SILVER PROVISION LOADING
    # ------------------------------------------------------------------

    def _get_silver_provisions(
        self,
        logical_doc_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Load the actual Silver provisions that will become ChunkBuilder
        input.

        Chunk generation deliberately reads from Silver rather than
        directly from parser output so that the pipeline follows:

            Parser → Bronze → Silver → ChunkBuilder → Gold
        """

        cursor = self.lakehouse.conn.execute(
            """
            SELECT
                provision_id,
                article_id,
                logical_doc_id,
                parent_provision_id,
                level,
                provision_number,
                provision_path,
                title,
                content,
                issued_date,
                effective_from,
                effective_to,
                effectivity_mode,
                trigger_reference,
                status,
                start_physical_file_id,
                start_physical_page,
                end_physical_file_id,
                end_physical_page,
                start_global_page,
                end_global_page,
                page_segments_json
            FROM silver_provisions
            WHERE logical_doc_id = ?
            ORDER BY
                start_global_page,
                provision_path;
            """,
            [logical_doc_id],
        )

        rows = cursor.fetchall()

        columns = [
            description[0]
            for description in cursor.description
        ]

        provisions: List[Dict[str, Any]] = []

        for row in rows:
            record = dict(
                zip(columns, row)
            )

            page_segments_json = record.get(
                "page_segments_json"
            )

            if page_segments_json:
                try:
                    page_segments = json.loads(
                        page_segments_json
                    )
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        "Invalid page_segments_json in Silver "
                        f"for provision {record.get('provision_id')}."
                    ) from exc
            else:
                page_segments = []

            provisions.append(
                {
                    "provision_id": record["provision_id"],
                    "article_id": record["article_id"],
                    "parent_provision_id": record["parent_provision_id"],
                    "logical_doc_id": record["logical_doc_id"],
                    "level": record["level"],
                    "provision_number": record["provision_number"],
                    "provision_path": record["provision_path"],
                    "title": record["title"],
                    "content": record["content"],
                    "issued_date": record["issued_date"],
                    "effective_from": record[
                        "effective_from"
                    ],
                    "effective_to": record[
                        "effective_to"
                    ],
                    "effectivity_mode": record["effectivity_mode"],
                    "trigger_reference": record["trigger_reference"],
                    "status": record["status"],
                    "start_physical_file_id": record[
                        "start_physical_file_id"
                    ],
                    "start_physical_page": record[
                        "start_physical_page"
                    ],
                    "end_physical_file_id": record[
                        "end_physical_file_id"
                    ],
                    "end_physical_page": record[
                        "end_physical_page"
                    ],
                    "start_global_page": record[
                        "start_global_page"
                    ],
                    "end_global_page": record[
                        "end_global_page"
                    ],
                    "page_segments": page_segments,
                }
            )

        return provisions

    # ------------------------------------------------------------------
    # GOLD LOOKUP
    # ------------------------------------------------------------------

    def _get_gold_chunks(
        self,
        logical_doc_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Read the actual Gold chunks materialized by ChunkBuilder.

        Gold is the source of truth for benchmark chunk IDs.
        """

        cursor = self.lakehouse.conn.execute(
            """
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
                provision_id,
                provision_path,
                issued_date,
                effective_from,
                effective_to,
                status,
                metadata_json
            FROM gold_chunks
            WHERE logical_doc_id = ?
            ORDER BY
                global_page,
                chunk_index;
            """,
            [logical_doc_id],
        )

        rows = cursor.fetchall()

        columns = [
            description[0]
            for description in cursor.description
        ]

        return [
            dict(zip(columns, row))
            for row in rows
        ]

    # ------------------------------------------------------------------
    # CONTRACT CHUNK ADAPTER
    # ------------------------------------------------------------------

    @staticmethod
    def _build_contract_chunks(
        gold_ready_chunks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Adapt actual Gold-ready chunks to DocumentChunk-compatible
        dictionaries for Gold provenance validation.

        No chunk is created here.
        """

        contract_chunks: List[Dict[str, Any]] = []

        for chunk in gold_ready_chunks:
            global_page = chunk.get(
                "global_page"
            )

            if global_page is None:
                raise ValueError(
                    "Every Gold-ready chunk must have global_page "
                    "for chunk-level provenance validation."
                )

            contract_chunks.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "article_id": chunk["article_id"],
                    "provision_id": chunk["provision_id"],
                    "provision_path": chunk["provision_path"],
                    "text": chunk["text_content"],
                    "doc_id": chunk["logical_doc_id"],
                    "page_number": global_page,
                    "doc_version_hash": chunk[
                        "doc_version_hash"
                    ],
                    "chunk_hash": chunk["chunk_hash"],
                }
            )

        return contract_chunks

    # ------------------------------------------------------------------
    # DOCUMENT PROCESSING
    # ------------------------------------------------------------------

    def process_document(
        self,
        pdf_path: str,
    ) -> bool:
        storage_path = str(Path(pdf_path))

        logger.info(
            "Ingesting PDF: %s",
            storage_path,
        )

        # --------------------------------------------------------------
        # 0. INPUT VALIDATION
        # --------------------------------------------------------------

        path = Path(storage_path)

        if not path.exists():
            logger.error(
                "PDF does not exist: %s",
                storage_path,
            )
            return False

        if not path.is_file():
            logger.error(
                "PDF path is not a file: %s",
                storage_path,
            )
            return False

        if path.suffix.lower() != ".pdf":
            logger.error(
                "Input is not a PDF: %s",
                storage_path,
            )
            return False

        pdf_paths = [storage_path]

        # --------------------------------------------------------------
        # 1. DOCUMENT HASH
        # --------------------------------------------------------------
        #
        # The physical PDF hash is still computed before parsing.
        # The legal metadata is NOT hard-coded here; it will be extracted
        # by PDFParser from the PDF itself.
        # --------------------------------------------------------------

        try:
            computed_preparse_hash = (
                self._compute_file_hash(
                    storage_path=storage_path,
                    algorithm=self.rules.provenance_contract.hash_algorithm,
                )
            )
        except (
            FileNotFoundError,
            ValueError,
        ) as exc:
            logger.error(
                "Unable to compute document hash for %s: %s",
                storage_path,
                exc,
            )
            return False

        # --------------------------------------------------------------
        # 2. PARSE + EXTRACT DOCUMENT METADATA
        # --------------------------------------------------------------
        #
        # PDFParser extracts:
        #   - doc_id
        #   - title
        #   - doc_type
        #   - issued_date
        #   - effective_from
        #   - effective_to
        #   - per-article effective-date rules
        #
        # No legal metadata is supplied by the ingestion caller.
        # --------------------------------------------------------------

        parsed = self.parser.parse(
            parts_paths=pdf_paths,
            logical_doc_id=None,
        )

        logical_doc_id = parsed.logical_doc_id

        logger.info(
            "Parsed %s: %d physical part(s), %d page(s), "
            "%d article(s)",
            logical_doc_id,
            len(parsed.parts),
            len(parsed.pages),
            len(parsed.articles),
        )

        if not parsed.pages:
            logger.warning(
                "No pages were extracted for %s.",
                logical_doc_id,
            )
            return False

        if not parsed.articles:
            logger.warning(
                "No articles were extracted for %s.",
                logical_doc_id,
            )
            return False

        metadata_for_contract = dict(
            parsed.metadata
        )

        metadata_for_contract[
            "storage_path"
        ] = storage_path

        metadata_for_contract[
            "doc_version_hash"
        ] = computed_preparse_hash

        # --------------------------------------------------------------
        # 3. PRE-PARSE CONTRACT
        #
        #    Schema + Document Provenance
        #
        #    The checkpoint remains in the same pipeline position before
        #    Bronze materialization. Metadata is now extracted by the
        #    parser instead of being hard-coded by the caller.
        #
        #    FAIL → no Bronze
        # --------------------------------------------------------------

        pre_parse_report = (
            self.contract.validate_pre_parse(
                metadata_for_contract
            )
        )

        if pre_parse_report["status"] != "PASSED":
            logger.warning(
                "Pre-parse contract validation failed: %s",
                logical_doc_id,
            )
            logger.warning(
                "Contract report: %s",
                pre_parse_report,
            )
            return False

        logger.info(
            "Pre-parse contract validation PASSED: %s",
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 4. VERIFY PARSED DOCUMENT HASH
        # --------------------------------------------------------------

        try:
            doc_version_hash = (
                self._get_doc_version_hash(
                    parsed
                )
            )
        except ValueError as exc:
            logger.error(
                "Unable to determine document version hash "
                "for %s: %s",
                logical_doc_id,
                exc,
            )
            return False

        if (
            doc_version_hash
            != computed_preparse_hash
        ):
            logger.error(
                "Document hash changed between pre-parse and "
                "parser output for %s.",
                logical_doc_id,
            )
            logger.error(
                "Pre-parse hash: %s",
                computed_preparse_hash,
            )
            logger.error(
                "Parsed hash: %s",
                doc_version_hash,
            )
            return False

        logger.info(
            "Document version hash verified: %s",
            doc_version_hash,
        )

        # --------------------------------------------------------------
        # 5. BRONZE
        #
        #    Persist the physical source representation BEFORE the
        #    Bronze checkpoint.
        #
        #    Bronze remains the replay/audit representation. A failed
        #    checkpoint blocks downstream promotion; it does not imply
        #    that the raw Bronze representation must be deleted.
        # --------------------------------------------------------------

        bronze_records = [
            {
                "logical_doc_id": logical_doc_id,
                "physical_file_id": part.file_hash,
                "file_path": part.file_path,
                "part_no": part.part_no,
                "total_physical_pages": (
                    part.total_physical_pages
                ),
                "start_global_page": (
                    part.start_global_page
                ),
            }
            for part in parsed.parts
        ]

        try:
            self.lakehouse.insert_bronze_parts(
                bronze_records
            )
        except Exception as exc:
            logger.exception(
                "Bronze persistence failed for %s: %s",
                logical_doc_id,
                exc,
            )
            return False

        logger.info(
            "Bronze persistence completed: %s",
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 6. BRONZE CONTRACT CHECKPOINT
        #
        #    Schema + Document Provenance
        #
        #    FAIL → block Silver promotion
        # --------------------------------------------------------------

        bronze_report = (
            self.contract.validate_bronze(
                metadata_for_contract
            )
        )

        if bronze_report["status"] != "PASSED":
            logger.error(
                "Bronze contract checkpoint failed for %s.",
                logical_doc_id,
            )
            logger.error(
                "Contract report: %s",
                bronze_report,
            )

            return False

        logger.info(
            "Bronze contract checkpoint PASSED: %s",
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 7. SILVER: PAGES
        # --------------------------------------------------------------

        silver_pages = [
            {
                "logical_doc_id": page.logical_doc_id,
                "physical_file_id": page.physical_file_id,
                "file_path": page.file_path,
                "part_no": page.part_no,
                "physical_page": page.physical_page,
                "global_page": page.global_page,
                "raw_text": page.raw_text,
                "markdown_content": page.markdown_content,
                "tables": page.tables,
                "metadata": page.metadata,
            }
            for page in parsed.pages
        ]

        try:
            self.lakehouse.insert_silver_pages(
                silver_pages
            )
        except Exception as exc:
            logger.exception(
                "Silver page persistence failed for %s: %s",
                logical_doc_id,
                exc,
            )
            return False

        # --------------------------------------------------------------
        # 8. SILVER: HIERARCHICAL PROVISIONS AND EFFECTIVITY RULES
        # --------------------------------------------------------------

        try:
            self.lakehouse.insert_silver_provisions(
                provisions=parsed.provisions,
                effectivity_rules=parsed.effectivity_rules,
                logical_doc_id=logical_doc_id,
            )
        except Exception as exc:
            logger.exception(
                "Silver provision persistence failed for %s: %s",
                logical_doc_id,
                exc,
            )
            return False

        logger.info(
            "Silver persistence completed: %s",
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 9. SILVER CONTRACT CHECKPOINT
        #
        #    Temporal + Quality
        #
        #    IMPORTANT:
        #    Validation is performed against the data that has just
        #    been materialized into the Silver layer.
        #
        #    FAIL → block Gold promotion
        # --------------------------------------------------------------

        tables: List[Any] = []

        for page in parsed.pages:
            page_tables = page.tables or []
            tables.extend(page_tables)

        silver_report = (
            self.contract.validate_silver(
                metadata=metadata_for_contract,
                content=parsed.full_markdown,
                tables=tables,
            )
        )

        if silver_report["status"] != "PASSED":
            logger.error(
                "Silver contract checkpoint failed for %s.",
                logical_doc_id,
            )
            logger.error(
                "Contract report: %s",
                silver_report,
            )

            return False

        logger.info(
            "Silver contract checkpoint PASSED: %s",
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 10. LOAD ACTUAL SILVER AS CHUNK INPUT
        #
        #     This is the critical architecture change.
        #
        #     ChunkBuilder no longer consumes parser output directly.
        #     It consumes the materialized Silver representation.
        # --------------------------------------------------------------

        silver_chunk_provisions = (
            self._get_silver_provisions(
                logical_doc_id
            )
        )

        if not silver_chunk_provisions:
            logger.error(
                "No Silver provisions available for chunk generation "
                "after Silver validation: %s",
                logical_doc_id,
            )
            return False

        logger.info(
            "Loaded %d Silver provision(s) for chunk generation: %s",
            len(silver_chunk_provisions),
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 11. BUILD GOLD-READY CHUNKS FROM SILVER
        #
        #     ChunkBuilder generates the actual chunks that will be
        #     checked by the Gold Contract and later persisted.
        #
        #     No synthetic benchmark chunks are created.
        # --------------------------------------------------------------

        gold_ready_chunks = (
            self.chunk_builder.build_chunks(
                provisions=silver_chunk_provisions,
                logical_doc_id=logical_doc_id,
                doc_version_hash=doc_version_hash,
            )
        )

        if not gold_ready_chunks:
            logger.warning(
                "No Gold-ready chunks were generated from Silver "
                "for %s.",
                logical_doc_id,
            )
            return False

        logger.info(
            "Built %d Gold-ready chunks from Silver: %s",
            len(gold_ready_chunks),
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 12. GOLD CONTRACT CHECKPOINT
        #
        #     Chunk-level provenance + final integrity
        #
        #     FAIL → no Gold persistence
        # --------------------------------------------------------------

        contract_chunks = (
            self._build_contract_chunks(
                gold_ready_chunks
            )
        )

        gold_report = (
            self.contract.validate_gold(
                metadata=metadata_for_contract,
                chunks=contract_chunks,
            )
        )

        if gold_report["status"] != "PASSED":
            logger.error(
                "Gold contract checkpoint failed for %s.",
                logical_doc_id,
            )
            logger.error(
                "Contract report: %s",
                gold_report,
            )

            return False

        logger.info(
            "Gold contract checkpoint PASSED: %s",
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 13. GOLD
        #
        #     Persist EXACTLY the chunks that passed the Gold Contract.
        # --------------------------------------------------------------

        try:
            self.chunk_builder.persist_chunks(
                gold_ready_chunks
            )
        except Exception as exc:
            logger.exception(
                "Gold persistence failed for %s: %s",
                logical_doc_id,
                exc,
            )
            return False

        logger.info(
            "Gold persistence completed: %s",
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 14. VERIFY ACTUAL GOLD MATERIALIZATION
        # --------------------------------------------------------------

        materialized_chunks = (
            self._get_gold_chunks(
                logical_doc_id
            )
        )

        if not materialized_chunks:
            logger.error(
                "No Gold chunks found after persistence for %s.",
                logical_doc_id,
            )
            return False

        materialized_chunk_ids = {
            chunk["chunk_id"]
            for chunk in materialized_chunks
        }

        validated_chunk_ids = {
            chunk["chunk_id"]
            for chunk in gold_ready_chunks
        }

        if (
            materialized_chunk_ids
            != validated_chunk_ids
        ):
            logger.error(
                "Gold chunk IDs differ from the validated chunk set "
                "for %s.",
                logical_doc_id,
            )
            return False

        logger.info(
            "Gold materialization verified: %s",
            logical_doc_id,
        )

        # --------------------------------------------------------------
        # 15. FINAL GOLD SUMMARY
        # --------------------------------------------------------------

        article_ids = sorted(
            {
                chunk["article_id"]
                for chunk in materialized_chunks
                if chunk.get("article_id")
            }
        )

        logger.info(
            "Gold summary for %s: %d article(s), %d chunk(s)",
            logical_doc_id,
            len(article_ids),
            len(materialized_chunks),
        )

        return True

    # ------------------------------------------------------------------
    # BENCHMARK SKELETON
    # ------------------------------------------------------------------

    def export_benchmark_skeleton(
        self,
        questions_path: str,
        output_path: str,
    ) -> None:
        """
        Create the benchmark JSON skeleton.

        Gold document, provision, and chunk references are taken from the
        manually annotated source question file.

        The materialized Gold data is used only to validate that the
        annotated provision/chunk references actually exist.

        gold_answer and gold_citation_doc_ids remain unchanged from
        the source question file.
        """

        with open(
            questions_path,
            "r",
            encoding="utf-8",
        ) as f:
            source = json.load(f)

        output = Path(output_path)
        existing: Dict[str, Any] = {}
        existing_dataset: Dict[str, Any] = {}
        if output.exists():
            with output.open("r", encoding="utf-8") as f:
                existing_dataset = json.load(f)
            existing = {
                sample["id"]: sample
                for sample in existing_dataset.get("samples", [])
                if sample.get("id") is not None
            }

        dataset: Dict[str, Any] = {
            "dataset_name": source.get("dataset_name", existing_dataset.get("dataset_name")),
            "version": source.get("version", existing_dataset.get("version")),
            "split": source.get("split", existing_dataset.get("split")),
            "samples": [],
        }

        source_ids = set()
        for question in source.get(
            "samples",
            [],
        ):
            question_id = question["id"]
            source_ids.add(question_id)
            old = existing.get(question_id, {})
            gold_doc_ids = list(
                question.get("gold_doc_ids")
                or old.get("gold_doc_ids", [])
            )

            gold_chunk_ids = list(
                question.get(
                    "gold_chunk_ids",
                    [],
                )
            )
            gold_provisions = [
                dict(reference)
                for reference in (
                    question.get("gold_provisions")
                    or old.get("gold_provisions", [])
                )
            ]
            gold_doc_ids = list(
                dict.fromkeys(
                    gold_doc_ids
                    + [
                        reference.get("logical_doc_id")
                        for reference in gold_provisions
                        if reference.get("logical_doc_id")
                    ]
                )
            )

            for reference in gold_provisions:
                if (
                    not reference.get("logical_doc_id")
                    or not (
                        reference.get("provision_id")
                        or reference.get("provision_path")
                    )
                ):
                    raise ValueError(
                        f"Question {question_id} has an invalid gold_provisions "
                        "entry; expected logical_doc_id and provision_id "
                        "or provision_path."
                    )
            available_chunks: List[Dict[str, Any]] = []

            for doc_id in gold_doc_ids:
                available_chunks.extend(
                    self._get_gold_chunks(doc_id)
                )

            valid_chunk_ids = {
                chunk["chunk_id"]
                for chunk in available_chunks
                if chunk.get("chunk_id")
            }

            valid_provisions_by_id = {
                (chunk["logical_doc_id"], chunk["provision_id"]): chunk.get(
                    "provision_path"
                )
                for chunk in available_chunks
                if chunk.get("logical_doc_id") and chunk.get("provision_id")
            }
            valid_provisions_by_path = {
                (chunk["logical_doc_id"], chunk["provision_path"]): chunk.get(
                    "provision_id"
                )
                for chunk in available_chunks
                if chunk.get("logical_doc_id") and chunk.get("provision_path")
            }
            invalid_chunks = (
                set(gold_chunk_ids) - valid_chunk_ids
            )

            invalid_provisions = []
            for reference in gold_provisions:
                logical_doc_id = reference["logical_doc_id"]
                provision_id = reference.get("provision_id")
                provision_path = reference.get("provision_path")
                if provision_id:
                    actual_path = valid_provisions_by_id.get(
                        (logical_doc_id, provision_id)
                    )
                    if actual_path is None or (
                        provision_path and actual_path != provision_path
                    ):
                        invalid_provisions.append(reference)
                elif (logical_doc_id, provision_path) not in valid_provisions_by_path:
                    invalid_provisions.append(reference)
            if invalid_chunks:
                raise ValueError(
                    f"Question {question['id']} contains Gold chunk IDs "
                    f"not found in Gold: {sorted(invalid_chunks)}"
                )

            if invalid_provisions:
                raise ValueError(
                    f"Question {question['id']} contains provision references "
                    "not found in Gold: "
                    f"{invalid_provisions}"
                )

            generated = {
                    "id": question["id"],
                    "query": question["query"],
                    "t_event": question["t_event"],
                    "difficulty": question.get(
                        "difficulty",
                        "medium",
                    ),
                    "category": question.get(
                        "category",
                        "temporal",
                    ),
                    "gold_doc_ids": gold_doc_ids,
                    "gold_provisions": gold_provisions,
                    "gold_chunk_ids": (
                        gold_chunk_ids
                    ),
                    "gold_answer": question.get(
                        "gold_answer",
                        "",
                    ),
                    "gold_citation_doc_ids": (
                        question.get(
                            "gold_citation_doc_ids",
                            [],
                        )
                    ),
                }
            # Preserve all existing manually maintained fields for matched IDs.
            generated.update(old)
            generated["gold_doc_ids"] = gold_doc_ids
            if question.get("gold_provisions"):
                generated["gold_provisions"] = gold_provisions
            dataset["samples"].append(generated)

        # Keep removed source questions and their annotations until a user removes them.
        for question_id, old in existing.items():
            if question_id not in source_ids:
                dataset["samples"].append(old)

        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with open(
            output,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                dataset,
                f,
                ensure_ascii=False,
                indent=2,
            )

        logger.info(
            "Benchmark skeleton saved to %s",
            output,
        )

    # ------------------------------------------------------------------
    # CLOSE
    # ------------------------------------------------------------------

    def close(self) -> None:
        self.lakehouse.close()


# ======================================================================
# MAIN
# ======================================================================

def main() -> None:
    builder = BenchmarkDatasetBuilder()

    incoming_dir = Path(
        builder.config.db.incoming_dir
    )

    pdf_paths = sorted(
        incoming_dir.glob("*.pdf")
    )

    if not pdf_paths:
        raise FileNotFoundError(
            f"No PDF files found in incoming directory: "
            f"{incoming_dir}"
        )

    try:
        failed_documents: List[str] = []

        for pdf_path in pdf_paths:
            try:
                success = builder.process_document(
                    pdf_path=str(pdf_path)
                )
            except Exception:
                logger.exception(
                    "Document processing raised an error for %s",
                    pdf_path,
                )
                success = False

            if not success:
                failed_documents.append(str(pdf_path))

        if failed_documents:
            raise RuntimeError(
                "Failed to materialize the following PDFs "
                "through Gold: "
                + ", ".join(failed_documents)
            )

        builder.export_benchmark_skeleton(
            questions_path=(
                builder.config.benchmark.questions_path
            ),
            output_path=(
                builder.config.benchmark.output_path
            ),
        )

    finally:
        builder.lakehouse.export_tables_to_parquet()
        builder.close()


if __name__ == "__main__":
    main()
