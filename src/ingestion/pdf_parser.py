import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.interfaces import BaseParser
from src.core.config import SystemConfig, load_system_config
from src.contracts.rules import load_contract_rules
from src.ingestion.provision_parser import ProvisionParser

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Optional parser dependencies
# ---------------------------------------------------------------------------

HAS_DOCLING = False
HAS_PYMUPDF = False

try:
    from docling.document_converter import DocumentConverter
    from docling_core.types.doc import TableItem

    HAS_DOCLING = True
except ImportError:
    DocumentConverter = None
    TableItem = None
    logger.warning(
        "Library 'docling' is not installed. "
        "Docling parser will be unavailable."
    )

try:
    import fitz  # PyMuPDF

    HAS_PYMUPDF = True
except ImportError:
    fitz = None
    logger.warning(
        "Library 'PyMuPDF' (fitz) is not installed."
    )


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PhysicalPDFPart:
    """
    Represents one physical PDF file belonging to a logical document.

    physical_file_id is the contract-configured hash of the physical PDF file.
    """

    file_path: str
    file_hash: str
    part_no: int = 1
    total_physical_pages: int = 0
    start_global_page: int = 1


@dataclass
class ParsedArticle:
    """
    Structured legal article extracted from one logical document.

    An article may span multiple physical PDF files and multiple pages.
    page_segments preserves the page-level mapping required later when
    creating provenance-aware retrieval chunks.
    """

    article_id: str
    logical_doc_id: str

    article_number: str
    article_title: str
    content: str

    issued_date: Optional[str] = None
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    status: str = "active"

    start_physical_file_id: str = ""
    start_physical_page: int = 1
    end_physical_file_id: str = ""
    end_physical_page: int = 1

    start_global_page: int = 1
    end_global_page: int = 1

    # Each segment contains:
    # {
    #     "physical_file_id": str,
    #     "file_path": str,
    #     "part_no": int,
    #     "physical_page": int,
    #     "global_page": int,
    #     "text": str,
    # }
    #
    # This is intentionally retained at Silver level so that downstream
    # chunking can preserve exact page-level provenance.
    page_segments: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ParsedPage:
    """
    One physically parsed PDF page.

    physical_page is the page number inside the physical PDF.
    global_page is the page number inside the complete logical document.
    """

    logical_doc_id: str
    physical_file_id: str
    file_path: str
    part_no: int
    physical_page: int
    global_page: int
    raw_text: str
    markdown_content: str
    tables: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocumentResult:
    """
    Complete parsing result for one logical document.

    metadata contains document-level fields extracted from the PDF,
    including issued_date and the default effective_from date.
    Article-level effective dates are stored on ParsedArticle.
    """

    logical_doc_id: str
    parts: List[PhysicalPDFPart]
    pages: List[ParsedPage]
    articles: List[ParsedArticle]
    provisions: List[Dict[str, Any]]
    effectivity_rules: List[Dict[str, Any]]
    full_markdown: str
    parser_used: str
    total_global_pages: int
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def calculate_file_hash(
    file_path: str,
    algorithm: str,
) -> str:
    """
    Calculate a deterministic hash for a physical PDF file.
    """

    hasher = hashlib.new(algorithm)

    with open(file_path, "rb") as file:
        while chunk := file.read(1024 * 1024):
            hasher.update(chunk)

    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# PDF Parser
# ---------------------------------------------------------------------------

