"""Apply the brand profile to the build workspace.

This module knows nothing about S3 (it receives a profile object) -- so two
callers share the same function: build session start (proto/session.py) and
hosting start (routes/prototypes.py). Both call sites are needed for "a
prototype that already carries the theme is updated to the latest profile just
by re-hosting" to hold -- a prototype created before the first profile upload,
with no theme copy inside prototype/, has nothing for re-hosting to update, so
it stays unbranded until an improvement session is opened once.

**The truth is the file at the build_dir root; files of the same name under
prototype/ are derivatives we manage.** The agent decides the location (it
differs per framework) and we find them by name.

build_dir is a tree the build agent writes to, so every read and write here
refuses to follow links (`pathsafe`'s `*_beneath`).
"""
from __future__ import annotations

import logging
from pathlib import Path

from aipds.design_profile import FONT_TOKENS, DesignProfile
from aipds.pathsafe import (read_text_beneath, stat_beneath, unlink_beneath,
                            write_beneath)
from aipds.proto import prompts

_log = logging.getLogger(__name__)

DESIGN_FILENAME = "DESIGN.md"
THEME_FILENAME = "aipds-theme.css"
_CLAUDE_FILENAME = "CLAUDE.md"

#: When this marker is on the first line, the file is "the stub left after the
#: profile was removed". theme_required uses it to tell that apart from "a profile
#: exists" -- which is why the gate does not need to look at S3.
_NO_PROFILE_MARKER = "/* aipds-theme: no brand profile */"

_SECTION_START = "<!-- aipds:design:start -->"
_SECTION_END = "<!-- aipds:design:end -->"

#: Where to fall back when the in-house font is not in the browser. Web fonts are
#: not shipped (a non-goal) -- we do not inject arbitrary CDNs into a prototype.
_FALLBACK = {
    "font_sans": "ui-sans-serif, system-ui, -apple-system, sans-serif",
    "font_mono": "ui-monospace, SFMono-Regular, Menlo, monospace",
}

#: Excluded from the walk -- so we neither overwrite a coincidentally same-named
#: file inside node_modules nor walk tens of thousands of files on every hosting.
_SKIP_DIRS = {"node_modules", ".next", ".git", "dist", "build", ".turbo"}
_TEXT_SUFFIXES = {".css", ".scss", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".html"}


def _css_var(key: str) -> str:
    return "--" + key.replace("_", "-")


