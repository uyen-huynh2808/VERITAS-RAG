from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from src.database.lakehouse_manager import LakehouseManager
from src.core.config import load_system_config
from src.contracts.rules import load_contract_rules


logger = logging.getLogger(__name__)


class ChunkBuilder:
    """
    Build page-aware Gold-ready chunks from validated Silver Provisions.

    Pipeline:
        Silver Provisions
              ↓
        Page-aware Chunking
              ↓
        Actual Gold-ready Chunks
              ↓
        [Gold Data Contract validation]
              ↓
        Gold persistence

    Responsibilities:
    - Build deterministic page-aware chunks from Silver data.
    - Preserve provision-level temporal metadata.
    - Preserve physical and global page provenance.
    - Generate deterministic chunk hashes.
    - Load Silver Articles for the normal E2E pipeline.
    - Persist chunks only when explicitly requested by the
      orchestration layer after Gold Contract validation.

    Not responsible for:
    - Data Contract validation.
    - Bronze / Silver persistence.
    - Embedding generation.
    - Vector indexing.
    - As-of-Date filtering.
    - Retrieval.
    - HART / NLI.

    Important:
    ChunkBuilder must not decide whether chunks are valid for Gold.
    The caller is responsible for running the Gold Data Contract
    before calling persist_chunks().
    """

    def __init__(
        self,
        lakehouse: LakehouseManager,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        hash_algorithm: Optional[str] = None,
    ) -> None:
        config = load_system_config()
        rules = load_contract_rules(config.contracts.rules_path)
        chunk_size = (
            config.chunking.max_tokens
            if chunk_size is None
            else chunk_size
        )
        chunk_overlap = (
            config.chunking.overlap
            if chunk_overlap is None
            else chunk_overlap
        )
        hash_algorithm = hash_algorithm or rules.provenance_contract.hash_algorithm
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0.")

        if chunk_overlap < 0:
            raise ValueError("chunk_overlap must be non-negative.")

        if chunk_overlap >= chunk_size:
            raise ValueError(
                "chunk_overlap must be smaller than chunk_size."
            )

        self.lakehouse = lakehouse
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.hash_algorithm = hash_algorithm

    # ------------------------------------------------------------------
    # Public API: build from Silver without persistence
    # ------------------------------------------------------------------

    def build_from_silver(
        self,
        logical_doc_id: str,
        doc_version_hash: str,
    ) -> List[Dict[str, Any]]:
        """
        Load validated Silver Articles and build Gold-ready chunks.

        This method does NOT persist anything into Gold.

        The expected lifecycle is:

            Silver persistence
                ↓
            Silver Contract validation
                ↓
            build_from_silver()
                ↓
            Gold Contract validation
                ↓
            persist_chunks()

        This separation ensures that the exact chunks generated from
        Silver can be validated before they become trusted Gold data.
        """
        if not logical_doc_id:
            raise ValueError("logical_doc_id must not be empty.")

        if not doc_version_hash:
            raise ValueError("doc_version_hash must not be empty.")

        provisions = self._load_provisions(logical_doc_id)

        if not provisions:
            logger.warning(
                "No Silver provisions found for logical_doc_id=%s",
                logical_doc_id,
            )
            return []

        gold_chunks = self.build_chunks(
            provisions=provisions,
            logical_doc_id=logical_doc_id,
            doc_version_hash=doc_version_hash,
        )

        if not gold_chunks:
            logger.warning(
                "No Gold chunks were generated for logical_doc_id=%s",
                logical_doc_id,
            )
            return []

        return gold_chunks

    # ------------------------------------------------------------------
    # Public API: build without persistence
    # ------------------------------------------------------------------

    def build_chunks(
        self,
        provisions: List[Dict[str, Any]],
        logical_doc_id: str,
        doc_version_hash: str,
    ) -> List[Dict[str, Any]]:
        """
        Build actual Gold-ready chunks without persisting them.

        This method is persistence-free so the caller can run the
        Gold Data Contract against the exact chunks that will later
        be written to Gold.

        Expected provision structure:

            {
                "provision_id": str,
                "parent_provision_id": str | None,
                "level": str,
                "path": str,
                "issued_date": ...,
                "effective_from": ...,
                "effective_to": ...,
                "status": ...,
                "page_segments": [
                    {
                        "physical_file_id": ...,
                        "file_path": ...,
                        "part_no": ...,
                        "physical_page": ...,
                        "global_page": ...,
                        "text": ...
                    }
                ]
            }

        Returns:
            List of Gold-ready chunk dictionaries.
        """
        if not logical_doc_id:
            raise ValueError("logical_doc_id must not be empty.")

        if not doc_version_hash:
            raise ValueError("doc_version_hash must not be empty.")

        if not provisions:
            logger.warning(
                "No provisions supplied for logical_doc_id=%s",
                logical_doc_id,
            )
            return []

        normalized_provisions = self._normalize_provisions(
            provisions,
            logical_doc_id=logical_doc_id,
        )

        chunks = self._chunk_provisions(
            normalized_provisions,
            doc_version_hash=doc_version_hash,
        )

        logger.info(
            "Built %d Gold-ready chunks for logical_doc_id=%s",
            len(chunks),
            logical_doc_id,
        )

        return chunks

    # ------------------------------------------------------------------
    # Public API: explicit Gold persistence
    # ------------------------------------------------------------------

    def persist_chunks(
        self,
        chunks: List[Dict[str, Any]],
    ) -> None:
        """
        Persist already-built and Gold-validated chunks into Gold.

        The caller must execute Gold Data Contract validation before
        invoking this method.

        This method intentionally performs no contract validation and
        does not rebuild the chunks. It persists exactly the chunk
        objects supplied by the caller.
        """
        if not chunks:
            logger.warning("No chunks supplied for Gold persistence.")
            return

        self.lakehouse.insert_gold_chunks(chunks)

        logger.info(
            "Persisted %d Gold chunks.",
            len(chunks),
        )

    # ------------------------------------------------------------------
    # Silver loading
    # ------------------------------------------------------------------

    def _load_provisions(
        self,
        logical_doc_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Load Silver Provisions together with page-level provenance.

        page_segments_json is required because Gold chunks must retain
        the physical page from which their text originated.

        This method reads from Silver only. It does not modify Silver
        and does not persist anything into Gold.
        """
        query = """
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
            ORDER BY start_global_page, provision_path
        """

        cursor = self.lakehouse.conn.execute(
            query,
            [logical_doc_id],
        )
        rows = cursor.fetchall()

        columns = [description[0] for description in cursor.description]
        provisions: List[Dict[str, Any]] = []

        for row in rows:
            provision = dict(zip(columns, row))

            page_segments = self._parse_page_segments(
                provision.get("page_segments_json"),
                article_id=provision["provision_id"],
            )

            provision["page_segments"] = page_segments
            provisions.append(provision)

        return provisions

    # ------------------------------------------------------------------
    # Article normalization
    # ------------------------------------------------------------------

    def _normalize_provisions(
        self,
        provisions: List[Dict[str, Any]],
        logical_doc_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Normalize provision dictionaries before chunk construction.

        This method does not create synthetic chunks or synthetic
        provenance. It only normalizes the actual article/page-segment
        data supplied by Silver.
        """
        normalized: List[Dict[str, Any]] = []

        for provision in provisions:
            if not isinstance(provision, dict):
                raise TypeError(
                    "Each provision must be a dictionary."
                )

            article_copy = dict(provision)

            article_copy["logical_doc_id"] = (
                article_copy.get("logical_doc_id")
                or logical_doc_id
            )

            if article_copy["logical_doc_id"] != logical_doc_id:
                raise ValueError(
                    "Article logical_doc_id does not match the requested "
                    f"logical_doc_id={logical_doc_id}."
                )

            provision_id = article_copy.get("provision_id")

            if not provision_id:
                raise ValueError(
                    "Every provision must contain a non-empty provision_id."
                )

            raw_segments = article_copy.get("page_segments")

            if raw_segments is None:
                raw_segments = article_copy.get(
                    "page_segments_json"
                )

            article_copy["page_segments"] = (
                self._parse_page_segments(
                    raw_segments,
                    article_id=str(provision_id),
                )
            )

            normalized.append(article_copy)

        return normalized

    # ------------------------------------------------------------------
    # Page-aware chunking
    # ------------------------------------------------------------------

    def _chunk_provisions(
        self,
        provisions: List[Dict[str, Any]],
        doc_version_hash: str,
    ) -> List[Dict[str, Any]]:
        """
        Build Gold chunks from actual Silver provision/page-segment data.

        Each page segment is chunked independently so that every Gold
        chunk retains exact physical-page provenance.

        A deterministic contract-configured hash is generated from the chunk's
        canonical identity and text.
        """
        chunks: List[Dict[str, Any]] = []

        for provision in provisions:
            page_segments = provision.get("page_segments") or []

            if not page_segments:
                logger.warning(
                    "Article %s has no page_segments; skipping.",
                    provision.get("provision_id"),
                )
                continue

            for segment in page_segments:
                text = str(
                    segment.get("text") or ""
                ).strip()

                if not text:
                    continue

                segment_chunks = self._split_text(text)

                for chunk_index, chunk_text in enumerate(
                    segment_chunks
                ):
                    global_page = segment.get("global_page")

                    chunk_id = self._build_chunk_id(
                        logical_doc_id=provision["logical_doc_id"],
                        article_id=provision["provision_id"],
                        global_page=global_page,
                        chunk_index=chunk_index,
                    )

                    chunk_hash = self._compute_chunk_hash(
                        chunk_id=chunk_id,
                        doc_version_hash=doc_version_hash,
                        chunk_text=chunk_text,
                        algorithm=self.hash_algorithm,
                    )

                    chunks.append(
                        {
                            "chunk_id": chunk_id,
                            "article_id": provision["article_id"],
                            "provision_id": provision["provision_id"],
                            "provision_path": provision["provision_path"],
                            "effectivity_mode": provision["effectivity_mode"],
                            "trigger_reference": provision.get("trigger_reference"),
                            "logical_doc_id": provision[
                                "logical_doc_id"
                            ],
                            "doc_version_hash": doc_version_hash,
                            "chunk_hash": chunk_hash,
                            "physical_file_id": segment.get(
                                "physical_file_id"
                            ),
                            "file_path": segment.get(
                                "file_path"
                            ),
                            "part_no": segment.get("part_no"),
                            "physical_page": segment.get(
                                "physical_page"
                            ),
                            "global_page": global_page,
                            "chunk_index": chunk_index,
                            "text_content": chunk_text,
                            "issued_date": provision.get(
                                "issued_date"
                            ),
                            "effective_from": provision.get(
                                "effective_from"
                            ),
                            "effective_to": provision.get(
                                "effective_to"
                            ),
                            "status": provision.get("status"),
                            "metadata": {
                                "provision_level": provision.get("level"),
                                "provision_number": provision.get("provision_number"),
                                "provision_path": provision.get("provision_path"),
                                "provision_title": provision.get("title"),
                            },
                        }
                    )

        return chunks

    # ------------------------------------------------------------------
    # Chunk identity / hash
    # ------------------------------------------------------------------

    @staticmethod
    def _build_chunk_id(
        logical_doc_id: str,
        article_id: str,
        global_page: Optional[int],
        chunk_index: int,
    ) -> str:
        """
        Build a deterministic chunk identifier.

        The identifier uses actual document/article/page provenance.
        No random UUID is introduced at chunking time.
        """
        page_value = (
            str(global_page)
            if global_page is not None
            else "unknown"
        )

        return (
            f"{logical_doc_id}:"
            f"{article_id}:"
            f"{page_value}:"
            f"{chunk_index}"
        )

    @staticmethod
    def _compute_chunk_hash(
        chunk_id: str,
        doc_version_hash: str,
        chunk_text: str,
        algorithm: str,
    ) -> str:
        """
        Compute a deterministic contract-configured hash for a chunk.

        The hash covers:
        - chunk identity
        - source document version
        - normalized chunk text

        This allows chunk-level provenance to be independently
        validated from the persisted chunk content.
        """
        canonical_payload = (
            f"chunk_id={chunk_id}\n"
            f"doc_version_hash={doc_version_hash}\n"
            f"text={chunk_text}"
        )

        return hashlib.new(
            algorithm, canonical_payload.encode("utf-8")
        ).hexdigest()

    # ------------------------------------------------------------------
    # Text splitting
    # ------------------------------------------------------------------

    def _split_text(
        self,
        text: str,
    ) -> List[str]:
        """
        Split text using deterministic whitespace tokenization.

        Chunking is performed independently for each page segment so
        that a chunk never crosses physical-page boundaries.
        """
        tokens = text.split()

        if not tokens:
            return []

        if len(tokens) <= self.chunk_size:
            return [" ".join(tokens)]

        chunks: List[str] = []

        step = self.chunk_size - self.chunk_overlap
        start = 0

        while start < len(tokens):
            end = min(
                start + self.chunk_size,
                len(tokens),
            )

            chunk = " ".join(
                tokens[start:end]
            ).strip()

            if chunk:
                chunks.append(chunk)

            if end >= len(tokens):
                break

            start += step

        return chunks

    # ------------------------------------------------------------------
    # Page-segment parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_page_segments(
        raw_segments: Any,
        article_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Deserialize and normalize page_segments_json.

        Each returned segment contains only actual page-level
        provenance supplied by the parser/Silver layer.
        """
        if raw_segments is None:
            return []

        if isinstance(raw_segments, list):
            segments = raw_segments

        elif isinstance(raw_segments, str):
            raw_segments = raw_segments.strip()

            if not raw_segments:
                return []

            try:
                segments = json.loads(raw_segments)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Invalid page_segments_json for article "
                    f"{article_id}: {exc}"
                ) from exc

        else:
            raise TypeError(
                "Unsupported page_segments_json type for article "
                f"{article_id}: "
                f"{type(raw_segments).__name__}"
            )

        if not isinstance(segments, list):
            raise ValueError(
                "page_segments_json for article "
                f"{article_id} must contain a JSON list."
            )

        normalized_segments: List[Dict[str, Any]] = []

        for segment in segments:
            if not isinstance(segment, dict):
                logger.warning(
                    "Ignoring invalid page segment in article %s.",
                    article_id,
                )
                continue

            text = str(
                segment.get("text") or ""
            ).strip()

            if not text:
                continue

            normalized_segments.append(
                {
                    "physical_file_id": segment.get(
                        "physical_file_id"
                    ),
                    "file_path": segment.get(
                        "file_path"
                    ),
                    "part_no": segment.get(
                        "part_no"
                    ),
                    "physical_page": segment.get(
                        "physical_page"
                    ),
                    "global_page": segment.get(
                        "global_page"
                    ),
                    "text": text,
                }
            )

        normalized_segments.sort(
            key=lambda segment: (
                segment.get("global_page")
                if segment.get("global_page") is not None
                else float("inf")
            )
        )

        return normalized_segments
