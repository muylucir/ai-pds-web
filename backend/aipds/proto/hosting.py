# backend/aipds/proto/hosting.py — the procedure for hosting one prototype.
#
# The hosting start route and re-hosting after backend startup must follow **the same
# procedure**: restore → brand sync → `npm` lifecycle → snapshot → token → desired state. With
# two copies, one side would miss the brand or mint a new token, so a prototype brought up after
# a restart would look different from one launched via the route, or links already handed out would die.
#
# `ProtoHost` remains generic hosting that knows nothing about S3. The S3-related work
# (proto/store.py) is done by this module before and after it.
from __future__ import annotations

import logging

from aipds.proto.design_sync import sync_design, theme_copies
from aipds.proto.host import HostInfo
from aipds.proto.store import PrototypeStore

_log = logging.getLogger(__name__)


async def restore_local(pid: str, slug: str, build_dir, store: PrototypeStore) -> None:
    """Restore from the source of truth what is not present locally — build output and access tokens.

    Tokens are planted in hosting's state tree (`ProtoHost.adopt_token`). On a replaced instance,
    there must be a local copy for the list to build share links and for the next hosting not to
    mint a new token — minting a new one kills the links already handed out.
    """
    import aipds.app as app_module
    await store.restore(slug, build_dir)
    token = await store.load_token(slug)
    host = app_module.proto_host()
    if token and host.token_for(pid, slug) is None:
        host.adopt_token(token, pid, slug)


def prototype_store(pid: str) -> PrototypeStore | None:
    """This project's prototype source of truth. None if there is no bucket (local development)."""
    import aipds.app as app_module
    if not app_module.durable_projects_enabled():
        return None
    return PrototypeStore(app_module.s3_store_factory(pid),
                          root=app_module.surveys_root_s3_factory(),
                          project_id=pid)


async def host_prototype(pid: str, slug: str) -> tuple[HostInfo, str | None]:
    """Start hosting. (HostInfo, access token — None on failure).

    If there is no build tree, `FileNotFoundError` (the route maps it to a 404).
    """
    import aipds.app as app_module
    from aipds.routes.proto_public import public_base_path

    host = app_module.proto_host()
    build_dir = app_module._proto_root() / pid / slug
    store = prototype_store(pid)

    # If there is no local tree (a replaced instance), restore it from S3's current generation.
    if store is not None:
        try:
            await restore_local(pid, slug, build_dir, store)
        except Exception:
            _log.exception("prototype source restore before host failed: %s/%s",
                           pid, slug)

    # Refresh the brand theme right before the rebuild. Hosting runs `npm run build` on the
    # existing tree without an rmtree (proto/host.py), so just rewriting the files here changes
    # only colors, typefaces, and rounding without touching a single line of code -- it is the only
    # path for an already completed prototype to be rebranded without an improvement session.
    try:
        profile = await app_module.design_profile_store().load()
        sync_design(build_dir, profile, app_module.project_language(pid))
        # sync_design only "refreshes" -- a prototype built **before** the profile upload has no
        # theme copy under prototype/, so nothing is swapped, and it stays unbranded even after
        # re-hosting. The operator must be able to learn "why nothing happened" at this point --
        # it means an improvement session must be opened once for it to take effect.
        if profile is not None and not theme_copies(build_dir):
            _log.warning(
                "design profile present but %s/%s has no theme copy under "
                "prototype/ -- re-hosting cannot re-brand it; an improvement "
                "session must run once to import aipds-theme.css first",
                pid, slug)
    except Exception:
        # A failure to apply the brand does not block hosting itself -- the screen opening
        # takes priority over colors. The cause remains in the log.
        _log.exception("design sync before host failed: %s/%s", pid, slug)

    info = await host.start(
        # cwd is explicit: ProtoHost's default is {root}/{pid}/{slug}, one level above the tree to
        # serve (where only the spec .md is — npm would die with package.json ENOENT).
        pid, slug, cwd=build_dir / "prototype",
        # It is `public_base_path`, not `proxy_prefix`: basePath is baked into asset URLs that
        # **the browser** resolves, and the browser's path includes the `/api` mount that is
        # stripped before this app sees it.
        base_path=public_base_path(pid, slug),
        # Same source as the build agent and Discovery (app.project_model) — the prototype app's
        # runtime LLM calls must also run on the model the project chose.
        model_id=app_module.project_model(pid))
    if info.state != "running":
        return info, None

    # The access token is issued **only after hosting has actually come up**. It is `ensure_token`,
    # so the value stays the same across repeated stop -> start — links already handed out must
    # not die because hosting is turned off and on during a workshop. The intended path for revoking it is a reset.
    token = host.ensure_token(pid, slug)
    if store is not None:
        # All three are best-effort: hosting is already up and the user gets the link. If they fail,
        # it just will not survive a replacement/restart, and the cause remains in the log.
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
    """Stop hosting and clear the desired state — so it does not come back up after a restart."""
    import aipds.app as app_module
    await app_module.proto_host().stop(pid, slug)
    store = prototype_store(pid)
    if store is not None:
        await store.clear_desired(slug)


async def adopt_local_state(previously_running: list[tuple[str, str]]) -> None:
    """Move what exists only locally to the S3 source of truth (at startup, before re-hosting).

    Covers two cases:

    - What the previous backend was hosting (`ProtoHost.previously_running`) → desired state.
      Hosting that came up before the desired state was recorded, or whose recording failed, also
      comes back up after a restart. What the user stopped has no pid file, so it does not land here.
    - A local build with no S3 generation → snapshot. Prototypes created before this store existed
      also survive the next replacement. If the content is the same, no new generation is created, so every startup is cheap.
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
    """Re-host, **one at a time**, the prototypes whose desired state is running. Returns how many came up.

    Runs in the background when the backend starts. Why sequential: a single hosting run is
    `npm install` + `next build` (peak about 2GB), so bringing them all up at once causes an OOM
    right after startup. Failures are skipped and logged — one does not block the rest.
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
                # There is no source to restore — no reason to keep the desired state.
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
