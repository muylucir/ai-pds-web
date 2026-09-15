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
