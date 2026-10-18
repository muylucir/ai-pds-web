# backend/tests/test_model_catalog.py
#
# Tests only the catalog domain — S3 is FakeS3Store; routes are in test_routes_models.py.
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
    # The seed is only a read fallback — there is no file until an admin touches it.
    assert CATALOG_KEY not in s3.blobs


@pytest.mark.asyncio
async def test_seed_is_the_four_requested_models_with_their_effort_in_order():
    """Model and effort are a pair — the first entry is the default selection on the project creation screen."""
    assert [(e.name, e.model_id, e.display, e.effort) for e in SEED_MODELS] == [
        ("Opus 5.5", "global.anthropic.claude-opus-5-5", True, "medium"),
        ("Sonnet 5.5", "global.anthropic.claude-sonnet-5-5", True, "high"),
        ("Opus 5.0", "global.anthropic.claude-opus-5", True, "high"),
        ("Sonnet 5.0", "global.anthropic.claude-sonnet-5", True, "high"),
    ]


@pytest.mark.asyncio
async def test_a_catalog_file_without_effort_still_loads_as_cli_default():
    """A catalog saved before the effort field existed (these really exist in production) is read as None = CLI default."""
    s3 = FakeS3Store()
    s3.blobs[CATALOG_KEY] = json.dumps({"models": [
        {"name": "Opus 5.5", "model_id": "global.anthropic.claude-opus-5-5", "display": True}]})
    [entry] = await ModelCatalog(s3).load()
    assert entry.effort is None


@pytest.mark.asyncio
async def test_effort_of_reads_the_entry_and_is_none_for_unknown_models():
    cat = ModelCatalog(FakeS3Store())
    assert await cat.effort_of("global.anthropic.claude-sonnet-5-5") == "high"
    assert await cat.effort_of("global.anthropic.claude-unknown") is None


@pytest.mark.asyncio
async def test_update_sets_and_clears_effort_and_leaves_it_when_not_given():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    mid = SEED_MODELS[0].model_id
    assert (await cat.update(mid, effort="high")).effort == "high"
    assert (await cat.update(mid, display=True)).effort == "high"   # unchanged if not passed
    assert (await cat.update(mid, effort=None)).effort is None       # None clears it
    assert json.loads(s3.blobs[CATALOG_KEY])["models"][0]["effort"] is None


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
                          "display": False, "effort": None}


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
    # The 4 seed models are already displayed — one more is allowed as the 5th; the next is rejected.
    await cat.add("다섯", "global.anthropic.claude-opus-4-8", display=True)
    with pytest.raises(CatalogError) as exc:
        await cat.add("여섯", "global.anthropic.claude-opus-4-7", display=True)
    assert exc.value.code == "too_many_displayed"
    # A rejection changes nothing.
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
    # Even if the file was hand-edited to turn on 6 or more, only 5 go to the screen.
    assert len(displayed) == MAX_DISPLAYED
    assert [e.model_id for e in displayed] == [
        f"global.anthropic.claude-x{i}" for i in range(MAX_DISPLAYED)]


@pytest.mark.asyncio
async def test_update_changes_name_and_display():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    target = SEED_MODELS[1].model_id
    updated = await cat.update(target, name="소네트 5.5", display=False)
    assert updated.name == "소네트 5.5" and updated.display is False
    entries = {e.model_id: e for e in await cat.load()}
    assert entries[target].name == "소네트 5.5"
    # Everything else is unchanged — effort was not touched either, so it is unchanged.
    assert entries[target].effort == SEED_MODELS[1].effort
    assert entries[SEED_MODELS[0].model_id].name == SEED_MODELS[0].name


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
    # No bucket configured (local/test): reads work and writes are rejected —
    # same discipline as durable_projects_enabled().
    cat = ModelCatalog(None)
    assert [e.model_id for e in await cat.load()] == [e.model_id for e in SEED_MODELS]
    with pytest.raises(CatalogError) as exc:
        await cat.add("x", "global.anthropic.claude-opus-4-8", display=False)
    assert exc.value.code == "readonly"


@pytest.mark.asyncio
async def test_update_does_not_mutate_the_module_level_seed():
    # Regression guard: back when load() returned list(SEED_MODELS), update()'s in-place
    # mutation permanently polluted the module-global constant. Pin it down explicitly rather
    # than relying on accidental detection that depends on file order.
    before = [(e.name, e.model_id, e.display) for e in SEED_MODELS]
    cat = ModelCatalog(FakeS3Store())
    await cat.update(SEED_MODELS[0].model_id, name="바뀐 이름", display=False)
    assert [(e.name, e.model_id, e.display) for e in SEED_MODELS] == before


# ---- reorder ----

@pytest.mark.asyncio
async def test_reorder_stores_the_new_order_and_the_combo_follows_it():
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    ids = [e.model_id for e in SEED_MODELS]
    new = list(reversed(ids))
    await cat.reorder(new)
    assert [e["model_id"] for e in json.loads(s3.blobs[CATALOG_KEY])["models"]] == new
    # The combo box order and the default selection (first entry) come from this order.
    assert [e.model_id for e in await cat.displayed()] == new


@pytest.mark.asyncio
async def test_reorder_keeps_name_and_display():
    cat = ModelCatalog(FakeS3Store())
    await cat.update(SEED_MODELS[0].model_id, name="오퍼스", display=False)
    ids = [e.model_id for e in SEED_MODELS]
    await cat.reorder(ids[1:] + ids[:1])
    last = (await cat.load())[-1]
    assert (last.model_id, last.name, last.display) == (ids[0], "오퍼스", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", [
    lambda ids: ids[:-1],                        # doesn't know about a model added in another tab
    lambda ids: ids + ["global.anthropic.x"],    # sends a model deleted in another tab
    lambda ids: ids[:-1] + ids[:1],              # duplicate
])
async def test_reorder_rejects_anything_but_a_permutation(mutate):
    s3 = FakeS3Store()
    cat = ModelCatalog(s3)
    with pytest.raises(CatalogError) as exc:
        await cat.reorder(mutate([e.model_id for e in SEED_MODELS]))
    assert exc.value.code == "stale"
    assert CATALOG_KEY not in s3.blobs
