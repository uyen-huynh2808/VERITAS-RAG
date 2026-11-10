import re
import unicodedata
from typing import Any, Dict, List, Optional


class QualityMetricsCalculator:
    """
    Calculates extraction-quality indicators.

    The calculator does not decide PASS/FAIL.
    Threshold decisions belong to Quality Contract.
    """

    OCR_NOISE_PATTERN = re.compile(
        r"[^\w\sÀ-ỹ.,;:!?%(){}\[\]\"'“”‘’\-+/=\*#|<>]"
    )

    VIETNAMESE_DIACRITIC_PATTERN = re.compile(
        r"[ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚĂĐĨŨƠƯẠẢẤẦẨẪẬẮẰẲẴẶ"
        r"ẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰ"
        r"ỲÝỶỸỴàáâãèéêìíòóôõùúăđĩũơư"
        r"ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉị"
        r"ọỏốồổỗộớờởỡợụủứừửữựỳýỷỹỵ]"
    )

    # Parser annotations such as <!-- PageProvenance: physical_file=<hash> -->
    # are not document text and must not be scored.
    HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.DOTALL)

    # Punctuation / Markdown markers stripped from token edges.
    _TOKEN_EDGE = "*#_|()[]{}<>.,;:!?\"'“”‘’…–—-"
    _LETTER = r"[^\W\d_]"
    # Symbols that never occur inside a correctly recognised word.
    _SYMBOL_IN_WORD = re.compile(r"[@#$%^&*~`|\\]")
    # Digit zero inside a word (Đ0c, th0i) or letter o between/before
    # digits (2o26, o1): classic OCR 0/o confusions.
    _ZERO_IN_WORD = re.compile(r"[^\W\d_]0[^\W\d_]")
    _LETTER_O_IN_NUMBER = re.compile(r"(?:^|\d)[oO](?=\d)")

    # Markers of administrative layout blocks in Vietnamese legal documents.
    _LAYOUT_MARKERS = (
        "noi nhan",
        "cong hoa xa hoi chu nghia viet nam",
        "doc lap - tu do - hanh phuc",
        "doc lap tu do hanh phuc",
        "tm. chinh phu",
        "tm. uy ban nhan dan",
        "tm. quoc hoi",
        "kt. bo truong",
        "kt. thu tuong",
        "(chu ky, dau)",
    )
    _YEAR_HEADER = re.compile(r"^nam(?:\s|$)")
    _NUMERIC = re.compile(r"^\d+$")
    _VALID_YEAR = re.compile(r"^(?:19|20|21)\d{2}$")

    @staticmethod
    def _fold(text: Any) -> str:
        decomposed = unicodedata.normalize("NFD", str(text or ""))
        stripped = "".join(
            char for char in decomposed
            if unicodedata.category(char) != "Mn"
        )
        stripped = stripped.replace("đ", "d").replace("Đ", "D")
        return re.sub(r"\s+", " ", stripped).strip().lower()

    @classmethod
    def _table_text(cls, table: Dict[str, Any]) -> str:
        values = [str(cell.get("value") or "") for cell in table.get("cells") or []]
        return cls._fold(" ".join(values) + " " + str(table.get("markdown") or ""))

    @classmethod
    def is_layout_table(cls, table: Dict[str, Any]) -> bool:
        text = cls._table_text(table)
        return any(marker in text for marker in cls._LAYOUT_MARKERS)

    @classmethod
    def content_tables(
        cls,
        tables: Optional[List[Dict[str, Any]]],
    ) -> Optional[List[Dict[str, Any]]]:
        if tables is None:
            return None
        return [table for table in tables if not cls.is_layout_table(table)]

    @classmethod
    def _table_header(cls, table: Dict[str, Any]) -> List[str]:
        """
        Column headers of a normalized table.

        Docling's DataFrame export moves a detected header into the column
        names, so row 0 may already be data; the Markdown export keeps the
        header line. Fall back to row 0 when no Markdown is available.
        """
        columns = table.get("columns")
        markdown = str(table.get("markdown") or "").strip()
        if markdown:
            first = markdown.splitlines()[0]
            header = [cell.strip() for cell in first.strip().strip("|").split("|")]
            if isinstance(columns, int) and len(header) == columns:
                return header
        row0 = sorted(
            (cell for cell in table.get("cells") or [] if cell.get("row") == 0),
            key=lambda cell: cell.get("column", 0),
        )
        return [str(cell.get("value") or "") for cell in row0]

    @classmethod
    def _invalid_year_cells(
        cls,
        tables: Optional[List[Dict[str, Any]]],
    ) -> int:
        """
        Numeric cells in a "Năm" (year) column that are not a plausible
        four-digit year, e.g. 20552 after a broken table merged two cells.
        Non-numeric values ("Năm thứ nhất", "2025-2026") are ignored.
        """

        if not tables:
            return 0

        invalid = 0
        for table in tables:
            header = cls._table_header(table)
            header_text = {
                index: cls._fold(name) for index, name in enumerate(header)
            }
            year_columns = {
                index
                for index, name in header_text.items()
                if cls._YEAR_HEADER.match(name)
            }
            if not year_columns:
                continue
            for cell in table.get("cells") or []:
                if cell.get("column") not in year_columns:
                    continue
                for token in str(cell.get("value") or "").split():
                    token = token.strip(".,;")
                    if cls._fold(token) == header_text[cell["column"]]:
                        continue  # the header cell itself
                    if cls._NUMERIC.match(token) and not cls._VALID_YEAR.match(token):
                        invalid += 1
        return invalid

    @classmethod
    def _scorable_text(cls, content: str) -> str:
        return cls.HTML_COMMENT_PATTERN.sub(" ", content or "")

    @classmethod
    def calculate(
        cls,
        content: str,
        tables: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> Dict[str, Any]:

        # Layout blocks that parsers sometimes detect as tables (national
        # motto header, "Nơi nhận / TM. CHÍNH PHỦ" signature block) are not
        # content tables; their empty cells are intentional.
        tables = cls.content_tables(tables)

        metrics = {
            "null_ratio": cls._null_ratio(content),
            "diacritic_density": cls._diacritic_density(
                content
            ),
            "ocr_noise_ratio": cls._ocr_noise_ratio(
                content
            ),
            "corrupted_token_ratio": cls._corrupted_token_ratio(
                content
            ),
            "broken_cells_ratio": cls._broken_cells_ratio(
                content
            ),
            "empty_cells_ratio": cls._empty_cells_ratio(tables),
            "invalid_year_cells": cls._invalid_year_cells(tables),
            "missing_table_headers": (
                cls._missing_table_headers(tables)
            ),
            "merged_cell_issues": (
                cls._merged_cell_issues(tables)
                if tables is not None
                else 0
            ),
        }

        return metrics

    @staticmethod
    def _null_ratio(
        content: str
    ) -> float:

        if not content:
            return 1.0

        null_count = (
            content.count("\x00")
            + content.count("\ufffd")
        )

        return null_count / len(content)

    @classmethod
    def _diacritic_density(
        cls,
        content: str
    ) -> float:
        """
        Proxy metric for Vietnamese diacritic presence.

        Measures the proportion of alphabetic characters that
        belong to the Vietnamese character set containing
        diacritics. This metric reflects diacritic density in
        the extracted text and is not a ground-truth measure
        of OCR diacritic preservation.
        """

        letters = [
            c for c in content
            if c.isalpha()
        ]

        if not letters:
            return 0.0

        vietnamese_chars = sum(
            1
            for c in letters
            if cls.VIETNAMESE_DIACRITIC_PATTERN.match(c)
        )

        return vietnamese_chars / len(letters)

    @classmethod
    def _ocr_noise_ratio(
        cls,
        content: str
    ) -> float:

        if not content:
            return 1.0

        # Invisible format characters (zero-width space/joiner, BOM) are
        # export artefacts of word processors, not OCR noise; the ellipsis
        # is ordinary punctuation in legal templates ("- ……..;").
        noise = sum(
            1
            for char in cls.OCR_NOISE_PATTERN.findall(content)
            if char != "…" and unicodedata.category(char) != "Cf"
        )

        return noise / len(content)

    @classmethod
    def _corrupted_token_ratio(
        cls,
        content: str,
    ) -> float:
        """
        Share of word tokens that show OCR character substitution.

        A token counts as corrupted when, after stripping surrounding
        punctuation and Markdown markers, it contains a letter together
        with a symbol that never occurs inside words (C#Ủ, d@, $HỦ), a
        digit zero between letters (Đ0c), or a letter o inside / before a
        number (2o26, o1). Parser HTML comments are ignored.
        """

        tokens = cls._scorable_text(content).split()
        words = 0
        corrupted = 0

        for raw in tokens:
            token = raw.strip(cls._TOKEN_EDGE)
            if not token or not re.search(r"[^\W_]", token):
                continue
            words += 1
            has_letter = re.search(cls._LETTER, token) is not None
            if (
                (has_letter and cls._SYMBOL_IN_WORD.search(token))
                or cls._ZERO_IN_WORD.search(token)
                or cls._LETTER_O_IN_NUMBER.search(token)
            ):
                corrupted += 1

        return corrupted / words if words else 0.0

    @staticmethod
    def _empty_cells_ratio(
        tables: Optional[List[Dict[str, Any]]],
    ) -> float:
        """
        Share of empty cells in the parser's rectangular table grids.

        Tables without cells or dimensions are skipped. A cell is empty
        when its value is missing or only whitespace.
        """

        if not tables:
            return 0.0

        total = 0
        empty = 0

        for table in tables:
            cells = table.get("cells") or []
            if not cells:
                continue
            for cell in cells:
                if "value" not in cell:
                    continue
                total += 1
                value = cell.get("value")
                if value is None or not str(value).strip() or str(value).strip().lower() in {"nan", "none"}:
                    empty += 1

        return empty / total if total else 0.0

    @classmethod
    def _broken_cells_ratio(
        cls,
        content: str
    ) -> float:

        table_lines = [
            line
            for line in content.splitlines()
            if "|" in line
        ]

        if not table_lines:
            return 0.0

        rows = [
            line.strip().strip("|").split("|")
            for line in table_lines
        ]

        expected_columns = max(
            len(row)
            for row in rows
        )

        if expected_columns == 0:
            return 0.0

        broken_cells = sum(
            abs(
                len(row) - expected_columns
            )
            for row in rows
        )

        total_cells = sum(
            len(row)
            for row in rows
        )

        return broken_cells / max(
            total_cells,
            1,
        )

    @staticmethod
    def _missing_table_headers(
        tables: Optional[List[Dict[str, Any]]]
    ) -> bool:
        """
        Only validate headers when structured tables actually exist.
        """
        if not tables:
            return False

        for table in tables:
            cells = table.get("cells", [])
            if not cells:
                continue

            has_header = any(
                cell.get("row", 1) == 0
                for cell in cells
            )

            if not has_header:
                return True

        return False

    @staticmethod
    def _merged_cell_issues(
        tables: Optional[
            List[Dict[str, Any]]
        ]
    ) -> int:
        """
        Detect invalid merged-cell structures from structured
        table information.

        Expected table format:
        {
            "rows": <number of rows>,
            "columns": <number of columns>,
            "cells": [
                {
                    "row": 0,
                    "column": 0,
                    "rowspan": 1,
                    "colspan": 2
                },
                ...
            ]
        }

        A merged cell itself is valid. An issue is reported only
        when its span is invalid or exceeds the table boundary.
        """

        if not tables:
            return 0

        issues = 0

        for table in tables:

            total_rows = table.get("rows")
            total_columns = table.get("columns")

            cells = table.get(
                "cells",
                []
            )

            if (
                not isinstance(total_rows, int)
                or not isinstance(total_columns, int)
                or total_rows < 1
                or total_columns < 1
            ):
                # Cannot reliably validate structure
                # without table dimensions.
                continue

            for cell in cells:

                row = cell.get(
                    "row",
                    0
                )
                column = cell.get(
                    "column",
                    0
                )
                rowspan = cell.get(
                    "rowspan",
                    1
                )
                colspan = cell.get(
                    "colspan",
                    1
                )

                if (
                    not isinstance(row, int)
                    or not isinstance(column, int)
                    or not isinstance(rowspan, int)
                    or not isinstance(colspan, int)
                ):
                    issues += 1
                    continue

                if (
                    row < 0
                    or column < 0
                    or rowspan < 1
                    or colspan < 1
                ):
                    issues += 1
                    continue

                if (
                    row + rowspan > total_rows
                    or column + colspan > total_columns
                ):
                    issues += 1

        return issues