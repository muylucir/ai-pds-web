# backend/aipds/parsers/uploads.py
"""업로드 파일 → 워크스페이스 `uploads/`에 둘 파일.

두 갈래다.

- **텍스트로 바꿔 둔다.** 문서·표·발표자료·웹 페이지·녹취록(md/txt/csv, xlsx,
  docx, pptx, hwpx, html, vtt/srt, pdf)은 업로드 시점에 마크다운으로 변환한다.
  에이전트가 바이너리 포맷을 직접 열 수 없고, 열 수 있더라도 매 턴 다시 파싱할
  이유가 없다. 변환 결과는 룰의 URL 모드와 같은 50,000자 한도로 절단한다(spec §6).
- **원본을 그대로 둔다.** 이미지와 PDF. 에이전트(Claude Code CLI)의 `Read`가 둘을
  직접 본다 — 화면 캡처·화이트보드 사진은 텍스트로 바꿀 것이 없고, PDF의 도표와
  스캔 페이지는 텍스트 추출에서 사라진다. PDF는 변환본도 함께 둔다: 대부분은
  텍스트로 충분하고, 원본 읽기는 페이지마다 토큰이 훨씬 많이 든다.

내용은 신뢰하지 않는 입력이다 — 실행하지 않고 저장만 한다.
"""
from __future__ import annotations
import html
import io
import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

#: 원본 크기 상한. 요청이 지나는 앞단 두 곳도 이 값에 맞춰져 있다 — nginx의
#: `client_max_body_size`(infra/lib/user-data.ts, infra/scripts/aipds-update)와 Next
#: 미들웨어의 `middlewareClientMaxBodySize`(frontend/next.config.mjs). Next는 넘는
#: 바디를 거부하지 않고 **잘라서** 넘기므로, 이 값만 올리면 큰 파일이 깨진 채 도착한다.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_CHARS = 50_000
_TRUNC_MARK = "\n[... 50,000자 초과분 생략]"

#: 압축을 푼 크기의 상한. docx·pptx·xlsx·hwpx는 zip이라, 10MB 압축본이 수 GB로
#: 풀리는 파일(zip bomb)을 파서에 넘기기 전에 거른다. 파이썬 zipfile은 항목을
#: 선언된 크기까지만 풀므로 선언값의 합으로 충분하다.
_MAX_UNZIPPED_BYTES = 100 * 1024 * 1024
_ZIP_EXTS = frozenset({".xlsx", ".docx", ".pptx", ".hwpx"})

_TEXT_EXTS = frozenset({".md", ".txt", ".csv"})

#: 확장자 → 내용으로 판정한 형식. `.png`로 저장된 JPEG(웹에서 받은 그림에 흔하다)는
#: 원본 키의 확장자를 내용에 맞춘다 — 확장자와 내용이 다르면 읽는 쪽이 틀린 형식으로
#: 보낼 수 있다.
_IMAGE_KINDS = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg",
                ".gif": "gif", ".webp": "webp"}
_KIND_EXT = {"png": ".png", "jpeg": ".jpg", "gif": ".gif", "webp": ".webp"}
IMAGE_EXTS = frozenset(_IMAGE_KINDS)


