"""Model catalog — the list of models the project creation screen can choose from.

It lives at `models/catalog.json` at the bucket root. It sits outside projects/ because
the catalog must exist before any project does (the project creation screen reads it
when there are no projects) — the same reason surveys/by-token/ sits outside the project
prefix.

The seed list is **only a fallback at read time and is never written to a file.** Right after deploy, the combo box
must be populated without the admin doing anything, and conversely, treating 'empty catalog' as a valid
state would block the first project creation — the seed is a bootstrap path, not a
convenience. The file is created only when an admin first edits it.

The display cap (5) applies only to `display`, not to registration: admins register many models
and expose only 5 of them on screen. Putting the cap on registration would make the requirement
unworkable.

**An entry is a pair of model and effort.** Effort sets the model's depth of thinking and amount of output, and by
observation it changes turn time as much as the model choice does (Opus 5.5 medium → high: turn time +53%, output +50%). So
it is decided in the same place as "which model to use". None is the CLI default — medium for Opus 5.5,
high for the other current-generation models. A project copies this value along with the model id at creation time.

**List order is combo box order.** There is no separate sort key — with only the file's array order,
there is no place for "order" and "sort key" to disagree. The first entry is also the default
selection on the project creation screen (CreateProjectForm picks `list[0]`).
"""
from __future__ import annotations

import json
import logging
from typing import Literal, get_args

from pydantic import BaseModel

from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: Key relative to the bucket root. The fourth prefix, next to projects/, sessions/ and surveys/.
CATALOG_KEY = "models/catalog.json"

#: Number of models that can be shown in the combo box at once. Unrelated to the number registered.
MAX_DISPLAYED = 5


#: Claude Code CLI `--effort` values (SDK option `effort`). The CLI rejects any other value.
Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORTS: tuple[str, ...] = get_args(Effort)


class ModelEntry(BaseModel):
    name: str
    model_id: str
    display: bool = True
    #: None = CLI default. Catalog files without this field are still read as is.
    effort: Effort | None = None


class CatalogError(Exception):
    """Catalog policy violation. `code` is translated into the route's HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: All four were confirmed ACTIVE in ap-northeast-2 with list-inference-profiles
#: (2026-10-03). The deploy default (MODEL = opus-4-8 in backend-permissions.ts) is
#: not here — intentionally: it is used only for projects created before this feature and as the
#: fallback when no model is specified, and it does not appear in the combo box.
#:
#: Basis for effort (2026-10-03 Discovery experiment, 108 turns): Opus 5.5 at medium is similar in quality to high
#: with 2/3 of the turn time. Sonnet 5.5 medium consistently fell behind on methodology adherence,
#: so it uses high. The 5.0 generation uses high, the same as the CLI default.
#: Sonnet 5.5 needs bundled CLI 2.1.286 (claude-agent-sdk 0.2.163) or later —
#: below that the turn does not start, with `unrecognized_model` (the lower bound in pyproject.toml).
SEED_MODELS: tuple[ModelEntry, ...] = (
    ModelEntry(name="Opus 5.5", model_id="global.anthropic.claude-opus-5-5", effort="medium"),
    ModelEntry(name="Sonnet 5.5", model_id="global.anthropic.claude-sonnet-5-5", effort="high"),
    ModelEntry(name="Opus 5.0", model_id="global.anthropic.claude-opus-5", effort="high"),
    ModelEntry(name="Sonnet 5.0", model_id="global.anthropic.claude-sonnet-5", effort="high"),
)

#: Distinguishes "leave effort untouched" from "clear effort (None)" in update().
_UNSET: object = object()


class ModelCatalog:
    """Reading/writing the catalog. Read-only if `s3` is None (bucket not configured)."""

    def __init__(self, s3: S3StoreLike | None) -> None:
        self._s3 = s3

    async def load(self) -> list[ModelEntry]:
        """The full registered list. Falls back to the seed if the file is missing or corrupt.

        Why corruption is not raised as an exception: if the catalog cannot be read, all project creation
        is blocked. Falling back to the seed keeps the workshop running, and the cause stays in the log.
        """
        # Copy with model_copy() — list(SEED_MODELS) creates only a new list and
        # shares the ModelEntry objects, so update()'s in-place changes would permanently pollute the
        # module-global constant (observed: one display toggle persists for the life of the process).
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

    async def effort_of(self, model_id: str) -> str | None:
        """This model's effort. None (CLI default) if not in the catalog or unspecified."""
        entry = next((e for e in await self.load() if e.model_id == model_id), None)
        return entry.effort if entry else None

    async def displayed(self) -> list[ModelEntry]:
        """The list to show in the combo box. The cap is applied here too — even if the file was hand-edited
        to turn on 6, the screen contract (at most 5) must hold."""
        return [e for e in await self.load() if e.display][:MAX_DISPLAYED]

    async def add(self, name: str, model_id: str, display: bool,
                  effort: str | None = None) -> ModelEntry:
        entries = await self._writable()
        if any(e.model_id == model_id for e in entries):
            raise CatalogError("duplicate", f"{model_id} is already registered")
        entry = ModelEntry(name=name, model_id=model_id, display=display, effort=effort)
        entries.append(entry)
        self._check_display_cap(entries)
        await self._save(entries)
        return entry

    async def update(self, model_id: str, *, name: str | None = None,
                     display: bool | None = None,
                     effort: object = _UNSET) -> ModelEntry:
        """If `effort` is not passed it is left as is; passing None clears it to the CLI default."""
        entries = await self._writable()
        entry = next((e for e in entries if e.model_id == model_id), None)
        if entry is None:
            raise CatalogError("not_found", f"{model_id} is not registered")
        if name is not None:
            entry.name = name
        if display is not None:
            entry.display = display
        if effort is not _UNSET:
            entry.effort = effort  # type: ignore[assignment]  # the route validates allowed values
        self._check_display_cap(entries)
        await self._save(entries)
        return entry

    async def reorder(self, model_ids: list[str]) -> list[ModelEntry]:
        """Saves all registered models again in `model_ids` order.

        **Accepts only a full permutation.** If the list the admin screen saw differs from the stored list
        (added or deleted in another tab), it is rejected — dropping unknown ids or appending missing ids
        at the end would silently save an order the admin never saw.
        """
        entries = await self._writable()
        by_id = {e.model_id: e for e in entries}
        if len(model_ids) != len(entries) or set(model_ids) != set(by_id):
            raise CatalogError(
                "stale", "the model list changed since it was loaded — reload it")
        ordered = [by_id[i] for i in model_ids]
        await self._save(ordered)
        return ordered

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
        assert self._s3 is not None  # _writable() already checked
        body = json.dumps({"models": [e.model_dump() for e in entries]},
                          ensure_ascii=False)
        await self._s3.put(CATALOG_KEY, body)
