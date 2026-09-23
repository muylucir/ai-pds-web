# backend/tests/test_proto_store.py — 프로토타입의 정본(aipds/proto/store.py).
#
# 이 파일이 지키는 것: 로컬 빌드 트리가 사라져도(인스턴스 교체) 같은 소스·같은 링크·
# 같은 호스팅 의도가 S3에서 돌아온다.
import json

import pytest

from aipds.proto.store import (KEEP_GENERATIONS, PrototypeStore, resolve_token,
                               source_prefix)
from fakes.in_memory_s3 import FakeS3Store

SLUG = "demo"
PNG = b"\x89PNG\r\n\x1a\n\x00\xff\xfe"


def _tree(root, files):
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode())


def _read_tree(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def store():
    return PrototypeStore(FakeS3Store(), root=FakeS3Store(), project_id="p1")


async def test_a_snapshot_restores_byte_for_byte_after_the_local_tree_is_gone(store, tmp_path):
    build = tmp_path / "a" / SLUG
    _tree(build, {"prototype/package.json": "{}", "prototype/public/logo.png": PNG,
                  "PROTOTYPE-demo.md": "# spec"})
    assert await store.snapshot(SLUG, build, "test") == 1

    fresh = tmp_path / "b" / SLUG
    assert await store.restore(SLUG, fresh) is True
    assert _read_tree(fresh) == _read_tree(build)


async def test_the_snapshot_leaves_out_what_the_bundle_leaves_out(store, tmp_path):
    """`node_modules`·`.next`·호스팅 로그·토큰은 세대에 담지 않는다 — 번들·아카이브와
    같은 제외 규칙이다(project_bundle.source_excluded)."""
    build = tmp_path / SLUG
    _tree(build, {"prototype/package.json": "{}",
                  "prototype/node_modules/x/index.js": "x",
                  "prototype/.next/cache/a": "a",
                  "prototype/.proto-host.log": "log",
                  ".proto-token": "secret"})
    await store.snapshot(SLUG, build, "test")
    rels = [rel for rel, _ in await store.entries(SLUG)]
    assert rels == ["prototype/package.json"]


async def test_no_build_output_means_no_generation(store, tmp_path):
    """명세만 있는 트리를 세대로 만들면 되살리기가 "빌드됨"을 거짓으로 만든다."""
    build = tmp_path / SLUG
    _tree(build, {"PROTOTYPE-demo.md": "# spec"})
    assert await store.snapshot(SLUG, build, "test") is None
    assert await store.has_source(SLUG) is False


async def test_unchanged_source_makes_no_new_generation(store, tmp_path):
    build = tmp_path / SLUG
    _tree(build, {"prototype/a.js": "1"})
    assert await store.snapshot(SLUG, build, "test") == 1
    assert await store.snapshot(SLUG, build, "test") is None
    _tree(build, {"prototype/a.js": "2"})
    assert await store.snapshot(SLUG, build, "test") == 2


async def test_only_the_recent_generations_are_kept(store, tmp_path):
    build = tmp_path / SLUG
    for n in range(KEEP_GENERATIONS + 2):
        _tree(build, {"prototype/a.js": str(n)})
        await store.snapshot(SLUG, build, "test")
    keys = await store._s3.list(source_prefix(SLUG))
    gens = sorted({k[len(source_prefix(SLUG)):].split("/")[0] for k in keys} - {"current.json"})
    assert gens == [f"{g:06d}" for g in range(3, KEEP_GENERATIONS + 3)]


async def test_a_half_uploaded_generation_is_never_current(tmp_path):
    """`current.json`을 마지막에 쓴다 — 올리다 실패한 세대는 가리켜지지 않는다."""
    s3 = FakeS3Store()
    store = PrototypeStore(s3)
    build = tmp_path / SLUG
    _tree(build, {"prototype/a.js": "v1"})
    await store.snapshot(SLUG, build, "test")

    _tree(build, {"prototype/a.js": "v2", "prototype/b.js": "new"})
    real = s3.put_bytes
    calls = {"n": 0}

    async def flaky(key, content):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("network")
        await real(key, content)

    s3.put_bytes = flaky
    with pytest.raises(RuntimeError):
        await store.snapshot(SLUG, build, "test")
    assert (await store.current(SLUG))["gen"] == 1
    assert dict(await store.entries(SLUG)) == {"prototype/a.js": b"v1"}


async def test_restore_leaves_a_local_build_alone(store, tmp_path):
    """로컬이 더 새로울 수 있다(아직 스냅샷되지 않은 편집)."""
    build = tmp_path / SLUG
    _tree(build, {"prototype/a.js": "old"})
    await store.snapshot(SLUG, build, "test")
    _tree(build, {"prototype/a.js": "edited"})
    assert await store.restore(SLUG, build) is False
    assert (build / "prototype/a.js").read_text() == "edited"


async def test_a_token_survives_the_local_tree_through_the_root_index(store, tmp_path):
    await store.save_token(SLUG, "tok-abc")
    assert await resolve_token(store._root, "tok-abc") == ("p1", SLUG)
    # 색인 키는 해시다 — 목록이 토큰 평문을 드러내지 않는다.
    assert all("tok-abc" not in k for k in await store._root.list(""))

    fresh = tmp_path / SLUG
    await store.restore(SLUG, fresh)
    assert (fresh / ".proto-token").read_text() == "tok-abc"


async def test_forgetting_a_token_unlinks_it(store):
    await store.save_token(SLUG, "tok-abc")
    await store.forget_token(SLUG)
    assert await resolve_token(store._root, "tok-abc") is None


async def test_desired_state_lists_only_what_should_run(store):
    await store.set_desired_running("a")
    await store.set_desired_running("b")
    await store.clear_desired("a")
    await store._s3.put("prototypes/c/session.json", json.dumps({}))
    assert await store.desired_running() == ["b"]
