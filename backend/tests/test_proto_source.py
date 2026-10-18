# backend/tests/test_proto_source.py — rules for picking out only the "source" from a build tree.
from __future__ import annotations

import time

import pytest

from aipds.proto.source import newest_source_mtime


def _write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_no_tree_gives_no_timestamp(tmp_path):
    assert newest_source_mtime(tmp_path / "nope") is None


def test_an_empty_tree_gives_no_timestamp(tmp_path):
    (tmp_path / "empty").mkdir()
    assert newest_source_mtime(tmp_path / "empty") is None


def test_it_reports_the_newest_source_file(tmp_path):
    old = _write(tmp_path / "prototype" / "a.tsx")
    import os
    os.utime(old, (1_000_000, 1_000_000))
    new = _write(tmp_path / "prototype" / "b.tsx")

    assert newest_source_mtime(tmp_path) == pytest.approx(new.stat().st_mtime)


def test_host_bookkeeping_is_not_source(tmp_path):
    """**Without this exclusion, a running prototype would look stale forever.**
    Hosting keeps writing to `.proto-host.log` even after `built_at` is recorded; in
    practice, the check that predated this test got exactly that wrong.

    `.proto-token` is a credential, so it was in this set to begin with (project_bundle)."""
    import os

    source = _write(tmp_path / "prototype" / "a.tsx")
    os.utime(source, (1_000_000, 1_000_000))
    for name in (".proto-host.log", ".proto-host.pid", ".proto-token"):
        _write(tmp_path / name)

    assert newest_source_mtime(tmp_path) == pytest.approx(1_000_000)


def test_build_artifacts_are_not_source(tmp_path):
    """`npm run build` writes `.next/` and `npm install` writes `node_modules/`.
    Both are always newer than `built_at`, so unless they are skipped, every prototype
    shows as stale."""
    import os

    source = _write(tmp_path / "prototype" / "a.tsx")
    os.utime(source, (1_000_000, 1_000_000))
    _write(tmp_path / "prototype" / ".next" / "chunk.js")
    _write(tmp_path / "prototype" / "node_modules" / "dep" / "index.js")
    _write(tmp_path / ".git" / "HEAD")

    assert newest_source_mtime(tmp_path) == pytest.approx(1_000_000)


def test_it_does_not_walk_into_excluded_directories(tmp_path):
    """**Performance is the reason.** The list route calls this for every card, and that list
    is polled. `node_modules` measures in the tens of thousands of files, so it must not be
    entered at all rather than filtered while walking; walking then filtering gives the same
    answer but slows the list response."""
    heavy = tmp_path / "prototype" / "node_modules"
    heavy.mkdir(parents=True)
    _write(tmp_path / "prototype" / "a.tsx")

    walked: list[str] = []
    real_scandir = __import__("os").scandir

    def spy(path):
        walked.append(str(path))
        return real_scandir(path)

    import os as os_mod
    orig = os_mod.scandir
    os_mod.scandir = spy
    try:
        newest_source_mtime(tmp_path)
    finally:
        os_mod.scandir = orig

    assert not any("node_modules" in p for p in walked), walked


# ---- local_entries: pruning while walking gives the same answer ----

def _rglob_entries(build_dir):
    """Reference implementation that walks everything, then filters. local_entries must give the same answer."""
    from aipds.project_bundle import source_excluded
    out = []
    for path in sorted(build_dir.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(build_dir).as_posix()
        if not source_excluded(rel):
            out.append((rel, path.read_bytes()))
    return out


def test_local_entries_prunes_without_changing_the_answer(tmp_path):
    """Same files, same bytes, **same order**.

    Why order is checked too: the generation hash (`store._hash_entries`) is computed in
    this order. `a.b` and `a/b` swap places between string sorting and path-segment sorting,
    so both are included on purpose: an implementation that sorts by string would create a
    new generation even though the source is unchanged.
    """
    import os
    from aipds.proto.host import TOKEN_FILENAME
    from aipds.proto.store import local_entries

    root = tmp_path / "build"
    _write(root / "prototype" / "app" / "page.tsx", "page")
    _write(root / "a.b", "dot")
    _write(root / "a" / "b", "slash")
    _write(root / "a-b", "dash")
    _write(root / "node_modules" / "react" / "index.js", "dep")
    _write(root / "prototype" / "node_modules" / "x" / "y.js", "nested dep")
    _write(root / "prototype" / ".next" / "cache" / "z", "build")
    _write(root / ".git" / "HEAD", "ref")
    _write(root / ".proto-host.log", "log")
    _write(root / "prototype" / TOKEN_FILENAME, "secret")
    (root / "img.png").write_bytes(b"\x89PNG\x00\xff")
    os.symlink(root / "prototype", root / "linked-dir")
    os.symlink(root / "a.b", root / "linked-file")

    got = local_entries(root)
    assert got == _rglob_entries(root)
    rels = [rel for rel, _ in got]
    assert "a/b" in rels and "a.b" in rels
    # Links are not included: the build agent writes this tree, so whatever a link points to would end up in generations and downloads.
    assert "linked-file" not in rels
    assert not any("node_modules" in r or ".next" in r for r in rels)


def test_local_entries_of_a_missing_tree_is_empty(tmp_path):
    from aipds.proto.store import local_entries
    assert local_entries(tmp_path / "nope") == []