class PDFParser(BaseParser):
    """
    Parser for single- and multi-part Vietnamese legal PDFs.

    Responsibilities:
        1. Parse physical PDF files.
        2. Preserve physical-file and page provenance.
        3. Normalize multiple physical PDFs into one logical document.
        4. Extract article-level structure.
        5. Preserve article-to-page mapping.

    Contract validation and lakehouse persistence are deliberately
    handled outside this class.
    """

    def __init__(
        self,
        prefer_docling: Optional[bool] = None,
        hash_algorithm: Optional[str] = None,
        header_ocr_engine: Optional[str] = None,
        config: Optional[SystemConfig] = None,
    ):
        self.config = config or load_system_config()
        rules = load_contract_rules(self.config.contracts.rules_path)
        self.hash_algorithm = (
            rules.provenance_contract.hash_algorithm
            if hash_algorithm is None
            else hash_algorithm
        )
        self.header_ocr_engine_type = (
            header_ocr_engine or self.config.parser.header_ocr_engine
        )
        self._header_ocr_engine = None
        self._header_ocr_attempted = False
        selected_parser = (
            self.config.parser.prefer_docling
            if prefer_docling is None
            else prefer_docling
        )
        self.prefer_docling = selected_parser and HAS_DOCLING
        self.docling_converter = None

        if self.prefer_docling:
            try:
                self.docling_converter = DocumentConverter()

                logger.info(
                    "Docling initialized as primary PDF parser."
                )

            except Exception as exc:
                logger.warning(
                    "Docling initialization failed: %s. "
                    "Falling back to PyMuPDF.",
                    exc,
                )

                self.prefer_docling = False

        if not self.prefer_docling and not HAS_PYMUPDF:
            raise RuntimeError(
                "Neither Docling nor PyMuPDF is available."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(
        self,
        parts_paths: List[str],
        logical_doc_id: Optional[str] = None,
    ) -> ParsedDocumentResult:
        return self.parse_multi_part_document(
            parts_paths=parts_paths,
            logical_doc_id=logical_doc_id,
        )

    def parse_file(
        self,
        file_path: str,
        logical_doc_id: Optional[str] = None,
        part_no: int = 1,
        page_offset: int = 0,
    ) -> Tuple[
        PhysicalPDFPart,
        List[ParsedPage],
        str,
    ]:
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(
                f"PDF file not found: {file_path}"
            )

        if path.suffix.lower() != ".pdf":
            raise ValueError(
                f"Expected a PDF file, got: {path.suffix}"
            )

        if part_no < 1:
            raise ValueError(
                "part_no must be >= 1."
            )

        if page_offset < 0:
            raise ValueError(
                "page_offset must be >= 0."
            )

        file_hash = calculate_file_hash(str(path), self.hash_algorithm)
        doc_id = logical_doc_id or path.stem

        pages: List[ParsedPage] = []
        parser_used = "pymupdf"

        if self.prefer_docling:
            try:
                pages, parser_used = self._parse_with_docling(
                    file_path=str(path),
                    logical_doc_id=doc_id,
                    file_hash=file_hash,
                    part_no=part_no,
                    page_offset=page_offset,
                )

                if not pages:
                    raise RuntimeError(
                        "Docling returned no parsed pages."
                    )

            except Exception as exc:
                logger.warning(
                    "Docling parsing failed for %s: %s. "
                    "Falling back to PyMuPDF.",
                    path.name,
                    exc,
                )

                pages = []

        if not pages:
            if not HAS_PYMUPDF:
                raise RuntimeError(
                    "PyMuPDF is unavailable and Docling parsing failed."
                )

            pages, parser_used = self._parse_with_pymupdf(
                file_path=str(path),
                logical_doc_id=doc_id,
                file_hash=file_hash,
                part_no=part_no,
                page_offset=page_offset,
            )

        if not pages:
            raise RuntimeError(
                f"No pages were parsed from PDF: {file_path}"
            )

        total_physical_pages = len(pages)

        part_info = PhysicalPDFPart(
            file_path=str(path),
            file_hash=file_hash,
            part_no=part_no,
            total_physical_pages=total_physical_pages,
            start_global_page=page_offset + 1,
        )

        return part_info, pages, parser_used

    def parse_multi_part_document(
        self,
        parts_paths: List[str],
        logical_doc_id: Optional[str] = None,
    ) -> ParsedDocumentResult:
        if not parts_paths:
            raise ValueError(
                "parts_paths must contain at least one PDF file."
            )

        if logical_doc_id is not None:
            logical_doc_id = logical_doc_id.strip() or None

        all_parts: List[PhysicalPDFPart] = []
        all_pages: List[ParsedPage] = []
        markdown_sections: List[str] = []

        # A provisional ID is only used while extracting raw pages.
        # The authoritative logical_doc_id is taken from the PDF metadata
        # once the document text has been parsed.
        provisional_doc_id = (
            logical_doc_id
            or Path(parts_paths[0]).stem
        )

        current_global_offset = 0
        parser_names: List[str] = []

        for part_no, file_path in enumerate(
            parts_paths,
            start=1,
        ):
            part_info, pages, parser_used = self.parse_file(
                file_path=file_path,
                logical_doc_id=provisional_doc_id,
                part_no=part_no,
                page_offset=current_global_offset,
            )

            all_parts.append(part_info)
            all_pages.extend(pages)
            parser_names.append(parser_used)

            current_global_offset += (
                part_info.total_physical_pages
            )

            part_markdown = "\n\n".join(
                page.markdown_content
                for page in pages
                if page.markdown_content.strip()
            )

            markdown_sections.append(
                f"<!-- PART {part_no}: "
                f"{Path(file_path).name} -->\n"
                f"{part_markdown}"
            )

        unique_parsers = list(dict.fromkeys(parser_names))

        parser_used = (
            unique_parsers[0]
            if len(unique_parsers) == 1
            else "mixed"
        )

        full_markdown = "\n\n".join(markdown_sections)

        # Extract document-level metadata directly from the parsed PDF.
        # No issued/effective dates are supplied by the ingestion caller.
        canonical_content = re.sub(
            r"<!--.*?-->", " ", full_markdown, flags=re.DOTALL
        )
        canonical_content = " ".join(canonical_content.split())
        content_fallback_id = (
            "DOC-"
            + hashlib.new(
                self.hash_algorithm,
                canonical_content.encode("utf-8"),
            ).hexdigest()[:24]
        )
        metadata = self._extract_document_metadata(
            full_markdown=full_markdown,
            logical_doc_id=logical_doc_id or content_fallback_id,
            fallback_path=parts_paths[0],
        )
        if (
            not metadata.get("issued_date")
            or str(metadata.get("doc_id", "")).startswith("DOC-")
        ):
            header_text = self._extract_header_text(parts_paths[0])
            if header_text:
                metadata = self._extract_document_metadata(
                    full_markdown=header_text + "\n" + full_markdown,
                    logical_doc_id=logical_doc_id or content_fallback_id,
                    fallback_path=parts_paths[0],
                )

        extracted_doc_id = metadata.get("doc_id")
        actual_logical_doc_id = (
            extracted_doc_id
            or logical_doc_id
            or provisional_doc_id
        )

        # Pages were parsed before the document ID was known. Update their
        # logical ID before article extraction so all downstream records
        # carry the authoritative ID.
        for page in all_pages:
            page.logical_doc_id = actual_logical_doc_id

        articles = self._extract_articles(
            pages=all_pages,
            logical_doc_id=actual_logical_doc_id,
        )

        (
            default_effective_from,
            effective_date_rules,
            effectivity_rules,
        ) = self._extract_effectivity_from_articles(articles)

        metadata["effective_from"] = (
            default_effective_from
        )
        metadata["effective_date_rules"] = (
            effective_date_rules
        )

        self._apply_effective_dates(
            articles=articles,
            effective_rules=effective_date_rules,
            default_effective_from=default_effective_from,
            issued_date=metadata.get("issued_date"),
        )

        provision_parser = ProvisionParser()
        parsed_provisions = [
            provision
            for article in articles
            for provision in provision_parser.parse_article(vars(article))
        ]
        provision_parser.apply_effectivity_rules(
            parsed_provisions,
            effectivity_rules,
        )

        provision_paths = {
            provision["path"] for provision in parsed_provisions
        }
        missing_effectivity_targets = sorted(
            {
                rule.get("target_path")
                for rule in effectivity_rules
                if rule.get("target_path") != "*"
                and rule.get("target_path") not in provision_paths
            }
        )

        logical_doc_id = actual_logical_doc_id

        # --------------------------------------------------------------
        # Parser-level fallback
        #
        # Docling may produce articles while losing clause/point headings
        # from its item text. Retry with PyMuPDF when a source effectivity
        # reference cannot be resolved to an extracted provision.
        # --------------------------------------------------------------

        if (
            (not articles or missing_effectivity_targets)
            and HAS_PYMUPDF
            and any(
                parser_name == "docling"
                for parser_name in parser_names
            )
        ):
            logger.warning(
                "Docling produced %d legal articles for %s, but "
                "could not resolve effectivity targets %s. "
                "Retrying extraction with PyMuPDF.",
                len(articles),
                logical_doc_id,
                missing_effectivity_targets,
            )

            pymupdf_parts: List[PhysicalPDFPart] = []
            pymupdf_pages: List[ParsedPage] = []

            pymupdf_global_offset = 0

            try:
                for part_no, file_path in enumerate(
                    parts_paths,
                    start=1,
                ):
                    part_info, pages, _ = (
                        self._parse_with_pymupdf_file_for_fallback(
                            file_path=file_path,
                            logical_doc_id=logical_doc_id,
                            part_no=part_no,
                            page_offset=pymupdf_global_offset,
                        )
                    )

                    pymupdf_parts.append(part_info)
                    pymupdf_pages.extend(pages)

                    pymupdf_global_offset += (
                        part_info.total_physical_pages
                    )

                pymupdf_articles = self._extract_articles(
                    pages=pymupdf_pages,
                    logical_doc_id=logical_doc_id,
                )

                if pymupdf_articles:
                    logger.info(
                        "PyMuPDF fallback extracted %d legal "
                        "articles from %s.",
                        len(pymupdf_articles),
                        logical_doc_id,
                    )

                    all_parts = pymupdf_parts
                    all_pages = pymupdf_pages
                    articles = pymupdf_articles
                    parser_names = ["pymupdf"] * len(
                        pymupdf_parts
                    )
                    parser_used = "pymupdf"

                    markdown_sections = []

                    for part_no, file_path in enumerate(
                        parts_paths,
                        start=1,
                    ):
                        part_pages = [
                            page
                            for page in pymupdf_pages
                            if page.part_no == part_no
                        ]

                        part_markdown = "\n\n".join(
                            page.markdown_content
                            for page in part_pages
                            if page.markdown_content.strip()
                        )

                        markdown_sections.append(
                            f"<!-- PART {part_no}: "
                            f"{Path(file_path).name} -->\n"
                            f"{part_markdown}"
                        )

                    current_global_offset = (
                        pymupdf_global_offset
                    )

                else:
                    logger.warning(
                        "PyMuPDF fallback also extracted 0 "
                        "legal articles from %s.",
                        logical_doc_id,
                    )

            except Exception as exc:
                logger.warning(
                    "PyMuPDF article-extraction fallback failed "
                    "for %s: %s",
                    logical_doc_id,
                    exc,
                )

        # If the full-document parser fallback replaced the pages/articles,
        # refresh metadata and article-level effective dates from the
        # authoritative fallback text.
        if parser_used == "pymupdf":
            full_markdown = "\n\n".join(
                markdown_sections
            )

            metadata = self._extract_document_metadata(
                full_markdown=full_markdown,
                logical_doc_id=logical_doc_id,
                fallback_path=parts_paths[0],
            )
            if not metadata.get("issued_date"):
                header_text = self._extract_header_text(parts_paths[0])
                if header_text:
                    metadata = self._extract_document_metadata(
                        full_markdown=header_text + "\n" + full_markdown,
                        logical_doc_id=logical_doc_id,
                        fallback_path=parts_paths[0],
                    )

            (
                default_effective_from,
                effective_date_rules,
                effectivity_rules,
            ) = self._extract_effectivity_from_articles(articles)

            metadata["effective_from"] = (
                default_effective_from
            )
            metadata["effective_date_rules"] = (
                effective_date_rules
            )

            self._apply_effective_dates(
                articles=articles,
                effective_rules=effective_date_rules,
                default_effective_from=default_effective_from,
                issued_date=metadata.get("issued_date"),
            )

            parsed_provisions = [
                provision
                for article in articles
                for provision in provision_parser.parse_article(vars(article))
            ]
            provision_parser.apply_effectivity_rules(
                parsed_provisions,
                effectivity_rules,
            )

        return ParsedDocumentResult(
            logical_doc_id=logical_doc_id,
            parts=all_parts,
            pages=all_pages,
            articles=articles,
            provisions=parsed_provisions,
            effectivity_rules=effectivity_rules,
            full_markdown="\n\n".join(markdown_sections),
            parser_used=parser_used,
            total_global_pages=current_global_offset,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Document metadata extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_for_matching(text: str) -> str:
        normalized = unicodedata.normalize("NFD", text)
        normalized = "".join(
            char for char in normalized
            if unicodedata.category(char) != "Mn"
        )
        return normalized.replace(chr(273), "d").replace(chr(272), "d").lower()

    @staticmethod
    def _parse_vietnamese_date(
        day: str,
        month: str,
        year: str,
    ) -> Optional[str]:
        """
        Convert a Vietnamese textual date to ISO format.

        Invalid calendar dates are rejected instead of being silently
        normalized.
        """
        from datetime import date

        try:
            value = date(
                int(year),
                int(month),
                int(day),
            )
        except ValueError:
            return None

        return value.isoformat()

    @classmethod
    def _extract_first_date(cls, text: str) -> Optional[str]:
        normalized = cls._normalize_for_matching(text)
        pattern = re.compile(
            r"ngay\s+(\d{1,2})\s+thang\s+(\d{1,2})\s+nam\s+(\d{4})",
            re.IGNORECASE,
        )
        for match in pattern.finditer(normalized):
            parsed = cls._parse_vietnamese_date(*match.groups())
            if parsed:
                return parsed
        return None

    @classmethod
    def _extract_issued_date(
        cls,
        full_markdown: str,
    ) -> Optional[str]:
        """Prefer a date adjacent to the signing location in the PDF."""
        normalized_text = cls._normalize_for_matching(full_markdown)
        date_pattern = (
            r"ngay\s+(\d{1,2})\s+"
            r"thang\s+(\d{1,2})\s+"
            r"nam\s+(\d{4})"
        )
        location_date_pattern = re.compile(
            r"(?:ha\s+no?i|tp\.?\s*ho\s+chi\s+minh)"
            r"\s*[,;:]?\s*"
            + date_pattern,
            re.IGNORECASE,
        )
        # The signing date is part of the document header. Later pages may
        # repeat place/date text in amendments, citations, or appended material,
        # so selecting the last match can incorrectly turn an effective date
        # into the document's issue date. The first header-style match is the
        # source-backed signing date for the document.
        matches = list(location_date_pattern.finditer(normalized_text))
        if not matches:
            return None
        match = matches[0]

        return cls._parse_vietnamese_date(
            match.group(1), match.group(2), match.group(3)
        )

    @classmethod
    def _extract_doc_id(
        cls,
        full_markdown: str,
        fallback_logical_doc_id: Optional[str] = None,
    ) -> Optional[str]:
        normalized = cls._normalize_for_matching(full_markdown[:20000])
        pattern = re.compile(
            r"(?<![A-Za-z0-9])(\d+/\d{4}/[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)"
        )
        match = pattern.search(normalized)
        if not match:
            return fallback_logical_doc_id

        value = match.group(1)
        prefix, suffix = value.rsplit("/", 1)
        suffix = suffix.upper()
        if suffix == "ND-CP":
            suffix = "N" + chr(272) + "-CP"
        elif suffix.startswith("QD-"):
            suffix = "Q" + chr(272) + suffix[2:]
        return f"{prefix}/{suffix}"

    @classmethod
    def _extract_doc_type(
        cls,
        full_markdown: str,
        doc_id: Optional[str],
    ) -> Optional[str]:
        normalized = cls._normalize_for_matching(full_markdown[:20000])
        nghi_dinh = "Ngh\u1ecb \u0111\u1ecbnh"
        thong_tu = "Th\u00f4ng t\u01b0"
        luat = "Lu\u1eadt"
        quyet_dinh = "Quy\u1ebft \u0111\u1ecbnh"
        nghi_quyet = "Ngh\u1ecb quy\u1ebft"
        candidates = [
            (nghi_dinh, "nghi dinh"),
            (thong_tu, "thong tu"),
            (luat, "luat"),
            (quyet_dinh, "quyet dinh"),
            (nghi_quyet, "nghi quyet"),
        ]
        for doc_type, alias in candidates:
            if alias in normalized:
                return doc_type

        if doc_id:
            normalized_id = cls._normalize_for_matching(doc_id)
            if "/nd-cp" in normalized_id:
                return nghi_dinh
            if "/tt-" in normalized_id:
                return thong_tu
            if "/qd-" in normalized_id:
                return quyet_dinh
            if "/nq-" in normalized_id:
                return nghi_quyet
        return None

    @classmethod
    def _extract_title(
        cls,
        full_markdown: str,
        doc_id: Optional[str],
        doc_type: Optional[str],
    ) -> Optional[str]:
        """
        Extract a concise title from the opening part of the PDF.

        If the source title is not safely recoverable, fall back to the
        legal type + document ID rather than inventing a substantive title.
        """
        head = full_markdown[:16000]

        if doc_id:
            # Prefer a source line that contains the document ID.
            for line in head.splitlines():
                clean = re.sub(
                    r"^[#*\s]+|[#*\s]+$",
                    "",
                    line,
                ).strip()

                if doc_id in clean:
                    if len(clean) <= 300:
                        return clean

        if doc_type and doc_id:
            return f"{doc_type} {doc_id}"

        return doc_id

    @classmethod
    def _extract_effective_date_rules(
        cls,
        effective_article_content: str,
    ) -> Tuple[Optional[str], Dict[str, Optional[str]]]:
        document_rule, scoped_rules = ProvisionParser.extract_effectivity_rules(
            effective_article_content,
            cls._parse_vietnamese_date,
        )
        default_effective_from = (
            document_rule.get("effective_from")
            if document_rule and document_rule.get("mode") == "explicit_date"
            else None
        )
        article_date_rules = {
            rule["target_path"]: rule["effective_from"]
            for rule in scoped_rules
            if "." not in rule["target_path"]
            and rule["mode"] == "explicit_date"
        }
        return default_effective_from, article_date_rules

    def _extract_header_text(self, file_path: str) -> str:
        """OCR the first-page header when the hybrid parser omits it."""
        if not HAS_PYMUPDF or fitz is None:
            return ""
        try:
            import numpy as np
            from rapidocr import EngineType, RapidOCR
        except ImportError:
            logger.warning("Header OCR dependencies are unavailable.")
            return ""

        if not self._header_ocr_attempted:
            self._header_ocr_attempted = True
            try:
                engine_type = EngineType(self.header_ocr_engine_type.lower())
                params = {
                    "Det.engine_type": engine_type,
                    "Cls.engine_type": engine_type,
                    "Rec.engine_type": engine_type,
                }
                self._header_ocr_engine = RapidOCR(params=params)
            except Exception as exc:
                logger.warning("Could not initialize header OCR: %s", exc)
                return ""

        if self._header_ocr_engine is None:
            return ""

        try:
            with fitz.open(file_path) as document:
                if not len(document):
                    return ""
                page = document[0]
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(2, 2),
                    alpha=False,
                )
                image = np.frombuffer(
                    pixmap.samples,
                    dtype=np.uint8,
                ).reshape(pixmap.height, pixmap.width, pixmap.n)
                header = image[: int(pixmap.height * 0.28), :, :3]
                result = self._header_ocr_engine(header)
                return "\n".join(getattr(result, "txts", None) or [])
        except Exception as exc:
            logger.warning(
                "Could not OCR first-page metadata for %s: %s",
                file_path,
                exc,
            )
            return ""

    @classmethod
    def _extract_document_metadata(
        cls,
        full_markdown: str,
        logical_doc_id: Optional[str],
        fallback_path: str,
    ) -> Dict[str, Any]:
        """
        Extract document-level legal metadata from the PDF text.

        The parser does not hard-code dates from the ingestion script.
        If a required field cannot be extracted, it remains None so the
        document contract can reject the document rather than accepting
        fabricated metadata.
        """
        doc_id = cls._extract_doc_id(
            full_markdown=full_markdown,
            fallback_logical_doc_id=logical_doc_id,
        )

        doc_type = cls._extract_doc_type(
            full_markdown=full_markdown,
            doc_id=doc_id,
        )

        title = cls._extract_title(
            full_markdown=full_markdown,
            doc_id=doc_id,
            doc_type=doc_type,
        )

        issued_date = cls._extract_issued_date(
            full_markdown
        )

        metadata: Dict[str, Any] = {
            "doc_id": doc_id,
            "title": title,
            "issued_date": issued_date,
            "effective_from": None,
            "effective_to": None,
            "doc_type": doc_type,
            "storage_path": str(fallback_path),
            "effective_date_rules": {},
        }

        return metadata

    @classmethod
    def _apply_effective_dates(
        cls,
        articles: List[ParsedArticle],
        effective_rules: Dict[str, Optional[str]],
        default_effective_from: Optional[str],
        issued_date: Optional[str] = None,
    ) -> None:
        """
        Assign the document's default effective date to every article,
        then override articles explicitly listed in the effectiveness
        provision.

        This matches the legal pattern where one article establishes a
        general effective date and later clauses carve out exceptions for
        specific articles.
        """
        for article in articles:
            article.issued_date = issued_date
            article.effective_from = (
                effective_rules.get(
                    article.article_number,
                    default_effective_from,
                )
            )

    @classmethod
    def _extract_effective_dates_from_articles(
        cls,
        articles: List[ParsedArticle],
    ) -> Tuple[
        Optional[str],
        Dict[str, Optional[str]],
    ]:
        """Compatibility wrapper for article-level effective-date fields."""
        default_date, article_rules, _ = cls._extract_effectivity_from_articles(articles)
        return default_date, article_rules

    @classmethod
    def _extract_effectivity_from_articles(
        cls,
        articles: List[ParsedArticle],
    ) -> Tuple[Optional[str], Dict[str, Optional[str]], List[Dict[str, Any]]]:
        """Extract the source's general, scoped, and conditional rules."""
        document_rule: Optional[Dict[str, Any]] = None
        article_rules: Dict[str, Optional[str]] = {}
        all_rules: List[Dict[str, Any]] = []

        for article in articles:
            normalized_title = cls._normalize_for_matching(
                article.article_title
            )
            normalized_content = cls._normalize_for_matching(
                article.content
            )

            if (
                "hieu luc thi hanh" in normalized_title
                or "hieu luc thi hanh" in normalized_content
                or "co hieu luc" in normalized_content
            ):
                article_document_rule, scoped_rules = (
                    ProvisionParser.extract_effectivity_rules(
                        article.content,
                        cls._parse_vietnamese_date,
                    )
                )

                if article_document_rule:
                    # Keep the first explicit document-wide rule rather than
                    # letting a later scoped exception replace the default.
                    if (
                        document_rule is None
                        or (
                            document_rule.get("mode") != "explicit_date"
                            and article_document_rule.get("mode") == "explicit_date"
                        )
                    ):
                        document_rule = article_document_rule
                    article_document_rule["target_path"] = "*"
                    scoped_rules.insert(0, article_document_rule)

                for rule in scoped_rules:
                    if (
                        rule["target_path"] != "*"
                        and "." not in rule["target_path"]
                        and rule["mode"] == "explicit_date"
                    ):
                        article_rules[rule["target_path"]] = rule["effective_from"]

                source_pages = sorted(
                    {
                        segment.get("global_page")
                        for segment in article.page_segments
                        if segment.get("global_page") is not None
                    }
                )
                for rule in scoped_rules:
                    rule["source_article_id"] = article.article_id
                    rule["source_pages"] = source_pages
                all_rules.extend(scoped_rules)

        default_date = (
            document_rule.get("effective_from")
            if document_rule
            and document_rule.get("mode") == "explicit_date"
            else None
        )
        # A document-wide rule is stored once. Keeping multiple wildcard
        # entries would make the provision resolver's dictionary collapse to
        # the last one, potentially replacing an explicit default with a later
        # conditional statement.
        resolved_rules = [
            rule for rule in all_rules if rule.get("target_path") != "*"
        ]
        if document_rule is not None:
            resolved_rules.append(document_rule)

        return default_date, article_rules, resolved_rules

    # ------------------------------------------------------------------
    # Article extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_article_pattern() -> re.Pattern:
        dieu = "\u0110i\u1ec1u"
        return re.compile(
            r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*[ \t]*)?"
            + rf"(?:{dieu}|\u0110iu|Diu|Dieu)"
            + r"[ \t\r\n]+(\d+[A-Za-z]?)[ \t]*\.?(?:\*\*)?",
            re.IGNORECASE,
        )

    @staticmethod
    def _count_article_markers(text: str) -> int:
        normalized = PDFParser._normalize_for_matching(text)
        pattern = re.compile(
            r"(?m)^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*[ \t]*)?"
            r"(?:dieu|diu)[ \t\r\n]+\d+[a-z]?[ \t]*\.?(?:\*\*)?",
            re.IGNORECASE,
        )
        return len(pattern.findall(normalized))

    def _extract_articles(
        self,
        pages: List[ParsedPage],
        logical_doc_id: str,
    ) -> List[ParsedArticle]:
        """
        Extract legal articles while preserving their page spans.

        Article detection operates on raw page text so that article
        character offsets remain aligned with the original page text.

        The extractor tolerates common PDF/OCR layout variations such as:
            Äiá»u 1.
            Äiá»u 1. Pháº¡m vi Ä‘iá»u chá»‰nh
            Äiu 1. Phm vi Ä‘iu chÃ­nh
            Äiá»u
            1.
            **Äiá»u 1.**
        """

        if not pages:
            return []

        page_texts: List[str] = []
        page_ranges: List[
            Tuple[int, int, ParsedPage, int]
        ] = []

        cursor = 0

        for index, page in enumerate(pages):
            # raw_text is the canonical source for article detection.
            # markdown_content contains provenance/layout annotations
            # intended for downstream representation.
            page_text = (
                page.raw_text
                if isinstance(page.raw_text, str)
                else ""
            ).strip()

            # Defensive fallback for parser implementations that may
            # provide markdown but no raw text.
            if not page_text:
                page_text = re.sub(
                    r"<!--.*?-->",
                    "",
                    page.markdown_content,
                    flags=re.DOTALL,
                ).strip()

            page_texts.append(page_text)

            start = cursor
            end = start + len(page_text)

            page_ranges.append(
                (
                    start,
                    end,
                    page,
                    index,
                )
            )

            cursor = end

            if index < len(pages) - 1:
                cursor += 2

        full_text = "\n\n".join(page_texts)

        if not full_text.strip():
            logger.warning(
                "No text available for article extraction: %s",
                logical_doc_id,
            )
            return []

        marker_count = self._count_article_markers(
            full_text
        )

        logger.info(
            "Detected %d potential legal article markers "
            "in %s.",
            marker_count,
            logical_doc_id,
        )

        if marker_count == 0:
            logger.warning(
                "No legal article headings detected in %s. "
                "Accepted heading variants include "
                "'Äiá»u <number>' and OCR variants such as "
                "'Äiu <number>'.",
                logical_doc_id,
            )
            return []

        # --------------------------------------------------------------
        # Article heading positions
        # --------------------------------------------------------------

        heading_pattern = self._build_article_pattern()

        candidate_matches = list(heading_pattern.finditer(full_text))
        matches = []
        seen_article_numbers = set()
        for candidate in candidate_matches:
            article_number = candidate.group(1).strip().lower()
            if article_number in seen_article_numbers:
                logger.warning(
                    "Ignoring repeated article-heading candidate %s in %s; "
                    "keeping its text inside the surrounding provision.",
                    article_number,
                    logical_doc_id,
                )
                continue
            seen_article_numbers.add(article_number)
            matches.append(candidate)

        if not matches:
            logger.warning(
                "Article marker diagnostics detected %d potential "
                "markers, but no article headings matched the full "
                "extraction pattern for %s.",
                marker_count,
                logical_doc_id,
            )
            return []

        articles: List[ParsedArticle] = []

        for index, match in enumerate(matches):
            article_number = match.group(1).strip()

            # The article starts at the beginning of the heading match.
            article_start = match.start()

            # The content starts after the matched heading prefix.
            content_start = match.end()

            # The next article begins where the next heading begins.
            # Otherwise the current article runs until end of document.
            if index + 1 < len(matches):
                article_end = matches[index + 1].start()
            else:
                article_end = len(full_text)

            # ----------------------------------------------------------
            # Article content
            # ----------------------------------------------------------

            content_start_actual = content_start

            while (
                content_start_actual < article_end
                and full_text[
                    content_start_actual
                ].isspace()
            ):
                content_start_actual += 1

            content_end_actual = article_end

            while (
                content_end_actual > content_start_actual
                and full_text[
                    content_end_actual - 1
                ].isspace()
            ):
                content_end_actual -= 1

            raw_content = full_text[
                content_start_actual:content_end_actual
            ].strip()

            if not raw_content:
                logger.debug(
                    "Article %s has empty content in %s.",
                    article_number,
                    logical_doc_id,
                )

            # ----------------------------------------------------------
            # Article title
            # ----------------------------------------------------------

            content_lines = raw_content.split(
                "\n",
                1,
            )

            first_line = (
                content_lines[0]
                .strip()
                .strip("*#: ")
            )

            article_title = f"\u0110i\u1ec1u {article_number}"

            if first_line and len(first_line) < 120:
                article_title += f": {first_line}"

            # ----------------------------------------------------------
            # Determine every page touched by the article
            # ----------------------------------------------------------

            overlapping_pages: List[ParsedPage] = []
            page_segments: List[Dict[str, Any]] = []

            article_match_start = article_start
            article_match_end = max(
                article_end,
                content_end_actual,
            )

            for (
                page_start,
                page_end,
                page,
                page_index,
            ) in page_ranges:
                overlaps_article = (
                    page_end > article_match_start
                    and page_start < article_match_end
                )

                if overlaps_article:
                    overlapping_pages.append(page)

                # Only article content, not the article heading,
                # becomes the page segment payload used downstream.
                segment_start = max(
                    content_start_actual,
                    page_start,
                )
                segment_end = min(
                    content_end_actual,
                    page_end,
                )

                if segment_start >= segment_end:
                    continue

                local_start = (
                    segment_start - page_start
                )
                local_end = (
                    segment_end - page_start
                )

                raw_segment_text = page_texts[
                    page_index
                ][local_start:local_end]
                leading_trim = len(raw_segment_text) - len(
                    raw_segment_text.lstrip()
                )
                trailing_trimmed_end = len(raw_segment_text.rstrip())
                segment_text = raw_segment_text.strip()

                if not segment_text:
                    continue

                page_segments.append(
                    {
                        "physical_file_id": (
                            page.physical_file_id
                        ),
                        "file_path": page.file_path,
                        "part_no": page.part_no,
                        "physical_page": (
                            page.physical_page
                        ),
                        "global_page": page.global_page,
                        "text": segment_text,
                        "text_start": (
                            segment_start
                            + leading_trim
                            - content_start_actual
                        ),
                        "text_end": (
                            segment_start
                            + trailing_trimmed_end
                            - content_start_actual
                        ),
                    }
                )

            if not overlapping_pages:
                logger.warning(
                    "Could not map article %s to a physical page "
                    "in document %s.",
                    article_number,
                    logical_doc_id,
                )
                continue

            # Remove accidental duplicates while preserving page order.
            unique_pages: List[ParsedPage] = []
            seen_page_keys = set()

            for page in overlapping_pages:
                page_key = (
                    page.physical_file_id,
                    page.physical_page,
                )

                if page_key in seen_page_keys:
                    continue

                seen_page_keys.add(page_key)
                unique_pages.append(page)

            start_page = unique_pages[0]
            end_page = unique_pages[-1]

            articles.append(
                ParsedArticle(
                    article_id=f"{logical_doc_id}:article:{article_number}",
                    logical_doc_id=logical_doc_id,
                    article_number=article_number,
                    article_title=article_title,
                    content=raw_content,
                    start_physical_file_id=(
                        start_page.physical_file_id
                    ),
                    start_physical_page=(
                        start_page.physical_page
                    ),
                    end_physical_file_id=(
                        end_page.physical_file_id
                    ),
                    end_physical_page=(
                        end_page.physical_page
                    ),
                    start_global_page=(
                        start_page.global_page
                    ),
                    end_global_page=(
                        end_page.global_page
                    ),
                    page_segments=page_segments,
                )
            )

        logger.info(
            "Extracted %d legal articles from %s.",
            len(articles),
            logical_doc_id,
        )

        return articles

    # ------------------------------------------------------------------
    # Table normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _build_normalized_table(
        rows: List[List[Any]],
        markdown: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Normalize parser-specific table output into the table structure
        consumed by QualityMetricsCalculator.

        Canonical structure:

            {
                "rows": int,
                "columns": int,
                "cells": [
                    {
                        "row": int,
                        "column": int,
                        "rowspan": int,
                        "colspan": int,
                        "value": Any,
                    }
                ],
                "markdown": str | None,
            }

        Parser-specific merged-cell information is preserved when
        available by callers. This generic normalizer assumes each
        extracted matrix cell occupies one row and one column.
        """

        if not rows:
            return {
                "rows": 0,
                "columns": 0,
                "cells": [],
                "markdown": markdown,
            }

        column_count = max(
            (len(row) for row in rows),
            default=0,
        )

        cells: List[Dict[str, Any]] = []

        for row_index, row in enumerate(rows):
            for column_index in range(column_count):
                value = (
                    row[column_index]
                    if column_index < len(row)
                    else None
                )

                cells.append(
                    {
                        "row": row_index,
                        "column": column_index,
                        "rowspan": 1,
                        "colspan": 1,
                        "value": value,
                    }
                )

        return {
            "rows": len(rows),
            "columns": column_count,
            "cells": cells,
            "markdown": markdown,
        }

    # ------------------------------------------------------------------
    # Docling implementation
    # ------------------------------------------------------------------

    def _parse_with_docling(
        self,
        file_path: str,
        logical_doc_id: str,
        file_hash: str,
        part_no: int,
        page_offset: int,
    ) -> Tuple[List[ParsedPage], str]:
        if self.docling_converter is None:
            raise RuntimeError(
                "Docling converter is not initialized."
            )

        conversion_result = self.docling_converter.convert(
            file_path
        )

        doc = conversion_result.document

        page_numbers = self._get_docling_page_numbers(doc)

        if not page_numbers:
            raise RuntimeError(
                "Docling document contains no page provenance."
            )

        page_content: Dict[int, List[str]] = {
            page: []
            for page in page_numbers
        }

        page_tables: Dict[
            int,
            List[Dict[str, Any]],
        ] = {
            page: []
            for page in page_numbers
        }

        for item, _level in doc.iterate_items():
            provenance = getattr(
                item,
                "prov",
                None,
            )

            if not provenance:
                continue

            text = self._extract_docling_item_text(
                item,
                doc,
            )

            item_page_numbers = (
                self._extract_item_page_numbers(
                    provenance
                )
            )

            if not item_page_numbers:
                continue

            for physical_page in item_page_numbers:
                if physical_page not in page_content:
                    page_content[physical_page] = []

                if text.strip():
                    page_content[physical_page].append(
                        text.strip()
                    )

                if (
                    TableItem is not None
                    and isinstance(item, TableItem)
                ):
                    table_data = (
                        self._extract_docling_table(
                            item,
                            doc,
                        )
                    )

                    if table_data is not None:
                        page_tables.setdefault(
                            physical_page,
                            [],
                        ).append(table_data)

        parsed_pages: List[ParsedPage] = []

        for physical_page in sorted(page_content):
            global_page = (
                page_offset + physical_page
            )

            raw_text = "\n\n".join(
                page_content[physical_page]
            ).strip()

            markdown_body = (
                self._build_page_markdown(
                    page_content[physical_page]
                )
            )

            provenance_header = (
                "<!-- PageProvenance: "
                f"physical_file={file_hash}, "
                f"part={part_no}, "
                f"physical_page={physical_page}, "
                f"global_page={global_page} -->"
            )

            markdown = (
                f"{provenance_header}\n"
                f"{markdown_body}"
            )

            parsed_pages.append(
                ParsedPage(
                    logical_doc_id=logical_doc_id,
                    physical_file_id=file_hash,
                    file_path=file_path,
                    part_no=part_no,
                    physical_page=physical_page,
                    global_page=global_page,
                    raw_text=raw_text,
                    markdown_content=markdown,
                    tables=page_tables.get(
                        physical_page,
                        [],
                    ),
                    metadata={
                        "parser": "docling",
                        "extraction_status": "complete",
                    },
                )
            )

        return parsed_pages, "docling"

    @staticmethod
    def _get_docling_page_numbers(
        doc: Any,
    ) -> List[int]:
        page_numbers = set()

        try:
            for item, _level in doc.iterate_items():
                provenance = getattr(
                    item,
                    "prov",
                    None,
                )

                if not provenance:
                    continue

                for prov in provenance:
                    page_no = getattr(
                        prov,
                        "page_no",
                        None,
                    )

                    if isinstance(page_no, int):
                        page_numbers.add(page_no)

        except Exception as exc:
            logger.debug(
                "Could not inspect Docling provenance: %s",
                exc,
            )

        if not page_numbers:
            pages = getattr(
                doc,
                "pages",
                None,
            )

            if pages:
                if isinstance(pages, dict):
                    for key in pages:
                        try:
                            page_numbers.add(
                                int(key)
                            )
                        except (
                            TypeError,
                            ValueError,
                        ):
                            continue
                else:
                    try:
                        page_numbers.update(
                            range(
                                1,
                                len(pages) + 1,
                            )
                        )
                    except TypeError:
                        pass

        return sorted(page_numbers)

    @staticmethod
    def _extract_item_page_numbers(
        provenance: Any,
    ) -> List[int]:
        page_numbers = set()

        try:
            for prov in provenance:
                page_no = getattr(
                    prov,
                    "page_no",
                    None,
                )

                if isinstance(page_no, int):
                    page_numbers.add(page_no)

        except TypeError:
            page_no = getattr(
                provenance,
                "page_no",
                None,
            )

            if isinstance(page_no, int):
                page_numbers.add(page_no)

        return sorted(page_numbers)

    @staticmethod
    def _extract_docling_item_text(
        item: Any,
        doc: Any,
    ) -> str:
        if (
            TableItem is not None
            and isinstance(item, TableItem)
        ):
            return ""

        # 1. Plain text
        text = getattr(
            item,
            "text",
            None,
        )

        if isinstance(text, str) and text.strip():
            return text.strip()

        # 2. Section / heading labels
        label = getattr(
            item,
            "label",
            None,
        )

        if isinstance(label, str) and label.strip():
            return label.strip()

        # 3. Export markdown
        export_md = getattr(
            item,
            "export_to_markdown",
            None,
        )

        if callable(export_md):
            try:
                value = export_md(doc=doc)

                if (
                    isinstance(value, str)
                    and value.strip()
                ):
                    return value.strip()

            except Exception:
                pass

        # 4. Export text
        export_txt = getattr(
            item,
            "export_to_text",
            None,
        )

        if callable(export_txt):
            try:
                value = export_txt(doc=doc)

                if (
                    isinstance(value, str)
                    and value.strip()
                ):
                    return value.strip()

            except Exception:
                pass

        return ""

    @classmethod
    def _extract_docling_table(
        cls,
        table: Any,
        doc: Any,
    ) -> Optional[Dict[str, Any]]:
        """
        Convert a Docling table into the canonical table structure
        consumed by QualityMetricsCalculator.

        DataFrame export gives us a reliable rectangular representation.
        It does not necessarily preserve merged-cell geometry, so the
        normalized representation uses rowspan=1 and colspan=1 unless
        richer geometry is available from the parser.
        """

        try:
            dataframe = table.export_to_dataframe(
                doc=doc
            )

            rows = dataframe.values.tolist()

            markdown = table.export_to_markdown(
                doc=doc
            )

            normalized = cls._build_normalized_table(
                rows=rows,
                markdown=markdown,
            )

            normalized["columns"] = len(
                dataframe.columns
            )

            return normalized

        except Exception as exc:
            logger.debug(
                "Failed to export Docling table: %s",
                exc,
            )

            return None

    @staticmethod
    def _build_page_markdown(
        content_items: List[str],
    ) -> str:
        if not content_items:
            return ""

        return "\n\n".join(
            item
            for item in content_items
            if item.strip()
        )

    # ------------------------------------------------------------------
    # PyMuPDF implementation
    # ------------------------------------------------------------------

    def _parse_with_pymupdf(
        self,
        file_path: str,
        logical_doc_id: str,
        file_hash: str,
        part_no: int,
        page_offset: int,
    ) -> Tuple[List[ParsedPage], str]:
        if not HAS_PYMUPDF or fitz is None:
            raise RuntimeError(
                "PyMuPDF is unavailable."
            )

        parsed_pages: List[ParsedPage] = []

        with fitz.open(file_path) as doc:
            for physical_page, page in enumerate(
                doc,
                start=1,
            ):
                global_page = (
                    page_offset + physical_page
                )

                text = page.get_text(
                    "text"
                ).strip()

                provenance_header = (
                    "<!-- PageProvenance: "
                    f"physical_file={file_hash}, "
                    f"part={part_no}, "
                    f"physical_page={physical_page}, "
                    f"global_page={global_page} -->"
                )

                markdown = (
                    f"{provenance_header}\n"
                    f"{text}"
                )

                tables = (
                    self._extract_pymupdf_tables(
                        page
                    )
                )

                parsed_pages.append(
                    ParsedPage(
                        logical_doc_id=logical_doc_id,
                        physical_file_id=file_hash,
                        file_path=file_path,
                        part_no=part_no,
                        physical_page=physical_page,
                        global_page=global_page,
                        raw_text=text,
                        markdown_content=markdown,
                        tables=tables,
                        metadata={
                            "parser": "pymupdf",
                            "extraction_status": "complete",
                        },
                    )
                )

        return parsed_pages, "pymupdf"

    def _parse_with_pymupdf_file_for_fallback(
        self,
        file_path: str,
        logical_doc_id: str,
        part_no: int,
        page_offset: int,
    ) -> Tuple[
        PhysicalPDFPart,
        List[ParsedPage],
        str,
    ]:
        """
        Explicit PyMuPDF parsing path used when Docling produced pages
        but failed to produce a usable legal-article structure.

        This method deliberately bypasses Docling preference.
        """

        path = Path(file_path)

        file_hash = calculate_file_hash(
            str(path), self.hash_algorithm
        )

        pages, parser_used = self._parse_with_pymupdf(
            file_path=str(path),
            logical_doc_id=logical_doc_id,
            file_hash=file_hash,
            part_no=part_no,
            page_offset=page_offset,
        )

        if not pages:
            raise RuntimeError(
                f"PyMuPDF returned no pages for fallback file: "
                f"{file_path}"
            )

        part_info = PhysicalPDFPart(
            file_path=str(path),
            file_hash=file_hash,
            part_no=part_no,
            total_physical_pages=len(pages),
            start_global_page=page_offset + 1,
        )

        return part_info, pages, parser_used

    @staticmethod
    def _extract_pymupdf_tables(
        page: Any,
    ) -> List[Dict[str, Any]]:
        """
        Extract PyMuPDF tables and normalize them into the canonical
        table representation consumed by QualityMetricsCalculator.
        """

        tables: List[Dict[str, Any]] = []

        try:
            table_finder = page.find_tables()

            if not table_finder:
                return tables

            detected_tables = getattr(
                table_finder,
                "tables",
                [],
            )

            for table in detected_tables:
                try:
                    extracted = table.extract()

                    if not extracted:
                        continue

                    normalized = (
                        PDFParser._build_normalized_table(
                            rows=extracted,
                        )
                    )

                    tables.append(normalized)

                except Exception as exc:
                    logger.debug(
                        "Failed to extract one PyMuPDF table: %s",
                        exc,
                    )

        except Exception as exc:
            logger.debug(
                "PyMuPDF table detection failed: %s",
                exc,
            )

        return tables
