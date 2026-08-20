# backend/tests/test_model_catalog.py
#
# 카탈로그 도메인만 시험한다 — S3는 FakeS3Store, 라우트는 test_routes_models.py.
from __future__ import annotations

import json

import pytest

from aipds.model_catalog import (
    CATALOG_KEY, MAX_DISPLAYED, SEED_MODELS, CatalogError, ModelCatalog,
)
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.mark.asyncio
async def test_missing_file_falls_back_to_seed_without_writing():
    s3 = FakeS3Store()
    entries = await ModelCatalog(s3).load()
    assert [e.model_id for e in entries] == [e.model_id for e in SEED_MODELS]
    # 시드는 읽기 폴백일 뿐이다 — 관리자가 손대기 전까지 파일은 없다.
    assert CATALOG_KEY not in s3.blobs


@pytest.mark.asyncio
async def test_seed_is_the_four_requested_models_in_order():
    assert [(e.name, e.model_id, e.display) for e in SEED_MODELS] == [
        ("Opus 5", "global.anthropic.claude-opus-5", True),
        ("Opus 4.6", "global.anthropic.claude-opus-4-6-v1", True),
        ("Sonnet 5", "global.anthropic.claude-sonnet-5", True),
        ("Sonnet 4.6", "global.anthropic.claude-sonnet-4-6", True),
    ]


@pytest.mark.asyncio
async def test_corrupt_file_falls_back_to_seed():
    s3 = FakeS3Store()
    s3.blobs[CATALOG_KEY] = "{{{ not json"
    entries = await ModelCatalog(s3).load()
    assert [e.model_id for e in entries] == [e.model_id for e in SEED_MODELS]


@pytest.mark.asyncio
async def test_add_writes_seed_plus_new_entry():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    added = await cat.add("Opus 4.8", "global.anthropic.claude-opus-4-8", display=False)
    assert added.model_id == "global.anthropic.claude-opus-4-8"
    stored = json.loads(s3.blobs[CATALOG_KEY])["models"]
    assert len(stored) == len(SEED_MODELS) + 1
    assert stored[-1] == {"name": "Opus 4.8",
                          "model_id": "global.anthropic.claude-opus-4-8",
                          "display": False}


@pytest.mark.asyncio
async def test_add_rejects_a_duplicate_model_id():
    cat = ModelCatalog(FakeS3Store())
    with pytest.raises(CatalogError) as exc:
        await cat.add("다른 이름", SEED_MODELS[0].model_id, display=False)
    assert exc.value.code == "duplicate"


@pytest.mark.asyncio
async def test_add_rejects_the_sixth_displayed_model():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    # 시드 4개가 이미 표시 상태다 — 하나 더는 5개로 허용, 그 다음이 거부된다.
    await cat.add("다섯", "global.anthropic.claude-opus-4-8", display=True)
    with pytest.raises(CatalogError) as exc:
        await cat.add("여섯", "global.anthropic.claude-opus-4-7", display=True)
    assert exc.value.code == "too_many_displayed"
    # 거부는 아무것도 바꾸지 않는다.
    assert len(json.loads(s3.blobs[CATALOG_KEY])["models"]) == 5


@pytest.mark.asyncio
async def test_add_allows_unlimited_hidden_models():
    cat = ModelCatalog(FakeS3Store())
    for i, mid in enumerate(["a", "b", "c", "d", "e", "f"]):
        await cat.add(f"m{i}", f"global.anthropic.claude-{mid}", display=False)
    entries = await cat.load()
    assert len(entries) == len(SEED_MODELS) + 6


@pytest.mark.asyncio
async def test_displayed_returns_only_display_true_capped_at_max():
    s3 = FakeS3Store()
    s3.blobs[CATALOG_KEY] = json.dumps({"models": [
        {"name": f"m{i}", "model_id": f"global.anthropic.claude-x{i}", "display": True}
        for i in range(7)
    ]}, ensure_ascii=False)
    displayed = await ModelCatalog(s3).displayed()
    # 파일이 손으로 편집돼 6개 이상이 켜져 있어도 화면에는 5개만 간다.
    assert len(displayed) == MAX_DISPLAYED
    assert [e.model_id for e in displayed] == [
        f"global.anthropic.claude-x{i}" for i in range(MAX_DISPLAYED)]


@pytest.mark.asyncio
async def test_update_changes_name_and_display():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    target = SEED_MODELS[1].model_id
    updated = await cat.update(target, name="오퍼스 4.6", display=False)
    assert updated.name == "오퍼스 4.6" and updated.display is False
    entries = {e.model_id: e for e in await cat.load()}
    assert entries[target].name == "오퍼스 4.6"
    # 나머지는 그대로다.
    assert entries[SEED_MODELS[0].model_id].name == "Opus 5"


@pytest.mark.asyncio
async def test_update_turning_on_a_sixth_display_is_rejected():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    await cat.add("다섯", "global.anthropic.claude-opus-4-8", display=True)
    hidden = await cat.add("여섯", "global.anthropic.claude-opus-4-7", display=False)
    with pytest.raises(CatalogError) as exc:
        await cat.update(hidden.model_id, display=True)
    assert exc.value.code == "too_many_displayed"


@pytest.mark.asyncio
async def test_update_of_an_unknown_model_id_is_not_found():
    cat = ModelCatalog(FakeS3Store())
    with pytest.raises(CatalogError) as exc:
        await cat.update("global.anthropic.claude-nope", display=False)
    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_remove_deletes_the_entry():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    await cat.remove(SEED_MODELS[0].model_id)
    assert SEED_MODELS[0].model_id not in {e.model_id for e in await cat.load()}


@pytest.mark.asyncio
async def test_remove_of_an_unknown_model_id_is_not_found():
    cat = ModelCatalog(FakeS3Store())
    with pytest.raises(CatalogError) as exc:
        await cat.remove("global.anthropic.claude-nope")
    assert exc.value.code == "not_found"


@pytest.mark.asyncio
async def test_without_a_store_reads_seed_and_refuses_writes():
    # 버킷 미설정(로컬/테스트): 읽기는 되고 쓰기는 거부된다 —
    # durable_projects_enabled()와 같은 규율.
    cat = ModelCatalog(None)
    assert [e.model_id for e in await cat.load()] == [e.model_id for e in SEED_MODELS]
    with pytest.raises(CatalogError) as exc:
        await cat.add("x", "global.anthropic.claude-opus-4-8", display=False)
    assert exc.value.code == "readonly"


@pytest.mark.asyncio
async def test_update_does_not_mutate_the_module_level_seed():
    # 회귀 방지: load()가 list(SEED_MODELS)를 돌려주던 시절 update()의 제자리
    # 변경이 모듈 전역 상수를 영구히 오염시켰다. 파일 순서에 의존한 우연한
    # 검출이 아니라 명시적으로 못박는다.
    before = [(e.name, e.model_id, e.display) for e in SEED_MODELS]
    cat = ModelCatalog(FakeS3Store())
    await cat.update(SEED_MODELS[0].model_id, name="바뀐 이름", display=False)
    assert [(e.name, e.model_id, e.display) for e in SEED_MODELS] == before
