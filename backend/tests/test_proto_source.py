# backend/tests/test_proto_source.py — 빌드 트리에서 "소스"만 골라 보는 규칙.
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
    """**이 제외가 없으면 실행 중인 프로토타입이 영원히 낡은 것으로 보인다.**
    호스팅이 `built_at`을 잡은 뒤에도 `.proto-host.log`에 계속 쓰기 때문이다 —
    실측으로 이 테스트가 없던 판정이 그렇게 틀렸다.

    `.proto-token`은 자격증명이라 애초에 이 집합에 있다(project_bundle)."""
    import os

    source = _write(tmp_path / "prototype" / "a.tsx")
    os.utime(source, (1_000_000, 1_000_000))
    for name in (".proto-host.log", ".proto-host.pid", ".proto-token"):
        _write(tmp_path / name)

    assert newest_source_mtime(tmp_path) == pytest.approx(1_000_000)


def test_build_artifacts_are_not_source(tmp_path):
    """`npm run build`가 `.next/`를 쓰고 `npm install`이 `node_modules/`를 쓴다.
    둘 다 `built_at`보다 반드시 새로우므로, 세지 않으면 모든 프로토타입이 낡았다고
    나온다."""
    import os

    source = _write(tmp_path / "prototype" / "a.tsx")
    os.utime(source, (1_000_000, 1_000_000))
    _write(tmp_path / "prototype" / ".next" / "chunk.js")
    _write(tmp_path / "prototype" / "node_modules" / "dep" / "index.js")
    _write(tmp_path / ".git" / "HEAD")

    assert newest_source_mtime(tmp_path) == pytest.approx(1_000_000)


def test_it_does_not_walk_into_excluded_directories(tmp_path):
    """**성능이 이유다.** 목록 라우트가 카드마다 이것을 부르고 그 목록은 폴링된다.
    `node_modules`는 실측 수만 개 파일이라, 걸으면서 걸러내는 대신 들어가지 않아야
    한다 — 걸어놓고 필터하면 정답은 같지만 목록 응답이 느려진다."""
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


# ---- local_entries: 잘라내며 걸어도 답이 같다 ----

def _rglob_entries(build_dir):
    """다 걸은 뒤 거르는 기준 구현. local_entries는 이것과 같은 답을 내야 한다."""
    from aipds.project_bundle import source_excluded
    out = []
    for path in sorted(build_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(build_dir).as_posix()
        if not source_excluded(rel):
            out.append((rel, path.read_bytes()))
    return out


def test_local_entries_prunes_without_changing_the_answer(tmp_path):
    """같은 파일, 같은 바이트, **같은 순서**.

    순서까지 보는 이유: 세대 해시(`store._hash_entries`)가 이 순서로 계산된다.
    `a.b`와 `a/b`는 문자열 정렬과 경로 조각 정렬에서 앞뒤가 뒤집히므로 일부러 둘 다
    둔다 — 문자열로 정렬하는 구현은 소스가 그대로인데 새 세대를 만든다.
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
    assert "a/b" in rels and "a.b" in rels and "linked-file" in rels
    assert not any("node_modules" in r or ".next" in r for r in rels)


def test_local_entries_of_a_missing_tree_is_empty(tmp_path):
    from aipds.proto.store import local_entries
    assert local_entries(tmp_path / "nope") == []
