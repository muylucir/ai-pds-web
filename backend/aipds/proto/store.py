# backend/aipds/proto/store.py — 프로토타입의 정본은 S3다.
#
# **왜 있는가.** 빌드 대화·세션 id·핸드오프·설문은 S3에 있었지만, 정작 빌드된 소스
# 트리와 접근 토큰과 "호스팅 중"이라는 사실은 인스턴스의 로컬 디스크와 메모리에만
# 있었다. 그래서 두 사건이 같은 결과를 냈다:
#
#   - 인스턴스 교체(HostingStack 배포, 장애): 소스가 사라져 카드가 `none`으로 돌아가고,
#     토큰 파일이 사라져 이미 나눠 준 링크가 전부 404가 된다. 명세에서 다시 빌드하면
#     설문을 받았던 그것과 **다른 물건**이 나온다.
#   - 백엔드 재시작: 토큰은 디스크에서 되읽지만 호스팅 레지스트리는 메모리라, 링크는
#     살아 있는데 그 링크가 가리키는 서버가 다시 뜨지 않는다.
#
# 이 모듈이 그 셋의 정본을 S3에 둔다. 로컬 트리는 캐시다 — 없으면 여기서 되살린다.
#
# 키(프로젝트 프리픽스 상대):
#
#   prototypes/{slug}/source/current.json    현재 세대의 요약
#   prototypes/{slug}/source/{gen:06d}/{rel} 그 세대의 파일들(바이트)
#   prototypes/{slug}/access-token           접근 토큰
#   prototypes/{slug}/hosting.json           원하는 상태(호스팅 중이어야 하는가)
#
# 버킷 루트: `surveys/preview-tokens/{sha256(token)}.json` → {project_id, slug}. 공개
# 프리뷰는 요청이 오기 전에는 어느 프로젝트인지 모르므로 색인이 루트에 있어야 한다(설문
# 토큰 색인 `surveys/by-token/`과 같은 이유). 키를 해시로 두는 것은 목록이 토큰 평문을
# 드러내지 않게 하기 위해서다.
#
# **왜 `surveys/` 아래인가.** 백엔드 롤의 S3 권한은 프리픽스 허용목록이다
# (infra/lib/backend-permissions.ts의 BACKEND_BUCKET_PREFIXES). 그 밖의 루트 프리픽스는
# AccessDenied이고, 목록을 넓히려면 HostingStack을 배포해야 한다 — 그 배포는 EC2를
# 교체할 수 있다. `surveys/`는 이미 "버킷 루트의 공개 링크 토큰 색인"의 자리이므로
# 같은 성질의 색인을 그 옆에 둔다.
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from aipds.pathsafe import reject_unsafe
from aipds.project_bundle import source_excluded
from aipds.proto.host import TOKEN_FILENAME
from aipds.s3store import S3StoreLike

_log = logging.getLogger(__name__)

#: 남기는 세대 수. 되돌리기(퍼널 과제)에 쓸 만큼만 — 소스는 작지만 세대마다 통째다.
KEEP_GENERATIONS = 5
_UPLOAD_CONCURRENCY = 8
TOKEN_INDEX_PREFIX = "surveys/preview-tokens/"


def source_prefix(slug: str) -> str:
    return f"prototypes/{slug}/source/"


def _current_key(slug: str) -> str:
    return f"{source_prefix(slug)}current.json"


def _gen_prefix(slug: str, gen: int) -> str:
    return f"{source_prefix(slug)}{gen:06d}/"


def token_key(slug: str) -> str:
    return f"prototypes/{slug}/access-token"


def hosting_key(slug: str) -> str:
    return f"prototypes/{slug}/hosting.json"


