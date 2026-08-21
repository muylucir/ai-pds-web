"""Apply the brand profile to the build workspace.

This module knows nothing about S3 (it receives a profile object) -- which is why two
callers use the same functions: the build session start (proto/session.py) and the hosting
start (routes/prototypes.py). Both points are needed for "a prototype that already carries a
theme is updated to the latest profile by re-hosting alone" to hold -- a prototype created
before the first profile upload, with no theme copy inside prototype/, has nothing for
re-hosting to update and stays unbranded until an improvement session is opened once.

**The truth is the file at the build_dir root, and the file of the same name under
prototype/ is a derivative we manage.** The agent decides the location (it differs per
framework) and we find it by name.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aipds.design_profile import FONT_TOKENS, DesignProfile
from aipds.proto import prompts

_log = logging.getLogger(__name__)

DESIGN_FILENAME = "DESIGN.md"
THEME_FILENAME = "aipds-theme.css"
_CLAUDE_FILENAME = "CLAUDE.md"

#: With this marker on the first line, the file is "a stub left behind after the profile was
#: deleted". theme_required uses it to tell that apart from "a profile is present" -- which
#: is why the gate does not have to look at S3.
_NO_PROFILE_MARKER = "/* aipds-theme: no brand profile */"

_SECTION_START = "<!-- aipds:design:start -->"
_SECTION_END = "<!-- aipds:design:end -->"

#: Where to fall back when the corporate font is not on the browser. Web fonts are not
#: shipped (a non-goal) -- we do not inject an arbitrary CDN into a prototype.
_FALLBACK = {
    "font_sans": "ui-sans-serif, system-ui, -apple-system, sans-serif",
    "font_mono": "ui-monospace, SFMono-Regular, Menlo, monospace",
}

#: Excluded from the walk -- so a coincidentally same-named file inside node_modules is not
#: overwritten, and tens of thousands of files are not walked on every hosting start.
_SKIP_DIRS = {"node_modules", ".next", ".git", "dist", "build", ".turbo"}
_TEXT_SUFFIXES = {".css", ".scss", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".html"}


def _css_var(key: str) -> str:
    return "--" + key.replace("_", "-")


def theme_css(tokens: dict[str, str]) -> str:
    """Build the :root block from the tokens.

    `.dark` is not generated -- dark values cannot be derived from the tokens (inverting
    lightness is a brand decision), and leaving it out keeps shadcn's default dark palette
    alive so the screen does not break.
    """
    lines = [
        "/* aipds-theme: generated from the admin brand profile.",
        "   Do not edit — it is overwritten on every build and every re-host. */",
        ":root {",
    ]
    for key, value in tokens.items():
        rendered = (f"{value}, {_FALLBACK[key]}" if key in FONT_TOKENS else value)
        lines.append(f"  {_css_var(key)}: {rendered};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def stub_css() -> str:
    """The harmless file left behind after the profile is deleted."""
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
                # Symlinked directories are not descended into -- everything under
                # prototype/ is a tree the agent writes freely, so a symlink cycle is
                # possible, and then this stack walk never ends. An infinite loop is not an
                # exception, so no try/except in any caller -- session.start(), the
                # build_complete tool, POST /host -- catches it and they simply hang (not
                # measured; a defensive fix).
                if entry.name not in _SKIP_DIRS and not entry.is_symlink():
                    stack.append(entry)
            else:
                yield entry


def theme_copies(build_dir: Path) -> list[Path]:
    """The theme copies under prototype/. Found by name.

    Symlinks are excluded -- including them would have _write_theme_everywhere call
    write_text on one, writing to whatever arbitrary path a merely same-named symlink points
    at.
    """
    return sorted(p for p in _walk(build_dir)
                  if p.name == THEME_FILENAME and not p.is_symlink())


def theme_required(build_dir: Path) -> bool:
    """Whether a brand profile has to be applied in this workspace.

    It does not look at S3 -- the root theme file's existence and its content are the answer.
    The intent is to keep the gate off the network on a tool-call path.
    """
    root = build_dir / THEME_FILENAME
    if not root.is_file():
        return False
    try:
        return _NO_PROFILE_MARKER not in root.read_text(encoding="utf-8")
    except OSError:
        return False


def theme_imported(build_dir: Path) -> bool:
    """Whether a copy exists and some file inside prototype/ references it."""
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


def _write_theme_everywhere(build_dir: Path, root_theme: Path, css: str) -> None:
    """Bring the root theme file and every copy under prototype/ to the same content.

    Whether it is a stub (no profile) or a real theme (a profile), only "what the content was
    decided to be" differs; "where it is written" is the same -- this sequence lives in one
    place so the two branches cannot rot separately.
    """
    root_theme.write_text(css, encoding="utf-8")
    for copy in theme_copies(build_dir):
        copy.write_text(css, encoding="utf-8")


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
    """Strip only our section. Delete the file when nothing is left -- preserve it when
    someone else's content is there (we do not bake the assumption that this file holds only
    our content into the code)."""
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
    """Bring the workspace into line with the profile. Called on every build and every hosting\n    start."""
    root_theme = build_dir / THEME_FILENAME
    design_md = build_dir / DESIGN_FILENAME
    claude_md = build_dir / _CLAUDE_FILENAME

    if profile is None:
        # If there never was one, nothing is created (the whole feature is opt-in). Only a
        # profile that existed and was then deleted gets overwritten with a stub.
        #
        # Deleting DESIGN.md also happens **only inside** this guard -- root_theme.is_file()
        # is the only evidence that we ever planted anything in this workspace. Deleting
        # outside this condition would have every POST /host of a project that never had a
        # brand profile quietly delete a root DESIGN.md the build agent may have created as
        # its own notes (which is not ours) -- the same principle by which _remove_section
        # preserves someone else's CLAUDE.md content.
        if root_theme.is_file():
            _write_theme_everywhere(build_dir, root_theme, stub_css())
            design_md.unlink(missing_ok=True)
        _remove_section(claude_md)
        return

    build_dir.mkdir(parents=True, exist_ok=True)

    # With no tokens a **stub** is written -- `theme_css({})` would be a file with zero
    # variables under a header saying "generated from the brand profile", and that file
    # lies.
    #
    # Measured 2026-08-19: two projects run with the same zero-token profile diverged. One
    # agent opened that file, judged "it is empty, so there is nothing to override" and left
    # the shadcn defaults (unbranded); the other read the DESIGN.md prose and moved the
    # palette into globals.css (branded). When the enforcing channel is empty, the outcome is
    # left to the agent's discretion.
    #
    # The stub carries the no-profile marker, so theme_required() becomes False -- meaning
    # there is no longer a path by which an empty theme reads as "the brand was applied" (the
    # gate in proto/tools.py). The file itself is kept, so the copy-refresh wiring stays
    # intact: if tokens are uploaded later, re-hosting alone updates the copies too.
    has_tokens = bool(profile.tokens)
    if not has_tokens:
        _log.warning(
            "design profile has no tokens; writing the no-profile stub to %s — "
            "the brand can only reach the screen through the DESIGN.md prose",
            root_theme)
    _write_theme_everywhere(build_dir, root_theme,
                            theme_css(profile.tokens) if has_tokens
                            else stub_css())

    if profile.prose.strip():
        design_md.write_text(profile.prose.strip() + "\n", encoding="utf-8")
    else:
        design_md.unlink(missing_ok=True)

    # The instruction and the content of the file placed next to it come from the same
    # value -- they cannot diverge.
    _upsert_section(claude_md, prompts.design_rules(language,
                                                    has_tokens=has_tokens))
