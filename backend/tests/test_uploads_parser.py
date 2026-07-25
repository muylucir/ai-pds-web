import io
import re

import pytest
from pathfinder.parsers.uploads import convert, upload_key, MAX_CHARS

_KEY_RE = re.compile(r"^uploads/[0-9a-f]{8}/(.+)$")

def _xlsx_bytes():
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "의견"
    ws.append(["이름", "의견"])
    ws.append(["김PM", "너무 느려요"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def test_md_passthrough():
    content, truncated = convert("노트.md", "# 제목\n내용".encode("utf-8"))
    assert content == "# 제목\n내용" and truncated is False

def test_xlsx_becomes_markdown_table():
    content, _ = convert("survey.xlsx", _xlsx_bytes())
    assert "## 의견" in content            # 시트명 헤더
    assert "| 이름 | 의견 |" in content
    assert "| 김PM | 너무 느려요 |" in content

def test_truncation_marks_and_cuts():
    content, truncated = convert("big.txt", ("가" * (MAX_CHARS + 100)).encode("utf-8"))
    assert truncated is True
    assert content.endswith("[... 50,000자 초과분 생략]")
    assert len(content) <= MAX_CHARS + 30   # 마커 길이 여유

def test_unsupported_extension_raises():
    with pytest.raises(ValueError):
        convert("virus.exe", b"MZ")

def test_corrupt_xlsx_raises_value_error_not_bad_zip_file():
    # A valid extension with bytes that aren't a real xlsx (zip) must degrade
    # to ValueError (route maps this to 415), not leak openpyxl's
    # zipfile.BadZipFile as an uncaught 500.
    with pytest.raises(ValueError):
        convert("bad.xlsx", b"not a real xlsx")

def test_corrupt_pdf_raises_value_error_not_pdf_stream_error():
    # Same guarantee for pypdf.errors.PdfStreamError on garbage bytes.
    with pytest.raises(ValueError):
        convert("bad.pdf", b"not a real pdf")

def test_upload_key_preserves_the_original_name_and_extension():
    key = upload_key("요구사항.pdf")
    m = _KEY_RE.match(key)
    assert m, key
    assert m.group(1) == "요구사항.pdf.md"


def test_same_name_different_extension_stays_distinguishable():
    """The old safe_name() forced every upload to .md, so 요구사항.pdf and
    요구사항.xlsx collided into 요구사항.md / 요구사항-2.md with no way to tell
    which was which."""
    pdf = upload_key("요구사항.pdf")
    xlsx = upload_key("요구사항.xlsx")
    assert pdf.endswith("요구사항.pdf.md")
    assert xlsx.endswith("요구사항.xlsx.md")


def test_identical_uploads_get_distinct_keys():
    assert upload_key("a.md") != upload_key("a.md")


def test_upload_key_strips_path_and_control_characters():
    key = upload_key("../../etc/pa sswd.txt")
    assert ".." not in key
    m = _KEY_RE.match(key)
    assert m and "/" not in m.group(1)


def test_upload_key_handles_a_nameless_file():
    m = _KEY_RE.match(upload_key(""))
    assert m and m.group(1) == "upload.md"
