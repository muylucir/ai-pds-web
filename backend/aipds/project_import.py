# backend/aipds/project_import.py — 번들 zip 하나를 살아 있는 프로젝트로.
#
# 포맷은 project_bundle.py가 정하고, 이 모듈은 그 포맷을 **푸는** IO다.
# 프로젝트를 등록하고 매니페스트를 쓰는 것은 이 모듈이 하지 않는다 — 라우트가 한다.
# 그 순서(내용 먼저, project.json 마지막, 등록은 그 다음)가 이 기능의 실패 모드를
# 정하므로 한 곳에서 읽혀야 하고, 그 자리는 HTTP 계약을 가진 쪽이다.
#
# **되돌리기는 우리가 쓴 키만 지운다.** `projects/{pid}/` 를 통째로 지우는 편이
# 짧지만(delete_project_data가 그렇게 한다) 그러면 "이 prefix는 우리 것"이라는
# 전제에 기대게 된다. 그 전제는 라우트의 409 게이트와 실제 쓰기 사이에서 깨질 수
# 있다: 그 사이에 누군가 같은 id로 프로젝트를 만들면(생성 라우트는 등록 여부만
# 보고, 임포트는 아직 등록하지 않았다) 되돌리기가 남의 프로젝트를 지운다. 쓴 것을
# 기억하면 그 레이스가 사라진다.
from __future__ import annotations

import asyncio
import json
import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

from aipds.agent.session_store import project_transcript_prefix
from aipds.pathsafe import reject_unsafe_segment
from aipds.project_bundle import (
    MANIFEST_NAME,
    MAX_ENTRIES,
    MAX_ENTRY_BYTES,
    MAX_TOTAL_BYTES,
    BundleError,
    bundle_path_to_s3_key,
    parse_proto_source_path,
    safe_entry_name,
)
from aipds.proto.store import PrototypeStore, source_prefix
from aipds.s3store import S3StoreLike
from aipds.survey.store import TOKEN_INDEX_PREFIX, new_token

_log = logging.getLogger(__name__)

#: 한 번에 풀어서 올리는 엔트리 수. project_export._FETCH_BATCH와 같은 이유로 같은
#: 크기다 — 전부 모으면 메모리가 상한이 되고, 하나씩 올리면 S3 왕복이 벽시계가 된다.
_WRITE_BATCH = 32

#: 경고 코드. `error_codes.py`와 같은 규율이다 — 백엔드는 UI 언어를 모르므로 문구를
#: 만들지 않고 코드를 보낸다(프론트의 딕셔너리가 문구를 소유한다).
WARN_MODEL_UNAVAILABLE = "model_unavailable"
WARN_SURVEY_TOKENS_REISSUED = "survey_tokens_reissued"  # nosec B105
WARN_UNKNOWN_ENTRIES = "unknown_entries"


@dataclass
class ImportedData:
    """임포트가 놓은 것과, 그것을 되돌리는 방법.

    `discard()`를 결과에 실어 보내는 이유: 임포트는 이 모듈이 끝나고도 한 걸음
    남아 있다 — 라우트가 `project.json`을 쓰고 등록한다. 그 걸음이 실패했을 때
    되돌릴 것은 **여기서 쓴 것**이므로, 되돌리는 방법도 여기가 알아야 한다.
    라우트가 키 목록을 받아 스스로 지우게 하면 그 규칙이 두 곳으로 갈라진다.
    """

    counts: dict
    warnings: list[dict]
    _keys: list[str]
    _paths: list[Path]
    _tokens: list[str]
    _s3: S3StoreLike
    _surveys_root: S3StoreLike
    _stop_at: Path

    async def discard(self) -> None:
        await _discard(self._keys, self._paths, self._tokens, s3=self._s3,
                       surveys_root=self._surveys_root, stop_at=self._stop_at)