def _index_key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{TOKEN_INDEX_PREFIX}{digest}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_entries(build_dir: Path) -> list[tuple[str, bytes]]:
    """빌드 트리의 (상대 경로, 바이트). 제외 규칙은 번들·아카이브와 같은 집합이다
    (`project_bundle.source_excluded`) — `node_modules`·`.next`·호스팅 로그·토큰은
    세대에 담지 않는다."""
    if not build_dir.is_dir():
        return []
    out: list[tuple[str, bytes]] = []
    for path in sorted(build_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(build_dir).as_posix()
        if source_excluded(rel):
            continue
        out.append((rel, path.read_bytes()))
    return out


def _hash_entries(entries: list[tuple[str, bytes]]) -> str:
    h = hashlib.sha256()
    for rel, data in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def has_build_output(build_dir: Path) -> bool:
    """빌드 디렉토리 아래 `prototype/`에 산출물이 있는가.

    "빌드됐다"의 단일 정의다. 세 곳이 이 질문을 하고, 전부 여기를 거쳐야
    한다 -- `first_prompt()`(무엇을 지시할지), `build_complete`
    도구(완료 선언을 받아줄지), 목록 라우트(카드를 built로 보일지). 기준이
    갈라지면 도구는 완료를 받아들이는데 목록은 built로 보이지 않는(또는 그
    반대) 상태가 된다.

    `prototype/`을 보고 빌드 디렉토리 자체를 보지 않는 것이 요점이다.
    `start()`가 에이전트보다 먼저 스펙 .md를 심고, 이전 호스팅 시도가
    `.proto-host.log`/`.pid`를 남길 수 있어서 -- 빌드 디렉토리가 있다는 건
    세션이 시작됐다는 뜻일 뿐 무언가 만들어졌다는 뜻이 아니다.

    직속 자식만 확인하고 재귀하지 않는다: node_modules/.next가 생긴 뒤에도
    매 목록 호출에서 싸게 유지된다.
    """
    proto_dir = build_dir / "prototype"
    try:
        return proto_dir.is_dir() and any(proto_dir.iterdir())
    except OSError:
        return False


class PrototypeStore:
    """한 프로젝트의 프로토타입 정본. `s3`는 프로젝트 스토어, `root`는 버킷 루트."""

    def __init__(self, s3: S3StoreLike, root: S3StoreLike | None = None,
                 project_id: str | None = None) -> None:
        self._s3 = s3
        self._root = root
        self._project_id = project_id

    # ---- 소스 세대 ----

    async def current(self, slug: str) -> dict | None:
        try:
            data = json.loads(await self._s3.get(_current_key(slug)))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("gen"), int) else None

    async def has_source(self, slug: str) -> bool:
        return await self.current(slug) is not None

    async def snapshot(self, slug: str, build_dir: Path, reason: str) -> int | None:
        """로컬 빌드 트리를 새 세대로 올린다. 새 세대 번호, 또는 None.

        None인 경우: 빌드 산출물이 없다(명세만 있는 트리를 세대로 만들면 되살리기가
        "빌드됨"을 거짓으로 만든다), 또는 현재 세대와 내용이 같다.

        `current.json`을 **마지막에** 쓴다. 파일을 올리는 도중 실패하면 반쯤 올라간
        세대는 가리켜지지 않으므로 되살리기는 이전 세대를 쓴다.
        """
        if not has_build_output(build_dir):
            return None
        entries = await asyncio.to_thread(local_entries, build_dir)
        digest = _hash_entries(entries)
        current = await self.current(slug)
        if current is not None and current.get("hash") == digest:
            return None
        gen = (current["gen"] + 1) if current is not None else 1
        prefix = _gen_prefix(slug, gen)
        sem = asyncio.Semaphore(_UPLOAD_CONCURRENCY)

        async def put(rel: str, data: bytes) -> None:
            async with sem:
                await self._s3.put_bytes(prefix + rel, data)

        await asyncio.gather(*(put(rel, data) for rel, data in entries))
        await self._s3.put(_current_key(slug), json.dumps({
            "gen": gen, "hash": digest, "files": len(entries),
            "reason": reason, "created_at": _now()}))
        await self._prune(slug, gen)
        _log.info("prototype source snapshot %s/%s gen=%d files=%d (%s)",
                  self._project_id, slug, gen, len(entries), reason)
        return gen

    async def _prune(self, slug: str, newest: int) -> None:
        keep_from = newest - KEEP_GENERATIONS + 1
        base = source_prefix(slug)
        gens = set()
        for key in await self._s3.list(base):
            head = key[len(base):].split("/", 1)[0]
            if head.isdigit():
                gens.add(int(head))
        for gen in sorted(g for g in gens if g < keep_from):
            await self._s3.delete_prefix(_gen_prefix(slug, gen))

    async def entries(self, slug: str) -> list[tuple[str, bytes]]:
        """현재 세대의 (상대 경로, 바이트). 세대가 없으면 빈 목록."""
        current = await self.current(slug)
        if current is None:
            return []
        prefix = _gen_prefix(slug, current["gen"])
        keys = await self._s3.list(prefix)
        bodies = await asyncio.gather(*(self._s3.get_bytes(k) for k in keys))
        return [(k[len(prefix):], b) for k, b in zip(keys, bodies)]

    async def restore(self, slug: str, build_dir: Path) -> bool:
        """로컬에 빌드 산출물이 없으면 현재 세대로 되살린다. 되살렸으면 True.

        로컬에 산출물이 있으면 건드리지 않는다 — 그쪽이 더 새로울 수 있다(아직 스냅샷
        되지 않은 편집). 토큰 파일도 함께 되살린다: 호스팅이 뜨면 목록이 그 토큰으로
        공유 링크를 만든다.
        """
        restored = False
        if not has_build_output(build_dir):
            files = await self.entries(slug)
            if files:
                def write() -> None:
                    for rel, data in files:
                        reject_unsafe(rel)       # 키는 우리가 썼지만 경로로 쓰기 전에 본다
                        dest = build_dir / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(data)
                await asyncio.to_thread(write)
                restored = True
                _log.info("restored prototype source %s/%s from S3 (%d files)",
                          self._project_id, slug, len(files))
        token = await self.load_token(slug)
        token_file = build_dir / TOKEN_FILENAME
        if token and not token_file.exists():
            build_dir.mkdir(parents=True, exist_ok=True)
            token_file.write_text(token, encoding="utf-8")
        return restored

    # ---- 접근 토큰 ----

    async def load_token(self, slug: str) -> str | None:
        try:
            token = (await self._s3.get(token_key(slug))).strip()
        except FileNotFoundError:
            return None
        return token or None

    async def save_token(self, slug: str, token: str) -> None:
        """토큰과 루트 색인을 쓴다. 이미 같은 값이면 쓰지 않는다(호스팅 재시작마다
        불리므로)."""
        if await self.load_token(slug) == token:
            return
        await self._s3.put(token_key(slug), token)
        if self._root is not None and self._project_id is not None:
            await self._root.put(_index_key(token), json.dumps(
                {"project_id": self._project_id, "slug": slug}))

    async def forget_token(self, slug: str) -> None:
        """루트 색인을 지운다. 리셋·프로젝트 삭제가 `prototypes/{slug}/`를 지우기
        **전에** 불러야 한다 — 토큰을 읽을 곳이 그 아래에 있다."""
        token = await self.load_token(slug)
        if token and self._root is not None:
            await self._root.delete_prefix(_index_key(token))

    # ---- 원하는 상태 ----

    async def set_desired_running(self, slug: str) -> None:
        await self._s3.put(hosting_key(slug), json.dumps(
            {"desired": "running", "since": _now()}))

    async def clear_desired(self, slug: str) -> None:
        await self._s3.delete_prefix(hosting_key(slug))

    async def desired_running(self) -> list[str]:
        slugs = []
        for key in await self._s3.list("prototypes/"):
            head, _, rest = key[len("prototypes/"):].partition("/")
            if rest == "hosting.json":
                slugs.append(head)
        return sorted(slugs)


async def resolve_token(root: S3StoreLike, token: str) -> tuple[str, str] | None:
    """루트 색인으로 토큰 → (project_id, slug). 없으면 None."""
    try:
        data = json.loads(await root.get(_index_key(token)))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    pid, slug = data.get("project_id"), data.get("slug")
    if isinstance(pid, str) and isinstance(slug, str):
        return pid, slug
    return None
