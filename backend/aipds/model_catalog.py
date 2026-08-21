"""The model catalogue -- the list of models the project creation screen can choose from.

It lives at `models/catalog.json` at the bucket root. It sits outside projects/ because the
catalogue has to exist before any project does (the project creation screen reads it with no
project) -- the same reason surveys/by-token/ is outside the project prefix.

The seed list is **a read-time fallback only and is never written to the file.** The combobox
has to be populated right after a deployment without the administrator doing anything, and
conversely treating an 'empty catalogue' as a valid state would block the first project
creation -- the seeds are a bootstrap path rather than a convenience. The file only comes into
existence when the administrator first edits it.

The display cap (5) applies to `display` rather than to registration: an administrator
registers several models and exposes only 5 of them on screen. Putting the cap on registration
would not satisfy the requirement.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: A key relative to the bucket root. The fourth prefix alongside projects/, sessions/ and
#: surveys/.
CATALOG_KEY = "models/catalog.json"

#: How many models can appear in the combobox at once. Unrelated to how many are
#: registered.
MAX_DISPLAYED = 5


class ModelEntry(BaseModel):
    name: str
    model_id: str
    display: bool = True


class CatalogError(Exception):
    """A catalogue policy violation. `code` is translated into the route's HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: All four were confirmed ACTIVE in ap-northeast-2 by measurement, with
#: list-inference-profiles. The deployment default (MODEL = opus-4-8 in
#: backend-permissions.ts) is not here -- deliberately: it is used only as the fallback for
#: projects created before this feature and for an unspecified model, and it does not appear in
#: the combobox.
SEED_MODELS: tuple[ModelEntry, ...] = (
    ModelEntry(name="Opus 5", model_id="global.anthropic.claude-opus-5"),
    ModelEntry(name="Opus 4.6", model_id="global.anthropic.claude-opus-4-6-v1"),
    ModelEntry(name="Sonnet 5", model_id="global.anthropic.claude-sonnet-5"),
    ModelEntry(name="Sonnet 4.6", model_id="global.anthropic.claude-sonnet-4-6"),
)


class ModelCatalog:
    """Reads and writes for the catalogue. With a None `s3` it is read-only (no bucket\n    configured)."""

    def __init__(self, s3: S3StoreLike | None) -> None:
        self._s3 = s3

    async def load(self) -> list[ModelEntry]:
        """The full registered list. It falls back to the seeds when the file is absent or
        corrupted.

        Why corruption does not raise: failing to read the catalogue would block every project
        creation. Falling back to the seeds keeps the workshop running, and the cause is in the
        log.
        """
        # Copied with model_copy() -- list(SEED_MODELS) creates only a new list while sharing
        # the ModelEntry objects, so an in-place change in update() would permanently
        # contaminate the module-global constant (measured: one display toggle persisted for
        # the life of the process).
        if self._s3 is None:
            return [e.model_copy() for e in SEED_MODELS]
        try:
            body = await self._s3.get(CATALOG_KEY)
        except FileNotFoundError:
            return [e.model_copy() for e in SEED_MODELS]
        try:
            d = json.loads(body)
            raw = d["models"] if isinstance(d, dict) else None
            if not isinstance(raw, list):
                raise ValueError("models is not a list")
            return [ModelEntry(**e) for e in raw]
        except Exception:
            _log.exception("corrupt model catalog at %s; falling back to seed",
                           CATALOG_KEY)
            return [e.model_copy() for e in SEED_MODELS]

    async def displayed(self) -> list[ModelEntry]:
        """The list for the combobox. The cap is applied here too -- even if the file was
        hand-edited with 6 enabled, the screen contract (at most 5) has to hold."""
        return [e for e in await self.load() if e.display][:MAX_DISPLAYED]

    async def add(self, name: str, model_id: str, display: bool) -> ModelEntry:
        entries = await self._writable()
        if any(e.model_id == model_id for e in entries):
            raise CatalogError("duplicate", f"{model_id} is already registered")
        entry = ModelEntry(name=name, model_id=model_id, display=display)
        entries.append(entry)
        self._check_display_cap(entries)
        await self._save(entries)
        return entry

    async def update(self, model_id: str, *, name: str | None = None,
                     display: bool | None = None) -> ModelEntry:
        entries = await self._writable()
        entry = next((e for e in entries if e.model_id == model_id), None)
        if entry is None:
            raise CatalogError("not_found", f"{model_id} is not registered")
        if name is not None:
            entry.name = name
        if display is not None:
            entry.display = display
        self._check_display_cap(entries)
        await self._save(entries)
        return entry

    async def remove(self, model_id: str) -> None:
        entries = await self._writable()
        kept = [e for e in entries if e.model_id != model_id]
        if len(kept) == len(entries):
            raise CatalogError("not_found", f"{model_id} is not registered")
        await self._save(kept)

    async def _writable(self) -> list[ModelEntry]:
        if self._s3 is None:
            raise CatalogError(
                "readonly",
                "model catalog is read-only without AIPDS_S3_BUCKET")
        return await self.load()

    @staticmethod
    def _check_display_cap(entries: list[ModelEntry]) -> None:
        shown = sum(1 for e in entries if e.display)
        if shown > MAX_DISPLAYED:
            raise CatalogError(
                "too_many_displayed",
                f"at most {MAX_DISPLAYED} models can be displayed "
                f"(now {shown}) — hide one first")

    async def _save(self, entries: list[ModelEntry]) -> None:
        assert self._s3 is not None  # _writable() has already checked
        body = json.dumps({"models": [e.model_dump() for e in entries]},
                          ensure_ascii=False)
        await self._s3.put(CATALOG_KEY, body)
