import io
import pytest
from pathfinder.parsers.uploads import convert, safe_name, MAX_CHARS

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

def test_safe_name_slug_and_collision():
    assert safe_name("고객 의견/2026.xlsx", set()) == "고객-의견-2026.md"
    assert safe_name("a.md", {"a.md"}) == "a-2.md"
    assert safe_name("a.md", {"a.md", "a-2.md"}) == "a-3.md"
