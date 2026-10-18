# backend/aipds/parsers/uploads.py
"""Uploaded file → files to place in the workspace's `uploads/`.

There are two branches.

- **Convert to text.** Documents, spreadsheets, slide decks, web pages, and transcripts (md/txt/csv, xlsx,
  docx, pptx, hwpx, html, vtt/srt, pdf) are converted to markdown at upload time.
  The agent cannot open binary formats directly, and even if it could, there is no reason to re-parse
  them every turn. The conversion result is truncated at the same 50,000-character limit as the rules' URL mode (spec §6).
- **Keep the original as is.** Images and PDFs. The agent's (Claude Code CLI) `Read` views both
  directly — screen captures and whiteboard photos have nothing to convert to text, and PDF charts and
  scanned pages are lost in text extraction. PDFs also keep a converted copy: text is enough for
  most, and reading the original costs far more tokens per page.

The content is untrusted input — it is only stored, never executed.
"""
from __future__ import annotations
import html
import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

#: Cap on the original size. The two front layers the request passes through are aligned to this value too — nginx's
#: `client_max_body_size` (infra/lib/user-data.ts, infra/scripts/aipds-update) and the Next
#: middleware's `middlewareClientMaxBodySize` (frontend/next.config.mjs). Next does not reject an oversized
#: body but **truncates** it and passes it on, so raising only this value makes large files arrive corrupted.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_CHARS = 50_000
_TRUNC_MARK = "\n[... 50,000자 초과분 생략]"

#: Cap on the decompressed size. docx, pptx, xlsx, and hwpx are zips, so files where a 10MB archive
#: expands to several GB (zip bombs) are filtered before reaching the parser. Python's zipfile extracts
#: entries only up to their declared size, so the sum of declared sizes is sufficient.
_MAX_UNZIPPED_BYTES = 100 * 1024 * 1024
_ZIP_EXTS = frozenset({".xlsx", ".docx", ".pptx", ".hwpx"})

_TEXT_EXTS = frozenset({".md", ".txt", ".csv"})

#: Extension → format determined by content. A JPEG saved as `.png` (common for images from the web)
#: gets the original key's extension adjusted to match its content — if extension and content differ,
#: the reader may send it as the wrong format.
_IMAGE_KINDS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg",
                ".gif": "gif", ".webp": "webp"}
_KIND_EXT = {"png": ".png", "jpeg": ".jpg", "gif": ".gif", "webp": ".webp"}
IMAGE_EXTS = frozenset(_IMAGE_KINDS)


def _ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def _cell(text: str) -> str:
    # Line breaks and pipes inside a cell break the markdown table.
    return " ".join(text.split()).replace("|", "\\|")