def _ext(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def _cell(text: str) -> str:
    # 셀 안의 줄바꿈·파이프는 마크다운 표를 깨뜨린다.
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
    # 내장 스타일 이름은 Word의 표시 언어와 무관하게 영어로 저장된다("heading 1").
    if style_name == "Title":
        return 1
    m = re.fullmatch(r"Heading (\d)", style_name)
    return min(int(m.group(1)), 6) if m else 0


def _docx_to_markdown(data: bytes) -> str:
    from docx import Document
    from docx.table import Table
    doc = Document(io.BytesIO(data))
    blocks: list[str] = []
    # 본문 순서대로 — 문단과 표가 섞여 있다. 머리말·꼬리말은 페이지마다 반복되는
    # 장식이라 읽지 않는다.
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
        # 발표자 노트에 슬라이드에 없는 맥락이 자주 있다.
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                blocks.append(f"Notes: {notes}")
    return "\n\n".join(blocks)


#: 화면에 보이지 않는 텍스트의 인라인 신호. 숨긴 텍스트는 프롬프트 주입의 흔한
#: 통로라 지운다 — 외부 스타일시트의 클래스로 숨긴 것까지는 못 잡는다(URL 모드와
#: 같은 등급의 알려진 위험).
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
            # src는 버린다 — data: URI 하나가 수십만 자다. 대체 텍스트만 남긴다.
            alt = " ".join((el.get("alt") or "").split())
            return f"[image: {alt}]" if alt else ""

        def convert_a(self, el, text, parent_tags):
            href = (el.get("href") or "").strip().lower()
            if not href.startswith(("http://", "https://", "mailto:")):
                return text           # javascript:·data:·문서 안 앵커
            return super().convert_a(el, text, parent_tags)

    # 바이트를 그대로 넘긴다 — <meta charset>을 보고 EUC-KR 같은 인코딩을 맞춘다.
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
#: 화자 표시가 없는 자막은 이 간격마다 문단을 끊는다 — 한 시간짜리 녹취가 시각
#: 하나 붙은 문단 하나가 되지 않게.
_UNNAMED_PARAGRAPH_SECONDS = 60


def _seconds(clock: str) -> int:
    total = 0
    for part in clock.split(":"):
        total = total * 60 + int(part)
    return total


def _transcript_to_text(data: bytes) -> str:
    """WebVTT·SRT 자막 → `[HH:MM:SS] 화자: 발화` 문단.

    번호·타이밍 줄과 WEBVTT 헤더·NOTE 블록을 버리고, 같은 화자가 이어 말한 큐를
    한 문단으로 합친다. Teams의 VTT는 큐마다 `<v 이름>`으로 화자를 붙인다. 원문의
    거의 절반이 타이밍이라 이렇게 하면 같은 한도에 두 배 가까이 담긴다.
    """
    text = data.decode("utf-8-sig", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    turns: list[tuple[int, str | None, list[str]]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.strip("\n").split("\n")
        at = next((i for i, line in enumerate(lines) if _CUE_TIME.match(line)), None)
        if at is None:
            continue        # WEBVTT 헤더, NOTE·STYLE 블록
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
    # 주석·처리 명령은 tag가 문자열이 아니다.
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
            # 병합 셀이 있으면 순번과 실제 위치가 어긋난다 — cellAddr가 정본이다.
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
    # 가로 병합이 만든 자리는 모든 행에서 비어 있다 — 그런 열은 싣지 않는다.
    cols = sorted({c for (_, c), text in cells.items() if text.strip()}) or [0]
    return _md_table([[cells.get((r, c), "") for c in cols]
                      for r in range(n_rows)])


#: 읽지 않는 컨트롤. 머리말·꼬리말은 페이지마다 반복되는 장식이다.
_HWPX_SKIPPED = frozenset({"header", "footer"})


def _hwpx_blocks(container) -> list[str]:
    """문단(`p`)들을 블록으로. 표와 글상자(`subList`)는 문단 사이의 별도 블록이 된다."""
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
    # 외부 엔티티·DTD·네트워크를 모두 끈다 — 업로드된 XML이다.
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
                             load_dtd=False, huge_tree=False)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        sections: list[str] = []
        # 본문 순서는 content.hpf의 spine이 정한다. 없으면 구역 번호순.
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

#: 받는 확장자 전부. 프론트의 파일 선택 필터(ChatInput.tsx의 accept)가 이 집합과
#: 같아야 한다 — tests/test_uploads_parser.py가 대조한다.
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
    """텍스트로 바꿀 수 있는 업로드 → (마크다운, 절단 여부)."""
    ext = _ext(filename)
    if ext in _TEXT_EXTS:   # 텍스트 그대로 (lossy 디코드)
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
    """`uploads/{uuid8}/` 아래에 둘 파일. 변환본과 원본 중 하나 이상이 있다.

    - 문서: 변환본만 (`요구사항.docx.md`)
    - PDF: 변환본과 원본 (`요구사항.pdf.md`, `요구사항.pdf`). 텍스트 층이 비어
      있으면(스캔본) 원본만 — 빈 변환본을 가리키면 에이전트가 "내용 없음"으로 읽는다.
    - 이미지: 원본만 (`화면.png`)
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
    """업로드 하나 → 저장할 키와 내용. 받을 수 없는 파일은 ValueError.

    키는 `uploads/{uuid8}/{원본명}` 아래다. The uuid directory is what makes
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
