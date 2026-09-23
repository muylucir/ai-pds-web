# backend/aipds/proto/hosting.py — 프로토타입 하나를 호스팅하는 절차.
#
# 호스팅 시작 라우트와 백엔드 기동 뒤 재호스팅이 **같은 절차**여야 한다: 되살리기 →
# 브랜드 동기화 → `npm` 수명 주기 → 스냅샷 → 토큰 → 원하는 상태. 두 벌이면 한쪽이
# 브랜드를 빠뜨리거나 토큰을 새로 만들어, 재시작 뒤 뜬 프로토타입이 라우트로 띄운
# 것과 다르게 보이거나 이미 나눠 준 링크가 죽는다.
#
# `ProtoHost`는 S3를 모르는 범용 호스팅으로 남는다. S3가 끼는 일(proto/store.py)은
# 이 모듈이 그 앞뒤에서 한다.
from __future__ import annotations

import logging

from aipds.proto.design_sync import sync_design, theme_copies
from aipds.proto.host import HostInfo
from aipds.proto.store import PrototypeStore

_log = logging.getLogger(__name__)


def prototype_store(pid: str) -> PrototypeStore | None:
    """이 프로젝트의 프로토타입 정본. 버킷이 없으면(로컬 개발) None."""
    import aipds.app as app_module
    if not app_module.durable_projects_enabled():
        return None
    return PrototypeStore(app_module.s3_store_factory(pid),
                          root=app_module.surveys_root_s3_factory(),
                          project_id=pid)


async def host_prototype(pid: str, slug: str) -> tuple[HostInfo, str | None]:
    """호스팅을 시작한다. (HostInfo, 접근 토큰 — 실패면 None).

    빌드 트리가 없으면 `FileNotFoundError`(라우트가 404로 옮긴다).
    """
    import aipds.app as app_module
    from aipds.routes.proto_public import public_base_path

    host = app_module.proto_host()
    build_dir = app_module._proto_root() / pid / slug
    store = prototype_store(pid)

    # 로컬 트리가 없으면(교체된 인스턴스) S3의 현재 세대로 되살린다.
    if store is not None:
        try:
            await store.restore(slug, build_dir)
        except Exception:
            _log.exception("prototype source restore before host failed: %s/%s",
                           pid, slug)

    # 리빌드 직전에 브랜드 테마를 갱신한다. 호스팅은 rmtree 없이 기존 트리에
    # `npm run build`를 돌리므로(proto/host.py), 여기서 파일만 새로 쓰면 코드는
    # 한 줄도 건드리지 않고 색·서체·라운드만 바뀐다 -- 이미 완료된 프로토타입이
    # 개선 세션 없이 리브랜딩되는 유일한 경로다.
    try:
        profile = await app_module.design_profile_store().load()
        sync_design(build_dir, profile, app_module.project_language(pid))
        # sync_design은 "갱신"만 한다 -- 프로필 업로드 **이전에** 빌드된
        # 프로토타입은 prototype/ 아래에 테마 사본이 없어 아무것도 갈지 않고,
        # 재호스팅해도 그대로 무브랜드로 남는다. 운영자가 "왜 아무 일도 안
        # 일어났는지"를 이 시점에 알 수 있어야 한다 -- 개선 세션을 한 번 열어야
        # 반영된다는 뜻이다.
        if profile is not None and not theme_copies(build_dir):
            _log.warning(
                "design profile present but %s/%s has no theme copy under "
                "prototype/ -- re-hosting cannot re-brand it; an improvement "
                "session must run once to import aipds-theme.css first",
                pid, slug)
    except Exception:
        # 브랜드 반영 실패가 호스팅 자체를 막지는 않는다 -- 화면이 열리는 것이
        # 색보다 우선이다. 원인은 로그에 남는다.
        _log.exception("design sync before host failed: %s/%s", pid, slug)

    info = await host.start(
        # cwd를 명시한다: ProtoHost의 기본값은 {root}/{pid}/{slug}로 서빙할 트리보다
        # 한 단계 위다(명세 .md만 있는 자리 — npm이 package.json ENOENT로 죽는다).
        pid, slug, cwd=build_dir / "prototype",
        # `public_base_path`이지 `proxy_prefix`가 아니다: basePath는 **브라우저가**
        # 해석하는 asset URL에 박히고, 브라우저의 경로에는 이 앱이 보기 전에 벗겨지는
        # `/api` 마운트가 있다.
        base_path=public_base_path(pid, slug),
        # 빌드 에이전트·Discovery와 같은 출처(app.project_model) — 프로토타입 앱의
        # 런타임 LLM 호출도 프로젝트가 고른 모델로 돌아야 한다.
        model_id=app_module.project_model(pid))
    if info.state != "running":
        return info, None

    # 접근 토큰은 **호스팅이 실제로 뜬 뒤에만** 발급한다. `ensure_token`이므로
    # stop -> start를 반복해도 값이 그대로다 — 워크숍 중 호스팅을 껐다 켜는 것
    # 때문에 이미 나눠 준 링크가 죽으면 안 된다. 폐기하는 의도된 경로는 리셋이다.
    token = host.ensure_token(pid, slug)
    if store is not None:
        # 셋 다 best-effort다: 호스팅은 이미 떴고 사용자는 링크를 받는다. 실패하면
        # 교체·재시작을 견디지 못할 뿐이고, 원인은 로그에 남는다.
        for what, step in (("token", store.save_token(slug, token)),
                           ("desired state", store.set_desired_running(slug)),
                           ("snapshot", store.snapshot(slug, build_dir, "host"))):
            try:
                await step
            except Exception:
                _log.exception("prototype %s write after host failed: %s/%s",
                               what, pid, slug)
    return info, token


