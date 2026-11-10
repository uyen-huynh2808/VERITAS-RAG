"""
Sinh các PDF kiểm thử contract (bổ sung cho các file làm tay 111, 444-999).

Chạy từ thư mục gốc dự án:
    python Docs/Task1/tao_bo_kiem_thu.py              # tạo tất cả
    python Docs/Task1/tao_bo_kiem_thu.py 141 151      # chỉ một số văn bản

Mỗi văn bản được soạn thành .docx (python-docx) rồi xuất PDF bằng LibreOffice
(soffice) hoặc Microsoft Word (gói docx2pdf), giống văn bản soạn bằng trình
soạn thảo thật: tiêu đề Điều in đậm, có giãn đoạn, khối chữ ký đặt trong bảng.
Không vẽ PDF trực tiếp bằng PyMuPDF (với Times New Roman, cách đó ghi sai mã
dấu cách/gạch nối).

Script cũng cập nhật Docs/Task1/expected_results.json cho các file nó tạo;
các mục của file làm tay được giữ nguyên.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

OUT_DIR = Path(__file__).resolve().parent
MANIFEST = OUT_DIR / "expected_results.json"

MOTTO = ["CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", "Độc lập - Tự do - Hạnh phúc"]
SIGNATURE = (["Nơi nhận:", "- Văn phòng Chính phủ;", "- Lưu: VT, ...(6). A.XX(7)."],
             ["TM. CHÍNH PHỦ", "THỦ TƯỚNG", "(Chữ ký, dấu)"])


# ---------------------------------------------------------------------------
# Text transformations
# ---------------------------------------------------------------------------

def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    base = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", base.replace("đ", "d").replace("Đ", "D"))


_PROTECTED = re.compile(
    r"^(Số:|Hà Nội|NGHỊ ĐỊNH$|Điều \d+\.)|có hiệu lực thi hành kể từ ngày"
)


def _corrupt_word(word: str, rng: random.Random) -> str:
    letters = [i for i, c in enumerate(word) if c.isalpha()]
    if len(letters) < 2:
        return word
    op = rng.random()
    if op < 0.35:
        i = rng.choice(letters[1:])
        return word[:i] + rng.choice("#@$") + word[i + 1:]
    if op < 0.6:
        vowels = [i for i in letters[1:-1] if strip_accents(word[i]).lower() == "o"]
        if vowels:
            i = rng.choice(vowels)
            return word[:i] + "0" + word[i + 1:]
        i = rng.choice(letters[1:])
        return word[:i] + "@" + word[i + 1:]
    if op < 0.8:
        return (rng.choice("$#") + word[1:]) if rng.random() < 0.5 else (word[:-1] + "@")
    return strip_accents(word)


def ocr_noise(rate: float, seed: int = 42) -> Callable[[str], str]:
    rng = random.Random(seed)

    def transform(text: str) -> str:
        if _PROTECTED.search(text.strip()):
            return text
        return " ".join(_corrupt_word(w, rng) if rng.random() < rate else w
                        for w in text.split(" "))
    return transform


# ---------------------------------------------------------------------------
# Document model
# ---------------------------------------------------------------------------

@dataclass
class Article:
    heading: str
    paragraphs: List[str] = field(default_factory=list)
    table: Optional[List[List[str]]] = None


@dataclass
class Decree:
    file: str
    number_line: str               # "Số: 141/2025/NĐ-CP"
    place_date: str                # "Hà Nội, ngày 01 tháng 3 năm 2025"
    subject: str
    articles: List[Article]
    preamble: List[str] = field(default_factory=lambda: [
        "Căn cứ Luật Tổ chức Chính phủ ngày 19 tháng 6 năm 2015;",
        "Theo đề nghị của Bộ trưởng Bộ Tư pháp;",
    ])
    transform: Callable[[str], str] = lambda text: text
    manifest: dict = field(default_factory=dict)


def build_docx(decree: Decree, target: Path) -> None:
    t = decree.transform
    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(13)

    def paragraph(text, bold=False, italic=False, center=False, before=6):
        p = document.add_paragraph()
        p.paragraph_format.space_before = Pt(before)
        p.paragraph_format.space_after = Pt(0)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.JUSTIFY
        run = p.add_run(t(text))
        run.bold, run.italic = bold, italic
        return p

    def two_columns(left, right, bold_first=True):
        table = document.add_table(rows=1, cols=2)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        for cell, lines in zip(table.rows[0].cells, (left, right)):
            for index, line in enumerate(lines):
                p = cell.paragraphs[0] if index == 0 else cell.add_paragraph()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = p.add_run(t(line))
                run.bold = bold_first and index == 0

    two_columns(["CHÍNH PHỦ", "________"], MOTTO + ["_____________________________"])
    p = document.add_paragraph()
    p.add_run(t(decree.number_line))
    p.add_run("\t\t\t")
    p.add_run(t(decree.place_date)).italic = True

    paragraph("NGHỊ ĐỊNH", bold=True, center=True, before=12)
    paragraph(decree.subject, bold=True, center=True, before=0)
    paragraph("CHÍNH PHỦ", bold=True, center=True, before=12)
    for line in decree.preamble:
        paragraph(line)
    paragraph("Chính phủ ban hành Nghị định " + decree.subject[0].lower()
              + decree.subject[1:] + ".")

    for article in decree.articles:
        paragraph(article.heading, bold=True, before=10)
        for text in article.paragraphs:
            paragraph(text, before=4)
        if article.table:
            rows = article.table
            table = document.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Table Grid"
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    cell = table.cell(r, c)
                    cell.text = t(value)
                    if r == 0:
                        for run in cell.paragraphs[0].runs:
                            run.bold = True
    document.add_paragraph()
    two_columns(*SIGNATURE)
    document.save(str(target))


def find_soffice() -> Optional[str]:
    for candidate in (shutil.which("soffice"), shutil.which("libreoffice"),
                      r"C:\Program Files\LibreOffice\program\soffice.exe",
                      r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def to_pdf(docx_path: Path, pdf_path: Path) -> str:
    soffice = find_soffice()
    if soffice:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([soffice, "--headless", "--convert-to", "pdf",
                            "--outdir", tmp, str(docx_path)],
                           check=True, capture_output=True)
            shutil.move(str(Path(tmp) / (docx_path.stem + ".pdf")), str(pdf_path))
        return "LibreOffice"
    try:
        from docx2pdf import convert
    except ImportError:
        return ""
    convert(str(docx_path), str(pdf_path))
    return "Microsoft Word"


# ---------------------------------------------------------------------------
# Test documents
# ---------------------------------------------------------------------------

def standard_articles(effective_clause: str, extra: Optional[List[Article]] = None,
                      scope: str = "kiểm thử hợp đồng dữ liệu của hệ thống VERITAS-RAG"):
    articles = [
        Article("Điều 1. Phạm vi điều chỉnh",
                [f"Nghị định này là tài liệu giả lập được sử dụng để {scope}. "
                 "Nội dung không mang giá trị pháp lý thực tế."]),
        Article("Điều 2. Đối tượng áp dụng",
                ["Nghị định này áp dụng đối với cơ quan, tổ chức và cá nhân tham gia "
                 "xây dựng, quản lý và khai thác kho dữ liệu văn bản quy phạm pháp luật."]),
    ]
    articles += extra or []
    number = len(articles) + 1
    articles.append(Article(f"Điều {number}. Hiệu lực thi hành", [effective_clause]))
    articles.append(Article(
        f"Điều {number + 1}. Trách nhiệm thi hành",
        ["Các Bộ trưởng, Thủ trưởng cơ quan ngang bộ, Thủ trưởng cơ quan thuộc "
         "Chính phủ chịu trách nhiệm thi hành Nghị định này."]))
    return articles


def case(file, mutation_type, variant, target_layer, expected, stage, codes, description):
    return {
        "file": file, "mutation_type": mutation_type, "variant": variant,
        "target_layer": target_layer, "expected": expected,
        "expected_stage": stage, "expected_codes": codes,
        "description": description,
    }


def decrees() -> List[Decree]:
    docs: List[Decree] = []

    # --- Quality: whole document lost its Vietnamese diacritics ------------
    docs.append(Decree(
        file="1212026NĐ-CP.pdf",
        number_line="Số: 121/2026/NĐ-CP",
        place_date="Hà Nội, ngày 15 tháng 1 năm 2026",
        subject="Về việc quy định nội dung giả lập văn bản mất dấu tiếng Việt",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 01 tháng 3 năm 2026."),
        transform=strip_accents,
        manifest=case(
            "1212026NĐ-CP.pdf", "ocr_corruption", "diacritics_lost", "quality",
            "QUARANTINE", "silver", ["DIACRITIC_DENSITY_LOW"],
            "Toàn bộ văn bản mất dấu tiếng Việt (kể cả metadata). Parser vẫn trích "
            "được số hiệu và ngày vì so khớp dạng không dấu; lớp Quality phải chặn "
            "ở Silver vì mật độ ký tự có dấu dưới ngưỡng 0,10."),
    ))

    # --- Schema: malformed document number (missing "-CP") ----------------
    docs.append(Decree(
        file="1312026NĐ-CP.pdf",
        number_line="Số: 131/2026/NĐ",
        place_date="Hà Nội, ngày 20 tháng 1 năm 2026",
        subject="Về việc quy định nội dung giả lập văn bản sai số hiệu",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 15 tháng 3 năm 2026."),
        manifest=case(
            "1312026NĐ-CP.pdf", "metadata_corruption", "invalid_doc_id", "schema",
            "QUARANTINE", "pre_parse", ["SCHEMA_VIOLATION"],
            "Số hiệu thiếu hậu tố '-CP' (131/2026/NĐ) nên không khớp mẫu doc_id của "
            "Schema; phải bị chặn ở pre_parse, trước Bronze."),
    ))

    # --- Revoked law: 141 replaced by 151 but still open-ended -------------
    docs.append(Decree(
        file="1412025NĐ-CP.pdf",
        number_line="Số: 141/2025/NĐ-CP",
        place_date="Hà Nội, ngày 01 tháng 3 năm 2025",
        subject="Về việc quy định nội dung giả lập văn bản bị thay thế",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 15 tháng 4 năm 2025."),
        manifest=case(
            "1412025NĐ-CP.pdf", "revoked_law", "replaced_but_open_ended", "temporal",
            "QUARANTINE", "silver", ["REVOKED_TARGET_STILL_EFFECTIVE"],
            "Bị Nghị định 151/2026/NĐ-CP thay thế từ 01/04/2026 nhưng không ghi thời "
            "điểm hết hiệu lực (effective_to trống): văn bản đã bị thay thế vẫn được "
            "coi là còn hiệu lực. Chỉ phát hiện được khi tra chéo với 151 trong cùng "
            "thư mục."),
    ))
    docs.append(Decree(
        file="1512026NĐ-CP.pdf",
        number_line="Số: 151/2026/NĐ-CP",
        place_date="Hà Nội, ngày 01 tháng 2 năm 2026",
        subject="Về việc quy định nội dung giả lập văn bản thay thế",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 01 tháng 4 năm 2026. "
            "Nghị định này thay thế Nghị định số 141/2025/NĐ-CP ngày 01 tháng 3 năm "
            "2025 của Chính phủ."),
        manifest=case(
            "1512026NĐ-CP.pdf", "clean", "valid_replacing_document", "none",
            "PASS", None, [],
            "Văn bản thay thế hợp lệ (ban hành và có hiệu lực sau 141). Lỗi nằm ở "
            "141, không phải ở văn bản này: kiểm tra rule không gắn nhầm lỗi cho "
            "văn bản nguồn."),
    ))

    # --- Valid replacement pair: 161 ends exactly when 171 starts ---------
    docs.append(Decree(
        file="1612025NĐ-CP.pdf",
        number_line="Số: 161/2025/NĐ-CP",
        place_date="Hà Nội, ngày 10 tháng 5 năm 2025",
        subject="Về việc quy định nội dung giả lập văn bản có thời hạn",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 01 tháng 7 năm 2025 đến "
            "hết ngày 31 tháng 3 năm 2026."),
        manifest=case(
            "1612025NĐ-CP.pdf", "clean", "valid_time_limited_document", "none",
            "PASS", None, [],
            "Có hiệu lực từ 01/07/2025 đến hết 31/03/2026 (effective_to = "
            "2026-04-01), đúng ngày 171 thay thế nó có hiệu lực: không được báo "
            "REVOKED_TARGET_STILL_EFFECTIVE. Kiểm tra trích effective_to dạng "
            "'đến hết ngày'."),
    ))
    docs.append(Decree(
        file="1712026NĐ-CP.pdf",
        number_line="Số: 171/2026/NĐ-CP",
        place_date="Hà Nội, ngày 15 tháng 2 năm 2026",
        subject="Về việc quy định nội dung giả lập văn bản thay thế hợp lệ",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 01 tháng 4 năm 2026. "
            "Nghị định này thay thế Nghị định số 161/2025/NĐ-CP ngày 10 tháng 5 năm "
            "2025 của Chính phủ."),
        manifest=case(
            "1712026NĐ-CP.pdf", "clean", "valid_replacing_document", "none",
            "PASS", None, [],
            "Thay thế 161 đúng lúc 161 hết hiệu lực: cặp quan hệ hợp lệ."),
    ))

    # --- Amendment that takes effect before the amended decree ------------
    docs.append(Decree(
        file="1812025NĐ-CP.pdf",
        number_line="Số: 181/2025/NĐ-CP",
        place_date="Hà Nội, ngày 20 tháng 3 năm 2025",
        subject="Về việc sửa đổi, bổ sung một số điều của Nghị định số 141/2025/NĐ-CP",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 01 tháng 4 năm 2025.",
            extra=[Article(
                "Điều 3. Sửa đổi, bổ sung Nghị định số 141/2025/NĐ-CP",
                ["Nghị định này sửa đổi, bổ sung một số điều của Nghị định số "
                 "141/2025/NĐ-CP ngày 01 tháng 3 năm 2025 của Chính phủ."])]),
        manifest=case(
            "1812025NĐ-CP.pdf", "revoked_law", "amendment_effective_before_target",
            "temporal", "QUARANTINE", "silver", ["RELATION_EFFECTIVE_ORDER_INVALID"],
            "Sửa đổi 141 và ban hành sau 141 (20/03 > 01/03/2025) nhưng có hiệu lực "
            "01/04/2025, trước khi chính 141 có hiệu lực (15/04/2025)."),
    ))

    # --- Effective-to before effective-from --------------------------------
    docs.append(Decree(
        file="1912026NĐ-CP.pdf",
        number_line="Số: 191/2026/NĐ-CP",
        place_date="Hà Nội, ngày 01 tháng 5 năm 2026",
        subject="Về việc quy định nội dung giả lập văn bản sai thời hạn hiệu lực",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 01 tháng 6 năm 2026 đến "
            "hết ngày 30 tháng 4 năm 2026."),
        manifest=case(
            "1912026NĐ-CP.pdf", "wrong_date", "end_before_start", "temporal",
            "QUARANTINE", "silver", ["INVALID_END_DATE"],
            "Có hiệu lực từ 01/06/2026 'đến hết ngày 30/04/2026': thời điểm hết "
            "hiệu lực (2026-05-01) đứng trước thời điểm có hiệu lực."),
    ))

    # --- Clean: valid table + self-expiry sentence -------------------------
    docs.append(Decree(
        file="2112026NĐ-CP.pdf",
        number_line="Số: 211/2026/NĐ-CP",
        place_date="Hà Nội, ngày 05 tháng 3 năm 2026",
        subject="Về việc quy định nội dung giả lập văn bản có bảng biểu",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 01 tháng 5 năm 2026. "
            "Nghị định này hết hiệu lực kể từ ngày 01 tháng 01 năm 2028.",
            extra=[Article(
                "Điều 3. Lộ trình áp dụng",
                ["Lộ trình áp dụng được quy định tại bảng sau:"],
                table=[["STT", "Nội dung", "Năm áp dụng"],
                       ["1", "Chuẩn hóa dữ liệu văn bản", "2026"],
                       ["2", "Kiểm tra hợp đồng dữ liệu", "2026"],
                       ["3", "Truy vết nguồn gốc câu trả lời", "2027"]])]),
        manifest=case(
            "2112026NĐ-CP.pdf", "clean", "valid_table_and_expiry", "none",
            "PASS", None, [],
            "Bảng hợp lệ (cột 'Năm áp dụng' toàn năm 4 chữ số, không ô trống) và câu "
            "'Nghị định này hết hiệu lực kể từ ngày 01/01/2028': các rule bảng không "
            "được báo nhầm, effective_to phải trích được = 2028-01-01."),
    ))

    # --- Clean amending decree quoting a whole article ---------------------
    docs.append(Decree(
        file="2312026NĐ-CP.pdf",
        number_line="Số: 231/2026/NĐ-CP",
        place_date="Hà Nội, ngày 01 tháng 8 năm 2026",
        subject="Về việc sửa đổi, bổ sung một số điều của Nghị định số 111/2026/NĐ-CP",
        articles=[
            Article("Điều 1. Sửa đổi, bổ sung một số điều của Nghị định số 111/2026/NĐ-CP",
                    ["Sửa đổi, bổ sung một số điều của Nghị định số 111/2026/NĐ-CP ngày "
                     "01 tháng 6 năm 2026 của Chính phủ như sau:",
                     "1. Sửa đổi Điều 2 như sau:",
                     "“Điều 2. Đối tượng áp dụng",
                     "Nghị định này áp dụng đối với cơ quan, tổ chức, cá nhân có liên "
                     "quan đến việc kiểm thử hệ thống dữ liệu pháp lý.”"]),
            Article("Điều 2. Hiệu lực thi hành",
                    ["Nghị định này có hiệu lực thi hành kể từ ngày 15 tháng 9 năm 2026."]),
            Article("Điều 3. Trách nhiệm thi hành",
                    ["Các Bộ trưởng, Thủ trưởng cơ quan ngang bộ chịu trách nhiệm thi "
                     "hành Nghị định này."]),
        ],
        manifest=case(
            "2312026NĐ-CP.pdf", "clean", "valid_amending_document", "none",
            "PASS", None, [],
            "Sửa đổi 111 (ban hành và có hiệu lực sau 111) và trích nguyên văn "
            "“Điều 2. …” trong ngoặc kép: không được báo trùng tiêu đề Điều, không "
            "được báo sai thứ tự quan hệ."),
    ))

    # --- Clean multi-page decree with clauses and points -------------------
    long_articles = []
    topics = ["Nguyên tắc quản lý dữ liệu", "Chuẩn hóa văn bản", "Kiểm tra chất lượng",
              "Truy vết nguồn gốc", "Lưu trữ và sao lưu", "Báo cáo định kỳ"]
    for index, topic in enumerate(topics, start=1):
        paragraphs = []
        for clause in range(1, 4):
            paragraphs.append(
                f"{clause}. Cơ quan quản lý có trách nhiệm tổ chức thực hiện nội dung "
                f"{topic.lower()} theo kế hoạch hằng năm, bảo đảm tính đầy đủ, chính "
                "xác và kịp thời của thông tin.")
            if clause == 2:
                for point in "abc":
                    paragraphs.append(
                        f"{point}) Xây dựng quy trình nghiệp vụ, phân công đầu mối và "
                        "bố trí nguồn lực phù hợp với điều kiện thực tế của đơn vị;")
        long_articles.append(Article(f"Điều {index + 2}. {topic}", paragraphs))
    docs.append(Decree(
        file="2412026NĐ-CP.pdf",
        number_line="Số: 241/2026/NĐ-CP",
        place_date="Hà Nội, ngày 10 tháng 4 năm 2026",
        subject="Về việc quy định nội dung giả lập văn bản nhiều trang",
        articles=standard_articles(
            "Nghị định này có hiệu lực thi hành kể từ ngày 01 tháng 7 năm 2026.",
            extra=long_articles),
        manifest=case(
            "2412026NĐ-CP.pdf", "clean", "valid_multi_page_document", "none",
            "PASS", None, [],
            "Văn bản sạch nhiều trang, có Khoản và Điểm: kiểm tra toàn bộ pipeline "
            "không báo nhầm trên văn bản dài."),
    ))

    # --- OCR noise with metadata preserved (sensitivity pair) --------------
    for number, rate in (("222", 0.01), ("333", 0.15)):
        docs.append(Decree(
            file=f"{number}2026NĐ-CP.pdf",
            number_line=f"Số: {number}/2026/NĐ-CP",
            place_date="Hà Nội, ngày 10 tháng 3 năm 2026",
            subject="Về việc quy định một số nội dung giả lập văn bản nhiễu nhận dạng ký tự",
            articles=standard_articles(
                "Nghị định này có hiệu lực thi hành kể từ ngày 01 tháng 5 năm 2026.",
                scope="kiểm thử khả năng phát hiện lỗi nhận dạng ký tự quang học của "
                      "lớp kiểm tra chất lượng dữ liệu trong hệ thống VERITAS-RAG"),
            transform=ocr_noise(rate),
            manifest=dict(
                case(f"{number}2026NĐ-CP.pdf", "ocr_corruption", "metadata_preserved",
                     "quality", "QUARANTINE", "silver",
                     ["OCR_TOKEN_CORRUPTION", "OCR_NOISE_EXCEEDED", "DIACRITIC_DENSITY_LOW"],
                     f"Nhiễu OCR {rate:.0%} số từ nhưng giữ nguyên số hiệu, ngày ban "
                     "hành, tiêu đề Điều và câu hiệu lực: Schema phải PASS, lớp Quality "
                     "ở Silver tự chặn."
                     + ("" if rate > 0.02 else
                        " Văn bản này DƯỚI ngưỡng max_corrupted_token_ratio: probe đo "
                        "độ nhạy, không tính vào Detection Rate.")),
                severity=f"rate_{rate:g}",
                in_scope=rate > 0.02,
            ),
        ))
    return docs


def main() -> None:
    wanted = set(sys.argv[1:])
    selected = [d for d in decrees()
                if not wanted or any(d.file.startswith(w) for w in wanted)]
    if not selected:
        raise SystemExit("Không có văn bản nào khớp tham số.")

    with tempfile.TemporaryDirectory() as tmp:
        for decree in selected:
            docx_path = Path(tmp) / (Path(decree.file).stem + ".docx")
            build_docx(decree, docx_path)
            pdf_path = OUT_DIR / decree.file
            tool = to_pdf(docx_path, pdf_path)
            if not tool:
                keep = OUT_DIR / docx_path.name
                shutil.copyfile(docx_path, keep)
                print(f"Không có LibreOffice/Word: đã lưu {keep}, hãy xuất PDF thủ công "
                      f"với tên {decree.file}.")
                continue
            print(f"Đã tạo {pdf_path.name} ({tool})")

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    generated = {d.file: d.manifest for d in selected}
    data["cases"] = [c for c in data["cases"] if c["file"] not in generated]
    data["cases"].extend(generated.values())
    data["cases"].sort(key=lambda c: c["file"])
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(f"Đã cập nhật {MANIFEST.name}: {len(data['cases'])} mục")


if __name__ == "__main__":
    main()