async def apply_bundle(bundle: Path, *, project_id: str, s3: S3StoreLike,
                       surveys_root: S3StoreLike,
                       proto_root: Path) -> ImportedData:
    """번들의 **내용**을 대상 프로젝트 자리에 놓는다.

    어떤 단계에서 실패하든 자기가 쓴 것을 되돌리고 예외를 다시 올린다 — 반쯤
    들어온 프로젝트는 화면에서 정상과 구별되지 않기 때문이다.
    """
    transcript_prefix = project_transcript_prefix(project_id)
    written_keys: list[str] = []
    written_paths: list[Path] = []
    registered_tokens: list[str] = []

    try:
        with zipfile.ZipFile(bundle) as zf:
            plan = _plan(zf, transcript_prefix=transcript_prefix)
            counts = {
                "objects": len(plan.s3),
                "source_files": len(plan.source),
                "prototypes": len({slug for slug, _, _ in plan.source}),
            }
            await _write_s3(zf, plan.s3, s3=s3, written=written_keys)
            await _write_source(zf, plan.source, project_id=project_id,
                                proto_root=proto_root, written=written_paths)
            # 가져온 소스를 S3 세대로도 올린다 — 정본은 S3다(proto/store.py). 로컬에만
            # 두면 가져온 프로젝트가 다음 인스턴스 교체에서 소스를 잃는다. 세대 키도
            # `written_keys`에 넣어 실패하면 함께 되돌린다.
            store = PrototypeStore(s3, project_id=project_id)
            for slug in sorted({slug for slug, _, _ in plan.source}):
                await store.snapshot(slug, proto_root / project_id / slug, "import")
                written_keys.extend(await s3.list(source_prefix(slug)))
            reissued = await _reissue_survey_tokens(
                written_keys, project_id=project_id, s3=s3,
                surveys_root=surveys_root, registered=registered_tokens)
    except BaseException:
        await _discard(written_keys, written_paths, registered_tokens,
                       s3=s3, surveys_root=surveys_root, stop_at=proto_root)
        raise

    warnings: list[dict] = []
    if reissued:
        warnings.append({"code": WARN_SURVEY_TOKENS_REISSUED, "count": reissued})
    if plan.unknown:
        warnings.append({"code": WARN_UNKNOWN_ENTRIES, "count": plan.unknown})
    _log.info("imported project %s: %d objects, %d source file(s), "
              "%d survey token(s) reissued",
              project_id, counts["objects"], counts["source_files"], reissued)
    return ImportedData(counts=counts, warnings=warnings, _keys=written_keys,
                        _paths=written_paths, _tokens=registered_tokens,
                        _s3=s3, _surveys_root=surveys_root, _stop_at=proto_root)


# ---- 계획 ----


class _Plan:
    """엔트리를 목적지별로 나눈 결과. 쓰기 전에 전부 검증하는 것이 요점이다 —
    절반 쓴 뒤에 위험한 엔트리를 발견하면 되돌릴 것이 늘어난다."""

    def __init__(self) -> None:
        #: (엔트리 이름, S3 키)
        self.s3: list[tuple[str, str]] = []
        #: (slug, 엔트리 이름, 빌드 트리 상대 경로)
        self.source: list[tuple[str, str, str]] = []
        self.unknown = 0


def _plan(zf: zipfile.ZipFile, *, transcript_prefix: str) -> _Plan:
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > MAX_ENTRIES:
        raise BundleError(f"bundle has too many entries ({len(infos)})")
    total = 0
    plan = _Plan()
    for info in infos:
        if info.file_size > MAX_ENTRY_BYTES:
            raise BundleError(f"bundle entry too large: {info.filename}")
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            raise BundleError("bundle expands beyond the size limit")
        name = info.filename
        if name == MANIFEST_NAME:
            continue
        safe_entry_name(name)
        key = bundle_path_to_s3_key(name, transcript_prefix=transcript_prefix)
        if key is not None:
            plan.s3.append((name, key))
            continue
        parsed = parse_proto_source_path(name)
        if parsed is not None:
            slug, rel = parsed
            # slug는 로컬 디렉터리 이름이 된다. `safe_entry_name`이 경로 전체의
            # 탈출을 막았지만 그것과 별개로 slug 하나가 `.` 이면
            # `{root}/{pid}/{slug}` 가 `{root}/{pid}` 로 접히고, 그 뒤의 정리가
            # 형제 프로토타입을 함께 지운다(pathsafe.reject_unsafe_segment 참고).
            try:
                reject_unsafe_segment(slug)
            except ValueError as e:
                raise BundleError(str(e)) from e
            plan.source.append((slug, name, rel))
            continue
        # 우리가 모르는 자리의 엔트리. 매니페스트 게이트를 통과했으니 이 zip은
        # 우리 것이고(kind + schema_version), 그래서 거절보다 건너뛰기가 맞다 —
        # 사용자가 zip을 다시 압축하며 붙은 `__MACOSX/` 같은 것이 흔하다. 다만
        # **조용히** 넘기지 않는다: 개수를 경고로 올려 화면이 말하게 한다.
        _log.warning("skipping unrecognised bundle entry: %s", name)
        plan.unknown += 1
    return plan