async def stop_prototype(pid: str, slug: str) -> None:
    """호스팅을 멈추고 원하는 상태를 지운다 — 재시작 뒤 다시 뜨지 않게."""
    import aipds.app as app_module
    await app_module.proto_host().stop(pid, slug)
    store = prototype_store(pid)
    if store is not None:
        await store.clear_desired(slug)


async def adopt_local_state(previously_running: list[tuple[str, str]]) -> None:
    """로컬에만 있는 것을 S3 정본으로 옮긴다(기동 때, 재호스팅 전에).

    두 경우를 덮는다:

    - 직전 백엔드가 호스팅하던 것(`ProtoHost.previously_running`) → 원하는 상태.
      원하는 상태를 기록하기 전에 뜬 호스팅이나, 기록이 실패한 호스팅도 재시작 뒤
      다시 뜬다. 사용자가 멈춘 것은 pid 파일이 없으므로 여기 들지 않는다.
    - S3 세대가 없는 로컬 빌드 → 스냅샷. 이 저장소가 생기기 전에 만든 프로토타입도
      다음 교체를 견딘다. 내용이 같으면 새 세대를 만들지 않으므로 매 기동이 싸다.
    """
    import aipds.app as app_module
    host = app_module.proto_host()
    root = app_module._proto_root()
    for pid, slug in previously_running:
        store = prototype_store(pid)
        if store is None or not app_module.registry.is_registered(pid):
            continue
        try:
            await store.set_desired_running(slug)
        except Exception:
            _log.exception("recording desired state for %s/%s failed", pid, slug)
    for pid in app_module.registry.list_ids():
        store = prototype_store(pid)
        if store is None:
            return
        for slug in host.slugs(pid):
            try:
                if not await store.has_source(slug):
                    await store.snapshot(slug, root / pid / slug, "backfill")
            except Exception:
                _log.exception("backfilling prototype source %s/%s failed", pid, slug)


async def rehost_desired() -> int:
    """원하는 상태가 running인 프로토타입을 **하나씩** 다시 호스팅한다. 띄운 수.

    백엔드가 뜰 때 백그라운드로 돈다. 순차인 이유: 호스팅 한 번이 `npm install` +
    `next build`(피크 약 2GB)라 한꺼번에 올리면 기동 직후 OOM이 난다. 실패한 것은
    건너뛰고 로그에 남긴다 — 하나가 나머지를 막지 않는다.
    """
    import aipds.app as app_module
    host = app_module.proto_host()
    started = 0
    for pid in app_module.registry.list_ids():
        store = prototype_store(pid)
        if store is None:
            return 0
        try:
            slugs = await store.desired_running()
        except Exception:
            _log.exception("reading desired hosting state failed for %s", pid)
            continue
        for slug in slugs:
            status = host.status(pid, slug)
            if status is not None and status.state == "running":
                continue
            try:
                info, _ = await host_prototype(pid, slug)
            except FileNotFoundError:
                # 되살릴 소스가 없다 — 원하는 상태를 남겨 둘 이유가 없다.
                _log.warning("no source to re-host %s/%s; clearing desired state",
                             pid, slug)
                await store.clear_desired(slug)
                continue
            except Exception:
                _log.exception("re-hosting %s/%s failed", pid, slug)
                continue
            if info.state == "running":
                started += 1
                _log.info("re-hosted %s/%s on :%s", pid, slug, info.port)
            else:
                _log.warning("re-hosting %s/%s ended %s", pid, slug, info.state)
    return started
