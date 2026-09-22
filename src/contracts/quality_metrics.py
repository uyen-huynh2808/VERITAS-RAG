import re
from dataclasses import dataclass


@dataclass
class QualityMetrics:
    null_ratio: float
    diacritic_ratio: float
    ocr_noise_ratio: float
    broken_cells_ratio: float
    missing_table_headers: bool


class QualityMetricsCalculator:
    """
    Computes quality metrics from parsed Markdown/text.

    This class does NOT decide whether the document passes
    the Quality Contract.
    """

    OCR_NOISE_PATTERN = re.compile(
        r"(?:�|�{2,}|[^\w\s]{4,})"
    )

    DIACRITIC_PATTERN = re.compile(
        r"[àáạảãâầấậẩẫăằắặẳẵ"
        r"èéẹẻẽêềếệểễ"
        r"ìíịỉĩ"
        r"òóọỏõôồốộổỗơờớợởỡ"
        r"ùúụủũưừứựửữ"
        r"ỳýỵỷỹđ]",
        re.IGNORECASE,
    )

    def calculate(self, content: str) -> QualityMetrics:

        if not content:
            return QualityMetrics(
                null_ratio=1.0,
                diacritic_ratio=0.0,
                ocr_noise_ratio=1.0,
                broken_cells_ratio=1.0,
                missing_table_headers=True,
            )

        return QualityMetrics(
            null_ratio=self._null_ratio(content),
            diacritic_ratio=self._diacritic_ratio(content),
            ocr_noise_ratio=self._ocr_noise_ratio(content),
            broken_cells_ratio=self._broken_cells_ratio(content),
            missing_table_headers=self._missing_table_headers(content),
        )

    @staticmethod
    def _null_ratio(content: str) -> float:
        corrupted = content.count("\x00") + content.count("\ufffd")
        return corrupted / max(len(content), 1)

    def _diacritic_ratio(self, content: str) -> float:
        letters = [c for c in content if c.isalpha()]

        if not letters:
            return 0.0

        diacritics = len(self.DIACRITIC_PATTERN.findall(content))

        return diacritics / len(letters)

    def _ocr_noise_ratio(self, content: str) -> float:
        noise = len(self.OCR_NOISE_PATTERN.findall(content))
        return noise / max(len(content), 1)

    @staticmethod
    def _table_lines(content: str):
        return [
            line.strip()
            for line in content.splitlines()
            if "|" in line
        ]

    def _missing_table_headers(self, content: str) -> bool:
        lines = self._table_lines(content)

        if not lines:
            return False

        for i in range(len(lines) - 1):
            separator = lines[i + 1]

            if re.search(r"\|?\s*:?-{3,}:?\s*(\||$)", separator):
                return False

        return True

    def _broken_cells_ratio(self, content: str) -> float:
        lines = self._table_lines(content)

        if not lines:
            return 0.0

        expected_cells = None
        broken = 0
        total = 0

        for line in lines:
            cells = [
                cell.strip()
                for cell in line.strip("|").split("|")
            ]

            if expected_cells is None:
                expected_cells = len(cells)

            total += expected_cells

            if len(cells) != expected_cells:
                broken += abs(len(cells) - expected_cells)

        return broken / max(total, 1)