def theme_css(tokens: dict[str, str]) -> str:
    """Build a :root block from the tokens.

    No `.dark` block is generated -- dark values cannot be derived from the
    tokens (inverting lightness is a brand decision), and without one the
    shadcn default dark palette stays intact so the screen does not break.
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
    """The harmless file left behind after the profile is removed."""
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
                # Do not descend into symlinked directories -- prototype/ is a
                # tree the agent writes freely, so a cyclic symlink is possible,
                # and then this stack walk never ends. An infinite loop is not
                # an exception, so no caller's try/except (session.start(), the
                # build_complete tool, POST /host) can catch it; it just hangs
                # (not observed -- a defensive fix).
                if entry.name not in _SKIP_DIRS and not entry.is_symlink():
                    stack.append(entry)
            else:
                yield entry


def theme_copies(build_dir: Path) -> list[Path]:
    """Theme copies under prototype/. Found by name.

    Symlinks are excluded -- including them would make _write_theme_everywhere
    call write_text on them, writing to whatever arbitrary path a symlink that
    merely shares the name points at.
    """
    return sorted(p for p in _walk(build_dir)
                  if p.name == THEME_FILENAME and not p.is_symlink())


def theme_required(build_dir: Path) -> bool:
    """Must the brand profile be applied to this workspace?

    Does not look at S3 -- the existence and content of the root theme file
    are the answer. This keeps the gate off the network on the tool-call path.
    """
    try:
        return _NO_PROFILE_MARKER not in read_text_beneath(build_dir, THEME_FILENAME)
    except (OSError, ValueError):
        return False


def theme_imported(build_dir: Path) -> bool:
    """Is there a copy, and does some file inside prototype/ reference it?"""
    if not theme_copies(build_dir):
        return False
    for path in _walk(build_dir):
        if path.name == THEME_FILENAME or path.suffix not in _TEXT_SUFFIXES:
            continue
        try:
            text = read_text_beneath(build_dir, path.relative_to(build_dir).as_posix(),
                                     errors="ignore")
        except OSError:
            continue
        if THEME_FILENAME in text:
            return True
    return False


def _write_theme_everywhere(build_dir: Path, css: str) -> None:
    """Bring the root theme file and every copy under prototype/ to the same content.

    Whether it is the stub (no profile) or a real theme (profile present), only
    "what content was chosen" differs; "where it is written" is the same --
    this sequence lives in one place so the two branches cannot go stale
    separately.
    """
    write_beneath(build_dir, THEME_FILENAME, css)
    for copy in theme_copies(build_dir):
        write_beneath(build_dir, copy.relative_to(build_dir).as_posix(), css)


def _upsert_section(build_dir: Path, rel: str, body: str) -> None:
    block = f"{_SECTION_START}\n{body}{_SECTION_END}\n"
    if stat_beneath(build_dir, rel) is None:
        write_beneath(build_dir, rel, block)
        return
    text = read_text_beneath(build_dir, rel)
    if _SECTION_START in text and _SECTION_END in text:
        head, _, rest = text.partition(_SECTION_START)
        _, _, tail = rest.partition(_SECTION_END)
        write_beneath(build_dir, rel, head + block + tail.lstrip("\n"))
        return
    joiner = "" if text.endswith("\n") else "\n"
    write_beneath(build_dir, rel, text + joiner + block)


def _remove_section(build_dir: Path, rel: str) -> None:
    """Strip only our section. If nothing is left, delete the file -- if someone
    else's content is there, preserve it (the code does not bake in the
    assumption that this file holds only ours)."""
    if stat_beneath(build_dir, rel) is None:
        return
    text = read_text_beneath(build_dir, rel)
    if _SECTION_START in text and _SECTION_END in text:
        head, _, rest = text.partition(_SECTION_START)
        _, _, tail = rest.partition(_SECTION_END)
        text = (head + tail.lstrip("\n"))
    if text.strip():
        write_beneath(build_dir, rel, text)
    else:
        unlink_beneath(build_dir, rel)


def sync_design(build_dir: Path, profile: DesignProfile | None,
                language: str) -> None:
    """Bring the workspace in line with the profile. Called on every build and every hosting."""
    if profile is None:
        # If there never was one, create nothing (the whole feature is opt-in).
        # Overwrite with the stub only when one existed and was then removed.
        #
        # Deleting DESIGN.md also happens **only inside** this guard -- the root
        # theme file's existence is the only evidence that we planted anything
        # in this workspace. Deleting outside this condition would make every
        # POST /host for a project that never had a brand profile silently
        # delete a root DESIGN.md the build agent may have created as its own
        # notes (even though it is not ours) -- the same principle as
        # _remove_section preserving someone else's CLAUDE.md content.
        if stat_beneath(build_dir, THEME_FILENAME) is not None:
            _write_theme_everywhere(build_dir, stub_css())
            unlink_beneath(build_dir, DESIGN_FILENAME)
        _remove_section(build_dir, _CLAUDE_FILENAME)
        return

    build_dir.mkdir(parents=True, exist_ok=True)

    # With no tokens, write the **stub** -- `theme_css({})` is a zero-variable file
    # headed "generated from the admin brand profile", and that file lies.
    #
    # Measured 2026-08-19: two projects run with the same zero-token profile
    # diverged. One agent opened that file, decided "it is empty, so there is
    # nothing to override" and left the shadcn defaults (unbranded); the other
    # read the DESIGN.md prose and moved the palette into globals.css (branded).
    # When the enforced channel is empty, the outcome is left to the agent's
    # discretion.
    #
    # The stub carries the no-profile marker, so theme_required() is False --
    # i.e. the path where an empty theme reads as "brand applied" disappears
    # (the gate in proto/tools.py). The file itself stays, so the copy-update
    # wiring is unchanged: when tokens arrive later, re-hosting alone replaces
    # the copies too.
    has_tokens = bool(profile.tokens)
    if not has_tokens:
        _log.warning(
            "design profile has no tokens; writing the no-profile stub to %s — "
            "the brand can only reach the screen through the DESIGN.md prose",
            build_dir / THEME_FILENAME)
    _write_theme_everywhere(build_dir,
                            theme_css(profile.tokens) if has_tokens
                            else stub_css())

    if profile.prose.strip():
        write_beneath(build_dir, DESIGN_FILENAME, profile.prose.strip() + "\n")
    else:
        unlink_beneath(build_dir, DESIGN_FILENAME)

    # The instructions and the content of the file next to them come from the
    # same value -- they cannot drift apart.
    _upsert_section(build_dir, _CLAUDE_FILENAME,
                    prompts.design_rules(language, has_tokens=has_tokens))
