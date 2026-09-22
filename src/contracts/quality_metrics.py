import re
from typing import Any, Dict, List, Optional


class QualityMetricsCalculator:
    """
    Calculates extraction-quality indicators.

    The calculator does not decide PASS/FAIL.
    Threshold decisions belong to Quality Contract.
    """

    OCR_NOISE_PATTERN = re.compile(
        r"[^\w\sÀ-ỹ.,;:!?%(){}\[\]\"'“”‘’\-+/=*#|<>]"
    )

    VIETNAMESE_DIACRITIC_PATTERN = re.compile(
        r"[ÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚĂĐĨŨƠƯẠẢẤẦẨẪẬẮẰẲẴẶ"
        r"ẸẺẼẾỀỂỄỆỈỊỌỎỐỒỔỖỘỚỜỞỠỢỤỦỨỪỬỮỰ"
        r"ỲÝỶỸỴàáâãèéêìíòóôõùúăđĩũơư"
        r"ạảấầẩẫậắằẳẵặẹẻẽếềểễệỉị"
        r"ọỏốồổỗộớờởỡợụủứừửữựỳýỷỹỵ]"
    )

    @classmethod
    def calculate(
        cls,
        content: str,
        tables: Optional[
            List[Dict[str, Any]]
        ] = None,
    ) -> Dict[str, Any]:

        metrics = {
            "null_ratio": cls._null_ratio(content),
            "diacritic_ratio": cls._diacritic_ratio(
                content
            ),
            "ocr_noise_ratio": cls._ocr_noise_ratio(
                content
            ),
            "broken_cells_ratio": cls._broken_cells_ratio(
                content
            ),
            "missing_table_headers": (
                cls._missing_table_headers(content)
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
    def _diacritic_ratio(
        cls,
        content: str
    ) -> float:
        """
        Proxy metric for Vietnamese diacritic integrity.

        Measures the proportion of alphabetic characters that
        belong to the Vietnamese character set containing
        diacritics. This is not a ground-truth text preservation
        accuracy measure.
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

        noise = len(
            cls.OCR_NOISE_PATTERN.findall(content)
        )

        return noise / len(content)

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
        content: str
    ) -> bool:

        lines = content.splitlines()

        for i in range(
            len(lines) - 1
        ):
            if "|" not in lines[i]:
                continue

            if re.match(
                r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$",
                lines[i + 1],
            ):
                return False

        return any(
            "|" in line
            for line in lines
        )

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