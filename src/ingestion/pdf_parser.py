import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    Represents one physical PDF file belonging to a logical legal document.

    Each physical PDF has its own SHA-256 hash and therefore its own
    physical_file_id.
    """

    file_path: str
    file_hash: str
    part_no: int = 1
    total_physical_pages: int = 0
    start_global_page: int = 1


@dataclass
class ParsedPage:
    """
    Parsed page with physical and logical provenance.

    physical_page:
        1-indexed page number inside the physical PDF.

    global_page:
        1-indexed page number across the complete logical document.
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
    Complete parsing result for one logical legal document.

    A logical document may consist of multiple physical PDF parts.
    """

    logical_doc_id: str
    parts: List[PhysicalPDFPart]
    pages: List[ParsedPage]
    full_markdown: str
    parser_used: str
    total_global_pages: int


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def calculate_file_hash(
    file_path: str,
    algorithm: str = "sha256",
) -> str:
    """
    Calculate the checksum of a physical PDF file.

    SHA-256 is the default because the provenance contract requires
    document hashing.
    """

    hasher = hashlib.new(algorithm)

    with open(file_path, "rb") as file:
        while chunk := file.read(1024 * 1024):
            hasher.update(chunk)

    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# PDF Parser
# ---------------------------------------------------------------------------

class PDFParser:
    """
    Hybrid PDF parser.

    Parsing strategy:
        1. Prefer Docling when available.
        2. Fall back to PyMuPDF when Docling fails or is unavailable.

    Responsibilities:
        - Parse physical PDF files.
        - Preserve logical/physical document identity.
        - Preserve physical and global page numbers.
        - Extract text, markdown and tables.
        - Preserve page-level provenance.

    Non-responsibilities:
        - Document metadata validation.
        - Contract validation.
        - Chunk generation.
        - Cross-document temporal validation.
        - Retrieval/vector indexing.
    """

    def __init__(self, prefer_docling: bool = True):
        self.prefer_docling = prefer_docling and HAS_DOCLING
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
        """
        Parse one physical PDF file.

        Args:
            file_path:
                Path to the physical PDF.

            logical_doc_id:
                Logical legal document identifier shared by all parts.

            part_no:
                Physical part sequence number.

            page_offset:
                Number of pages belonging to previous physical parts.

        Returns:
            (
                PhysicalPDFPart,
                List[ParsedPage],
                parser_used
            )
        """

        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(
                f"PDF file not found: {file_path}"
            )

        if path.suffix.lower() != ".pdf":
            raise ValueError(
                f"Expected a PDF file, got: {path.suffix}"
            )

        file_hash = calculate_file_hash(str(path))

        # The caller should normally provide the logical document ID.
        # Filename stem is only a safe fallback.
        doc_id = logical_doc_id or path.stem

        pages: List[ParsedPage] = []
        parser_used = "pymupdf"

        # --------------------------------------------------------------
        # Primary parser: Docling
        # --------------------------------------------------------------

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

        # --------------------------------------------------------------
        # Fallback parser: PyMuPDF
        # --------------------------------------------------------------

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
        logical_doc_id: str,
    ) -> ParsedDocumentResult:
        """
        Parse an ordered collection of physical PDFs belonging to
        one logical legal document.

        Example:

            logical_doc_id = "100/2019/NĐ-CP"

            part1.pdf -> global pages 1..20
            part2.pdf -> global pages 21..40
            part3.pdf -> global pages 41..55

        The caller is responsible for determining:
            - which files belong to the logical document;
            - their correct order.

        This method only preserves that supplied ordering.
        """

        if not parts_paths:
            raise ValueError(
                "parts_paths must contain at least one PDF file."
            )

        if not logical_doc_id.strip():
            raise ValueError(
                "logical_doc_id must not be empty."
            )

        all_parts: List[PhysicalPDFPart] = []
        all_pages: List[ParsedPage] = []
        markdown_sections: List[str] = []

        current_global_offset = 0
        parser_names: List[str] = []

        for part_no, file_path in enumerate(
            parts_paths,
            start=1,
        ):
            part_info, pages, parser_used = self.parse_file(
                file_path=file_path,
                logical_doc_id=logical_doc_id,
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

        # Preserve information when different parts use
        # different parser engines because of fallback.
        unique_parsers = list(dict.fromkeys(parser_names))

        if len(unique_parsers) == 1:
            parser_used = unique_parsers[0]
        else:
            parser_used = "mixed"

        return ParsedDocumentResult(
            logical_doc_id=logical_doc_id,
            parts=all_parts,
            pages=all_pages,
            full_markdown="\n\n".join(markdown_sections),
            parser_used=parser_used,
            total_global_pages=current_global_offset,
        )

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
        """
        Parse PDF using modern Docling v2 APIs.

        Docling provides:
            - structured document representation;
            - Markdown export;
            - table extraction;
            - item provenance information.

        Page-level content is reconstructed from Docling items according
        to their page provenance.
        """

        if self.docling_converter is None:
            raise RuntimeError(
                "Docling converter is not initialized."
            )

        conversion_result = self.docling_converter.convert(
            file_path
        )

        doc = conversion_result.document

        # First determine the physical page count.
        page_numbers = self._get_docling_page_numbers(doc)

        if not page_numbers:
            raise RuntimeError(
                "Docling document contains no page provenance."
            )

        page_content: Dict[int, List[str]] = {
            page_no: []
            for page_no in page_numbers
        }

        page_tables: Dict[
            int,
            List[Dict[str, Any]],
        ] = {
            page_no: []
            for page_no in page_numbers
        }

        # --------------------------------------------------------------
        # Iterate Docling items in reading order.
        # --------------------------------------------------------------

        for item, _level in doc.iterate_items():
            provenance = getattr(item, "prov", None)

            if not provenance:
                continue

            text = self._extract_docling_item_text(
                item,
                doc,
            )

            item_page_numbers = self._extract_item_page_numbers(
                provenance
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

                # Tables are handled separately so that quality
                # metrics can inspect structured cells.
                if (
                    TableItem is not None
                    and isinstance(item, TableItem)
                ):
                    table_data = self._extract_docling_table(
                        item,
                        doc,
                    )

                    if table_data is not None:
                        page_tables[physical_page].append(
                            table_data
                        )

        # --------------------------------------------------------------
        # Build ParsedPage objects.
        # --------------------------------------------------------------

        parsed_pages: List[ParsedPage] = []

        for physical_page in sorted(page_content):
            global_page = (
                page_offset + physical_page
            )

            raw_text = "\n\n".join(
                page_content[physical_page]
            ).strip()

            # If Docling has no textual item for a page, keep the page
            # empty rather than generating fake content.
            markdown_body = self._build_page_markdown(
                page_content[physical_page]
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
    def _get_docling_page_numbers(doc: Any) -> List[int]:
        """
        Extract physical page numbers available in Docling provenance.

        Falls back to doc.pages when available.
        """

        page_numbers = set()

        # Modern Docling exposes page information through provenance.
        try:
            for item, _level in doc.iterate_items():
                provenance = getattr(item, "prov", None)

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
                "Could not inspect Docling item provenance: %s",
                exc,
            )

        # Fallback to document pages if available.
        if not page_numbers:
            pages = getattr(doc, "pages", None)

            if pages:
                if isinstance(pages, dict):
                    for key in pages:
                        try:
                            page_numbers.add(int(key))
                        except (TypeError, ValueError):
                            continue
                else:
                    try:
                        page_numbers.update(
                            range(1, len(pages) + 1)
                        )
                    except TypeError:
                        pass

        return sorted(page_numbers)

    @staticmethod
    def _extract_item_page_numbers(
        provenance: Any,
    ) -> List[int]:
        """Extract unique physical page numbers from Docling provenance."""

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
        """
        Extract readable text from a Docling item.

        Tables are intentionally excluded here because they are stored
        separately in ParsedPage.tables.
        """

        if (
            TableItem is not None
            and isinstance(item, TableItem)
        ):
            return ""

        # TextItem and similar items expose .text.
        text = getattr(item, "text", None)

        if isinstance(text, str):
            return text

        # Some Docling items expose export_to_markdown().
        export_method = getattr(
            item,
            "export_to_markdown",
            None,
        )

        if callable(export_method):
            try:
                value = export_method(doc=doc)

                if isinstance(value, str):
                    return value
            except Exception:
                pass

        return ""

    @staticmethod
    def _extract_docling_table(
        table: Any,
        doc: Any,
    ) -> Optional[Dict[str, Any]]:
        """Convert a Docling TableItem into serializable table data."""

        try:
            dataframe = table.export_to_dataframe(
                doc=doc
            )

            return {
                "data": dataframe.to_dict(
                    orient="records"
                ),
                "columns": [
                    str(column)
                    for column in dataframe.columns
                ],
                "markdown": table.export_to_markdown(
                    doc=doc
                ),
            }

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
        """Build page-level Markdown from ordered Docling items."""

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
        """
        Parse PDF using PyMuPDF.

        This is the deterministic fallback when Docling is unavailable
        or fails.
        """

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

                tables = self._extract_pymupdf_tables(
                    page
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

    @staticmethod
    def _extract_pymupdf_tables(
        page: Any,
    ) -> List[Dict[str, Any]]:
        """
        Best-effort table extraction using PyMuPDF.

        Table extraction failure does not invalidate the page itself.
        Contract/quality validation is responsible for deciding whether
        the resulting table quality is acceptable.
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

                    tables.append(
                        {
                            "data": extracted,
                        }
                    )
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