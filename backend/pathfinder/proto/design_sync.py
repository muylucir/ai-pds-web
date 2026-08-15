"""브랜드 프로필을 빌드 워크스페이스에 반영한다.

이 모듈은 S3를 모른다(프로필 객체를 받는다) — 그래서 두 호출자가 같은 함수를
쓴다: 빌드 세션 시작(proto/session.py)과 호스팅 시작(routes/prototypes.py).
두 지점이 있어야 "이미 완료된 프로토타입도 재호스팅으로 리브랜딩된다"가 성립한다.

**진실은 build_dir 루트의 파일이고, prototype/ 아래 같은 이름의 파일은 우리가
관리하는 파생물이다.** 에이전트는 위치를 정하고(프레임워크마다 다르다) 우리는
이름으로 찾는다.
"""
from __future__ import annotations

from pathlib import Path

from pathfinder.design_profile import FONT_TOKENS, DesignProfile
from pathfinder.proto import prompts

DESIGN_FILENAME = "DESIGN.md"
THEME_FILENAME = "pathfinder-theme.css"
_CLAUDE_FILENAME = "CLAUDE.md"

#: 이 표시가 첫 줄에 있으면 "프로필이 지워진 뒤 남은 스텁"이다. theme_required가
#: 이걸로 "프로필 있음"과 구분한다 — 게이트가 S3를 보지 않아도 되는 이유다.
_NO_PROFILE_MARKER = "/* pathfinder-theme: no brand profile */"

_SECTION_START = "<!-- pathfinder:design:start -->"
_SECTION_END = "<!-- pathfinder:design:end -->"

#: 사내 폰트가 브라우저에 없을 때 떨어질 자리. 웹폰트는 싣지 않는다(비목표) —
#: 임의의 CDN을 프로토타입에 주입하지 않는다.
_FALLBACK = {
    "font_sans": "ui-sans-serif, system-ui, -apple-system, sans-serif",
    "font_mono": "ui-monospace, SFMono-Regular, Menlo, monospace",
}

#: 탐색에서 제외한다 — node_modules 안의 우연한 동명 파일을 덮어쓰지 않고,
#: 매 호스팅에서 수만 개 파일을 걷지 않는다.
_SKIP_DIRS = {"node_modules", ".next", ".git", "dist", "build", ".turbo"}
_TEXT_SUFFIXES = {".css", ".scss", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".html"}


def _css_var(key: str) -> str:
    return "--" + key.replace("_", "-")


def theme_css(tokens: dict[str, str]) -> str:
    """토큰에서 :root 블록을 만든다.

    `.dark`는 만들지 않는다 — 다크 값은 토큰에서 유도할 수 없고(밝기 반전은
    브랜드 결정이다), 만들지 않으면 shadcn 기본 다크 팔레트가 그대로 살아 있어
    화면이 깨지지 않는다.
    """
    lines = [
        "/* pathfinder-theme: generated from the admin brand profile.",
        "   Do not edit — it is overwritten on every build and every re-host. */",
        ":root {",
    ]
    for key, value in tokens.items():
        rendered = (f"{value}, {_FALLBACK[key]}" if key in FONT_TOKENS else value)
        lines.append(f"  {_css_var(key)}: {rendered};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def stub_css() -> str:
    """프로필이 지워진 뒤 남기는 무해한 파일."""
    return (f"{_NO_PROFILE_MARKER}\n"
            "/* The brand profile was removed in admin. This file stays so that\n"
            "   existing imports keep building; shadcn defaults apply. */\n"
            ":root {\n}\n")


def _walk(build_dir: Path):
    proto = build_dir / "prototype"
    if not proto.is_dir():
        return
    stack = [proto]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS:
                    stack.append(entry)
            else:
                yield entry


def theme_copies(build_dir: Path) -> list[Path]:
    """prototype/ 아래의 테마 사본. 이름으로 찾는다."""
    return sorted(p for p in _walk(build_dir) if p.name == THEME_FILENAME)


def theme_required(build_dir: Path) -> bool:
    """이 워크스페이스에 브랜드 프로필이 적용되어야 하는가.

    S3를 보지 않는다 — 루트 테마 파일의 존재와 그 첫 줄이 답이다. 게이트가
    도구 호출 경로에서 네트워크를 타지 않게 하려는 것이다.
    """
    root = build_dir / THEME_FILENAME
    if not root.is_file():
        return False
    try:
        return _NO_PROFILE_MARKER not in root.read_text(encoding="utf-8")
    except OSError:
        return False


def theme_imported(build_dir: Path) -> bool:
    """사본이 있고, prototype/ 안의 어떤 파일이 그것을 참조하는가."""
    if not theme_copies(build_dir):
        return False
    for path in _walk(build_dir):
        if path.name == THEME_FILENAME or path.suffix not in _TEXT_SUFFIXES:
            continue
        try:
            if THEME_FILENAME in path.read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return False


def _upsert_section(path: Path, body: str) -> None:
    block = f"{_SECTION_START}\n{body}{_SECTION_END}\n"
    if not path.is_file():
        path.write_text(block, encoding="utf-8")
        return
    text = path.read_text(encoding="utf-8")
    if _SECTION_START in text and _SECTION_END in text:
        head, _, rest = text.partition(_SECTION_START)
        _, _, tail = rest.partition(_SECTION_END)
        path.write_text(head + block + tail.lstrip("\n"), encoding="utf-8")
        return
    joiner = "" if text.endswith("\n") else "\n"
    path.write_text(text + joiner + block, encoding="utf-8")


def _remove_section(path: Path) -> None:
    """우리 절만 걷어낸다. 남는 것이 없으면 파일을 지운다 — 남의 내용이 있으면
    보존한다(이 파일이 우리 것만 담는다는 가정을 코드에 박지 않는다)."""
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if _SECTION_START in text and _SECTION_END in text:
        head, _, rest = text.partition(_SECTION_START)
        _, _, tail = rest.partition(_SECTION_END)
        text = (head + tail.lstrip("\n"))
    if text.strip():
        path.write_text(text, encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def sync_design(build_dir: Path, profile: DesignProfile | None,
                language: str) -> None:
    """워크스페이스를 프로필과 일치시킨다. 매 빌드·매 호스팅에서 호출된다."""
    root_theme = build_dir / THEME_FILENAME
    design_md = build_dir / DESIGN_FILENAME
    claude_md = build_dir / _CLAUDE_FILENAME

    if profile is None:
        # 한 번도 없었으면 아무것도 만들지 않는다(기능 전체가 opt-in이다).
        # 있었다가 지워진 경우에만 스텁으로 덮는다.
        if root_theme.is_file():
            css = stub_css()
            root_theme.write_text(css, encoding="utf-8")
            for copy in theme_copies(build_dir):
                copy.write_text(css, encoding="utf-8")
        design_md.unlink(missing_ok=True)
        _remove_section(claude_md)
        return

    build_dir.mkdir(parents=True, exist_ok=True)
    css = theme_css(profile.tokens)
    root_theme.write_text(css, encoding="utf-8")
    for copy in theme_copies(build_dir):
        copy.write_text(css, encoding="utf-8")

    if profile.prose.strip():
        design_md.write_text(profile.prose.strip() + "\n", encoding="utf-8")
    else:
        design_md.unlink(missing_ok=True)

    _upsert_section(claude_md, prompts.design_rules(language))