def _md_table(rows: list[list[str]]) -> str:
    width = max(len(row) for row in rows)
    rows = [[_cell(c) for c in row] + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def _xlsx_to_markdown(data: bytes) -> str:
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        rows = [[("" if c is None else str(c)) for c in row]
                for row in ws.iter_rows(values_only=True)]
        if not rows:
            continue
        parts.append(f"## {ws.title}")
        parts.append(_md_table(rows))
    return "\n".join(parts)


def _pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def _docx_heading_level(style_name: str) -> int:
    # Built-in style names are stored in English regardless of Word's display language ("heading 1").
    if style_name == "Title":
        return 1
    m = re.fullmatch(r"Heading (\d)", style_name)
    return min(int(m.group(1)), 6) if m else 0


def _docx_to_markdown(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    doc = Document(io.BytesIO(data))
    blocks: list[str] = []
    # In body order — paragraphs and tables are interleaved. Headers and footers are decoration
    # repeated on every page, so they are not read.
    for block in doc.iter_inner_content():
        if isinstance(block, Table):
            rows = [[cell.text for cell in row.cells] for row in block.rows]
            if rows:
                blocks.append(_md_table(rows))
            continue
        text = block.text.strip()
        if not text:
            continue
        style = block.style.name if block.style is not None else ""
        level = _docx_heading_level(style or "")
        p_pr = block._p.pPr
        if level:
            blocks.append("#" * level + " " + text)
        elif (p_pr is not None and p_pr.numPr is not None) or (style or "").startswith("List"):
            blocks.append("- " + text)
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def _pptx_shapes(shapes):
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _pptx_shapes(shape.shapes)
        else:
            yield shape


def _pptx_chart(chart) -> str:
    title = (chart.chart_title.text_frame.text.strip()
             if chart.has_title and chart.chart_title.has_text_frame else "")
    parts = [f"Chart: {title}" if title else "Chart"]
    for plot in chart.plots:
        categories = [str(c) for c in plot.categories]
        rows = [["", *categories]]
        rows += [[series.name or "", *("" if v is None else str(v) for v in series.values)]
                 for series in plot.series]
        if len(rows) > 1:
            parts.append(_md_table(rows))
    return "\n".join(parts)


def _pptx_to_markdown(data: bytes) -> str:
    from pptx import Presentation
    prs = Presentation(io.BytesIO(data))
    blocks: list[str] = []
    for number, slide in enumerate(prs.slides, 1):
        title_shape = slide.shapes.title
        title = " ".join(title_shape.text_frame.text.split()) if title_shape is not None else ""
        blocks.append(f"## Slide {number}" + (f": {title}" if title else ""))
        for shape in _pptx_shapes(slide.shapes):
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            if shape.has_text_frame:
                lines = ["  " * p.level + p.text.strip()
                         for p in shape.text_frame.paragraphs if p.text.strip()]
                if lines:
                    blocks.append("\n".join(lines))
            elif shape.has_table:
                blocks.append(_md_table(
                    [[cell.text for cell in row.cells] for row in shape.table.rows]))
            elif shape.has_chart:
                blocks.append(_pptx_chart(shape.chart))
        # Speaker notes often carry context that is not on the slide.
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                blocks.append(f"Notes: {notes}")
    return "\n\n".join(blocks)


#: Inline signals of text not visible on screen. Hidden text is a common channel for prompt
#: injection, so it is removed — text hidden by classes in external stylesheets is not caught (a known
#: risk of the same grade as URL mode).
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden"
    r"|font-size\s*:\s*0(?![.\d])|opacity\s*:\s*0(?![.\d])", re.I)
_HTML_DROPPED = ["head", "script", "style", "noscript", "template", "iframe",
                 "object", "embed", "svg", "canvas"]


def _html_hidden(tag) -> bool:
    return (tag.has_attr("hidden")
            or tag.get("aria-hidden") == "true"
            or (tag.name == "input" and (tag.get("type") or "").lower() == "hidden")
            or bool(_HIDDEN_STYLE.search(tag.get("style") or "")))


def _html_to_markdown(data: bytes) -> str:
    from bs4 import BeautifulSoup, Comment
    from markdownify import MarkdownConverter

    class _Converter(MarkdownConverter):
        def convert_img(self, el, text, parent_tags):
            # src is dropped — a single data: URI is hundreds of thousands of characters. Only the alt text is kept.
            alt = " ".join((el.get("alt") or "").split())
            return f"[image: {alt}]" if alt else ""

        def convert_a(self, el, text, parent_tags):
            href = (el.get("href") or "").strip().lower()
            if not href.startswith(("http://", "https://", "mailto:")):
                return text           # javascript:, data:, in-document anchors
            return super().convert_a(el, text, parent_tags)

    # Pass the bytes as is — it reads <meta charset> to pick encodings like EUC-KR.
    soup = BeautifulSoup(data, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in soup.find_all(_HTML_DROPPED) + soup.find_all(_html_hidden):
        if not tag.decomposed:
            tag.decompose()
    body = _Converter(heading_style="ATX", bullets="-").convert_soup(soup)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return f"# {title}\n\n{body}" if title else body


_CUE_TIME = re.compile(r"^\s*((?:\d+:)?\d{1,2}:\d{2})[.,]\d{1,3}\s*-->")
_VOICE = re.compile(r"<v(?:\.[^\s>]*)?\s+([^>]+)>")
_TAG = re.compile(r"<[^>]+>")
#: Subtitles without speaker labels break paragraphs at this interval — so an hour-long transcript
#: does not become a single paragraph with one timestamp.
_UNNAMED_PARAGRAPH_SECONDS = 60


def _seconds(clock: str) -> int:
    total = 0
    for part in clock.split(":"):
        total = total * 60 + int(part)
    return total


def _transcript_to_text(data: bytes) -> str:
    """WebVTT/SRT subtitles → `[HH:MM:SS] speaker: utterance` paragraphs.

    Drops number and timing lines and the WEBVTT header and NOTE blocks, and merges consecutive cues
    from the same speaker into one paragraph. Teams VTT tags the speaker on each cue with `<v name>`. Nearly
    half of the raw text is timing, so this fits close to twice as much within the same limit.
    """
    text = data.decode("utf-8-sig", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    turns: list[tuple[int, str | None, list[str]]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").split("\n")
        at = next((i for i, line in enumerate(lines) if _CUE_TIME.match(line)), None)
        if at is None:
            continue        # WEBVTT header, NOTE/STYLE blocks
        start = _seconds(_CUE_TIME.match(lines[at]).group(1))
        raw = " ".join(line.strip() for line in lines[at + 1:] if line.strip())
        voice = _VOICE.search(raw)
        speaker = voice.group(1).strip() if voice else None
        said = " ".join(html.unescape(_TAG.sub("", raw)).split())
        if not said:
            continue
        if turns and turns[-1][1] == speaker and (
                speaker is not None
                or start - turns[-1][0] < _UNNAMED_PARAGRAPH_SECONDS):
            turns[-1][2].append(said)
        else:
            turns.append((start, speaker, [said]))
    paragraphs = []
    for start, speaker, words in turns:
        clock = f"{start // 3600:02d}:{start // 60 % 60:02d}:{start % 60:02d}"
        who = f"{speaker}: " if speaker else ""
        paragraphs.append(f"[{clock}] {who}{' '.join(words)}")
    return "\n\n".join(paragraphs)


def _local(el) -> str:
    # For comments and processing instructions, tag is not a string.
    return el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""


def _hwpx_text(t) -> str:
    parts = [t.text or ""]
    for child in t:
        parts.append("\n" if _local(child) == "lineBreak" else " ")
        parts.append(child.tail or "")
    return "".join(parts)


def _hwpx_table(tbl) -> str:
    cells: dict[tuple[int, int], str] = {}
    for r, tr in enumerate(c for c in tbl if _local(c) == "tr"):
        for c, tc in enumerate(x for x in tr if _local(x) == "tc"):
            # With merged cells, the sequence number and actual position diverge — cellAddr is the source of truth.
            addr = next((x for x in tc if _local(x) == "cellAddr"), None)
            if addr is not None:
                r_at, c_at = int(addr.get("rowAddr", r)), int(addr.get("colAddr", c))
            else:
                r_at, c_at = r, c
            cells[(r_at, c_at)] = " ".join(
                " ".join(_hwpx_blocks(sub)) for sub in tc if _local(sub) == "subList")
    if not cells:
        return ""
    n_rows = max(r for r, _ in cells) + 1
    # Slots created by horizontal merges are empty in every row — such columns are not included.
    cols = sorted({c for (_, c), text in cells.items() if text.strip()}) or [0]
    return _md_table([[cells.get((r, c), "") for c in cols]
                      for r in range(n_rows)])


#: Controls that are not read. Headers and footers are decoration repeated on every page.
_HWPX_SKIPPED = frozenset({"header", "footer"})


def _hwpx_blocks(container) -> list[str]:
    """Paragraphs (`p`) into blocks. Tables and text boxes (`subList`) become separate blocks between paragraphs."""
    blocks: list[str] = []
    for p in container:
        if _local(p) != "p":
            continue
        buf: list[str] = []

        def flush() -> None:
            text = "".join(buf).strip()
            if text:
                blocks.append(text)
            buf.clear()

        def walk(el) -> None:
            for child in el:
                name = _local(child)
                if name == "t":
                    buf.append(_hwpx_text(child))
                elif name == "tbl":
                    flush()
                    table = _hwpx_table(child)
                    if table:
                        blocks.append(table)
                elif name == "subList":
                    flush()
                    blocks.extend(_hwpx_blocks(child))
                elif name not in _HWPX_SKIPPED:
                    walk(child)

        walk(p)
        flush()
    return blocks


def _hwpx_to_markdown(data: bytes) -> str:
    from lxml import etree
    # Disable external entities, DTDs, and network access — it is uploaded XML.
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
                             load_dtd=False, huge_tree=False)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        sections: list[str] = []
        # Body order is set by the spine in content.hpf. If absent, by section number.
        if "Contents/content.hpf" in names:
            hpf = etree.fromstring(z.read("Contents/content.hpf"), parser)
            hrefs = {item.get("id"): item.get("href")
                     for item in hpf.iter() if _local(item) == "item"}
            sections = [href for ref in hpf.iter() if _local(ref) == "itemref"
                        if (href := hrefs.get(ref.get("idref"))) in names
                        and re.fullmatch(r"Contents/section\d+\.xml", href)]
        if not sections:
            sections = sorted(
                (n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)),
                key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        if not sections:
            raise ValueError("no body sections")
        blocks: list[str] = []
        for name in sections:
            blocks += _hwpx_blocks(etree.fromstring(z.read(name), parser))
    return "\n\n".join(blocks)


_CONVERTERS = {
    ".xlsx": _xlsx_to_markdown,
    ".docx": _docx_to_markdown,
    ".pptx": _pptx_to_markdown,
    ".hwpx": _hwpx_to_markdown,
    ".pdf": _pdf_to_text,
    ".html": _html_to_markdown,
    ".htm": _html_to_markdown,
    ".vtt": _transcript_to_text,
    ".srt": _transcript_to_text,
}

#: Every accepted extension. The frontend's file picker filter (accept in ChatInput.tsx) must match this
#: set — tests/test_uploads_parser.py cross-checks them.
ALLOWED = _TEXT_EXTS | frozenset(_CONVERTERS) | IMAGE_EXTS


def _check_zip(ext: str, data: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            unzipped = sum(info.file_size for info in z.infolist())
    except zipfile.BadZipFile as e:
        raise ValueError(f"cannot parse {ext} file: corrupted or invalid") from e
    if unzipped > _MAX_UNZIPPED_BYTES:
        raise ValueError(f"{ext} file expands beyond "
                         f"{_MAX_UNZIPPED_BYTES // (1024 * 1024)}MB")


def convert(filename: str, data: bytes) -> tuple[str, bool]:
    """Upload that can be converted to text → (markdown, whether truncated)."""
    ext = _ext(filename)
    if ext in _TEXT_EXTS:   # text as is (lossy decode)
        content = data.decode("utf-8", errors="replace")
    elif ext in _CONVERTERS:
        if ext in _ZIP_EXTS:
            _check_zip(ext, data)
        try:
            content = _CONVERTERS[ext](data)
        except Exception as e:
            # A valid extension with corrupt/mismatched bytes (zipfile.BadZipFile,
            # KeyError from openpyxl, PdfStreamError, lxml XMLSyntaxError, etc.)
            # must not surface as a raw 500 — the route's ValueError->415 handler
            # is the intended path for any "we can't make sense of this upload".
            raise ValueError(f"cannot parse {ext} file: corrupted or invalid") from e
    else:
        raise ValueError(f"unsupported extension: {ext or '(none)'}")
    if len(content) > MAX_CHARS:
        cut = MAX_CHARS - len(_TRUNC_MARK)
        return content[:cut] + _TRUNC_MARK, True
    return content, False


def _image_kind(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


@dataclass(frozen=True)
class PreparedUpload:
    """Files to place under `uploads/{uuid8}/`. At least one of the converted copy and the original is present.

    - Documents: converted copy only (`요구사항.docx.md`)
    - PDF: converted copy and original (`요구사항.pdf.md`, `요구사항.pdf`). If the text layer is empty
      (a scan), original only — pointing at an empty converted copy makes the agent read it as "no content".
    - Images: original only (`화면.png`)
    """
    text_key: str | None
    text: str | None
    truncated: bool
    original_key: str | None
    original: bytes | None


def _safe_name(filename: str) -> str:
    name = PurePosixPath(filename or "").name          # drop any path parts
    return re.sub(r"[^\w가-힣.-]+", "-", name).strip("-.") or "upload"


def prepare(filename: str, data: bytes) -> PreparedUpload:
    """One upload → the keys to store and their contents. Files that cannot be accepted raise ValueError.

    Keys are under `uploads/{uuid8}/{original-name}`. The uuid directory is what makes
    this safe: it is unique per upload, so there is no read-then-write window
    to lose a race in, and no collision check to get subtly wrong. The original
    name AND extension are preserved in the converted key (`요구사항.pdf.md`,
    `요구사항.xlsx.md`), because its content is a CONVERSION of them -- the
    trailing `.md` says what the body really is.
    """
    ext = _ext(filename)
    folder = f"uploads/{uuid.uuid4().hex[:8]}"
    name = _safe_name(filename)
    if ext in IMAGE_EXTS:
        kind = _image_kind(data)
        if kind is None:
            raise ValueError(f"cannot parse {ext} file: corrupted or invalid")
        if _IMAGE_KINDS[ext] != kind:
            name = name[:len(name) - len(ext)] + _KIND_EXT[kind]
        return PreparedUpload(None, None, False, f"{folder}/{name}", data)
    text, truncated = convert(filename, data)
    text_key = f"{folder}/{name}.md"
    if ext != ".pdf":
        return PreparedUpload(text_key, text, truncated, None, None)
    if not text.strip():
        return PreparedUpload(None, None, False, f"{folder}/{name}", data)
    return PreparedUpload(text_key, text, truncated, f"{folder}/{name}", data)