# ---- 쓰기 ----


async def _write_s3(zf: zipfile.ZipFile, entries: list[tuple[str, str]], *,
                    s3: S3StoreLike, written: list[str]) -> None:
    for start in range(0, len(entries), _WRITE_BATCH):
        batch = entries[start:start + _WRITE_BATCH]
        bodies = await asyncio.to_thread(
            lambda b=batch: [zf.read(name) for name, _ in b])
        await asyncio.gather(*(s3.put_bytes(key, body)
                              for (_, key), body in zip(batch, bodies)))
        written.extend(key for _, key in batch)


async def _write_source(zf: zipfile.ZipFile, entries: list[tuple[str, str, str]],
                        *, project_id: str, proto_root: Path,
                        written: list[Path]) -> None:
    """프로토타입 소스를 로컬 빌드 트리에 놓는다.

    `{proto_root}/{pid}/{slug}/prototype/` 이 비어 있지 않으면 카드가 곧바로
    `built`로 보이고(proto/session.has_build_output) 호스팅 버튼이 npm 수명주기를
    돌린다. node_modules는 번들에 없으므로 그 install이 채운다.
    """
    def _write(batch: list[tuple[str, str, str]]) -> list[Path]:
        out: list[Path] = []
        for slug, name, rel in batch:
            dest = proto_root / project_id / slug / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(name))
            out.append(dest)
        return out

    for start in range(0, len(entries), _WRITE_BATCH):
        batch = entries[start:start + _WRITE_BATCH]
        written.extend(await asyncio.to_thread(_write, batch))


# ---- 설문 토큰 ----

_LIVE_QUESTIONNAIRE = "/survey/questionnaire.json"
_ARCHIVED_QUESTIONNAIRE = "/questionnaire.json"


