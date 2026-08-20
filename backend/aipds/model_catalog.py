"""모델 카탈로그 — 프로젝트 생성 화면이 고를 수 있는 모델 목록.

버킷 루트의 `models/catalog.json`에 산다. projects/ 밖에 두는 이유는
카탈로그가 프로젝트보다 먼저 존재해야 하기 때문이다(프로젝트 생성 화면이
프로젝트가 없는 상태에서 이것을 읽는다) — surveys/by-token/이 프로젝트
프리픽스 밖에 있는 것과 같은 이유다.

시드 목록은 **읽기 시점의 폴백일 뿐 파일로 쓰지 않는다.** 배포 직후 관리자가
아무것도 하지 않아도 콤보박스가 채워져야 하고, 반대로 '빈 카탈로그'를 유효
상태로 두면 첫 프로젝트 생성이 막힌다 — 시드는 편의가 아니라 부트스트랩
경로다. 관리자가 처음 수정할 때 비로소 파일이 생긴다.

표시 상한(5)은 등록이 아니라 `display`에만 적용된다: 관리자는 여러 모델을
등록해 두고 그중 5개만 화면에 노출한다. 상한을 등록에 두면 요구사항이
성립하지 않는다.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel

from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: 버킷 루트 기준 키. projects/·sessions/·surveys/ 옆의 네 번째 프리픽스다.
CATALOG_KEY = "models/catalog.json"

#: 콤보박스에 동시에 띄울 수 있는 모델 수. 등록 수와는 무관하다.
MAX_DISPLAYED = 5


class ModelEntry(BaseModel):
    name: str
    model_id: str
    display: bool = True


class CatalogError(Exception):
    """카탈로그 정책 위반. `code`가 라우트의 HTTP 상태로 번역된다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


#: ap-northeast-2에서 네 개 모두 ACTIVE인 것을 list-inference-profiles로 실측
#: 확인했다. 배포 기본값(backend-permissions.ts의 MODEL = opus-4-8)은 여기
#: 없다 — 의도된 것이다: 이 기능 이전에 만든 프로젝트와 모델 미지정 시의
#: 폴백으로만 쓰이고 콤보박스에는 뜨지 않는다.
SEED_MODELS: tuple[ModelEntry, ...] = (
    ModelEntry(name="Opus 5", model_id="global.anthropic.claude-opus-5"),
    ModelEntry(name="Opus 4.6", model_id="global.anthropic.claude-opus-4-6-v1"),
    ModelEntry(name="Sonnet 5", model_id="global.anthropic.claude-sonnet-5"),
    ModelEntry(name="Sonnet 4.6", model_id="global.anthropic.claude-sonnet-4-6"),
)


class ModelCatalog:
    """카탈로그의 읽기/쓰기. `s3`가 None이면 읽기 전용(버킷 미설정)."""

    def __init__(self, s3: S3StoreLike | None) -> None:
        self._s3 = s3

    async def load(self) -> list[ModelEntry]:
        """등록된 전체 목록. 파일이 없거나 손상됐으면 시드로 떨어진다.

        손상을 예외로 올리지 않는 이유: 카탈로그를 읽지 못하면 프로젝트 생성이
        전부 막힌다. 시드로 떨어지면 워크숍은 계속 돌고, 원인은 로그에 남는다.
        """
        # model_copy()로 복사한다 — list(SEED_MODELS)는 리스트만 새로 만들고
        # ModelEntry 객체는 공유하므로, update()의 제자리 변경이 모듈 전역
        # 상수를 영구히 오염시킨다(실측: 표시 토글 한 번이 프로세스 내내 남는다).
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
        """콤보박스에 띄울 목록. 상한을 여기서도 자른다 — 파일이 손으로
        편집되어 6개가 켜져 있어도 화면 계약(최대 5개)은 지켜져야 한다."""
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
        assert self._s3 is not None  # _writable()이 이미 확인했다
        body = json.dumps({"models": [e.model_dump() for e in entries]},
                          ensure_ascii=False)
        await self._s3.put(CATALOG_KEY, body)