async def _reissue_survey_tokens(keys: list[str], *, project_id: str,
                                 s3: S3StoreLike, surveys_root: S3StoreLike,
                                 registered: list[str]) -> int:
    """설문 정의의 `token`과 `project_id`를 다시 쓰고 인덱스를 등록한다.

    **원본 토큰을 그대로 쓸 수 없다.** `surveys/by-token/{token}.json`은 버킷
    **루트**에 있고 `{project_id, slug}`를 가리키는 일방향 인덱스다. 같은 버킷
    안에서 임포트하면(프로젝트 복제가 그렇다) 원본과 같은 키를 덮어써서 **이미
    배포된 원본 설문 링크가 새 프로젝트로 넘어간다** — 응답이 엉뚱한 프로젝트에
    쌓이고, 그 사실은 어디에도 에러로 남지 않는다.

    아카이브된 회차도 토큰 값을 새로 바꾸지만 **인덱스는 등록하지 않는다**. 지난
    회차의 링크가 살아 있을 이유가 없고, 원본 토큰을 남기면 그것이 원본 인스턴스의
    링크와 같은 값이 된다. `SurveyStore.purge`의 `_collect_tokens`는 등록되지 않은
    토큰의 인덱스를 지우려 하지만 없는 키에 대한 `delete_prefix`는 0을 돌려주므로
    무해하고, 그쪽이 요구하는 것(정의에서 토큰을 **읽을 수** 있다)은 지켜진다.
    """
    reissued = 0
    for key in keys:
        live = key.endswith(_LIVE_QUESTIONNAIRE)
        if not live and not (key.endswith(_ARCHIVED_QUESTIONNAIRE)
                             and "/survey/archive/" in key):
            continue
        try:
            data = json.loads(await s3.get(key))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # 정의를 읽을 수 없으면 토큰을 바꿀 수도 없다. 그대로 두면 원본의
            # 토큰이 살아남으므로 임포트 전체를 실패시킨다(호출부가 되돌린다).
            raise BundleError(f"unreadable questionnaire in bundle: {key}") from e
        if not isinstance(data, dict):
            raise BundleError(f"unreadable questionnaire in bundle: {key}")
        token = new_token()
        data["token"] = token
        data["project_id"] = project_id
        slug = data.get("slug")
        await s3.put(key, json.dumps(data, ensure_ascii=False))
        reissued += 1
        if live and isinstance(slug, str) and slug:
            # 인덱스를 정의보다 **나중에** 쓴다. save_questionnaire는 반대 순서인데
            # (인덱스 먼저) 그쪽은 "정의는 있는데 인덱스가 없는" 회수 불가 상태를
            # 피하려는 것이다. 여기서는 실패 시 되돌리기가 둘 다 지우므로 그 위험이
            # 없고, 대신 되돌릴 목록에 담긴 뒤에 쓰는 것이 중요하다.
            await surveys_root.put(
                f"{TOKEN_INDEX_PREFIX}{token}.json",
                json.dumps({"project_id": project_id, "slug": slug}))
            registered.append(token)
    return reissued


# ---- 되돌리기 ----


async def _discard(keys: list[str], paths: list[Path], tokens: list[str], *,
                   s3: S3StoreLike, surveys_root: S3StoreLike,
                   stop_at: Path) -> None:
    """실패한 임포트가 쓴 것을 지운다. 베스트에포트 — 여기서 던지면 원래의
    실패 원인이 가려진다."""
    for token in tokens:
        try:
            await surveys_root.delete_prefix(f"{TOKEN_INDEX_PREFIX}{token}.json")
        except Exception:
            _log.warning("could not roll back survey token index", exc_info=True)
    # `delete_prefix`에 전체 키를 넘기는 것은 단일 키 삭제의 확립된 관례다
    # (S3StoreLike에 단일 delete가 없다 — survey/store.py의 purge가 같은 모양이다).
    results = await asyncio.gather(*(s3.delete_prefix(key) for key in keys),
                                   return_exceptions=True)
    failed = sum(1 for r in results if isinstance(r, BaseException))
    if failed:
        _log.warning("import rollback left %d/%d object(s) behind",
                     failed, len(keys))
    _discard_paths(paths, stop_at=stop_at)


def _discard_paths(paths: list[Path], *, stop_at: Path) -> None:
    """쓴 파일을 지우고 그 때문에 빈 디렉터리가 된 것을 위로 걷어낸다.

    `{proto_root}/{pid}` 를 통째로 rmtree하지 않는 이유는 S3 쪽과 같다: 이 pid로
    이전에 실패한 임포트나 삭제된 프로젝트의 잔재가 있을 수 있고, 그것까지
    지우는 것은 이 함수가 약속한 범위 밖이다.

    `stop_at`이 위로 걷는 경계다(프로토타입 루트). 없으면 이 루프는 루트가
    비었을 때 그 부모까지 계속 올라간다 — 정리가 상위 디렉터리를 지우기 시작하는
    것은 정리가 아니다.
    """
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            _log.warning("could not roll back %s", path, exc_info=True)
    for directory in sorted({p.parent for p in paths},
                            key=lambda d: len(d.parts), reverse=True):
        current = directory
        while current != stop_at and stop_at in current.parents:
            try:
                current.rmdir()
            except OSError:
                break  # 비어 있지 않거나 없다 — 어느 쪽이든 여기서 멈춘다
            current = current.parent
