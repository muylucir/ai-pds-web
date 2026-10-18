# backend/aipds/proto/cleanup.py — project-level prototype cleanup.
#
# A prototype's **records and its live parts live in different places.** S3's
# projects/{pid}/prototypes/** holds the records (spec, transcript, handoff,
# survey), while the live parts are on the EC2 local disk at
# {proto_root}/{pid}/{slug}/ (build tree + hosting process + access token) and in
# backend memory (build sessions, token cache). So if project deletion removes
# only the projects/{pid}/ prefix, all the live parts remain — a build tree of
# hundreds of MB to GB, a preview process that keeps running and the port it
# holds, and **a state where already-shared preview links still open** (the proxy
# does not check whether the project is registered; it only looks at proto_host's
# token and state).
#
# The order run here is the same as the per-prototype reset (reset_prototype in
# routes/prototypes.py), and the reason that order is load-bearing applies as is:
# the survey token index (surveys/by-token/, at the bucket root) has no reverse
# lookup, so **you have to read the question file** to know which token points
# to this slug. Therefore the survey purge must come before the S3 project-prefix
# deletion — if the order is flipped, that index stays forever, unreachable by
# any code.
from __future__ import annotations

import logging
from typing import Any, Callable

_log = logging.getLogger(__name__)

#: Prototype record prefix relative to the project-scoped store (projects/{pid}/).
_PROTO_PREFIX = "prototypes/"


async def _slugs_from_s3(s3: Any) -> set[str]:
    """Extract slugs from the S3 records. Since the survey tree lives under this
    prefix (survey.store.survey_prefix == "prototypes/{slug}/survey/"), this
    enumeration covers every token that needs to be reclaimed."""
    out: set[str] = set()
    for key in await s3.list(_PROTO_PREFIX):
        slug, sep, _ = key[len(_PROTO_PREFIX):].partition("/")
        if sep and slug:
            out.add(slug)
    return out


async def purge_project_prototypes(
    project_id: str,
    *,
    host: Any,
    sessions: dict,
    s3: Any | None = None,
    survey_store_factory: Callable[[str, str], Any] | None = None,
    preview_store: Any | None = None,
) -> list[str]:
    """Run the reset path for **every** slug of this project.

    Order per slug: close the build session → survey purge (including reclaiming
    the token index) → build tree / access token purge.

    The return value is a list of failure labels; an empty list means everything
    succeeded. If there is even one failure, the caller (the project-deletion
    route) must **not delete the S3 project prefix** and must return 500: deleting
    the prefix after a failed survey purge removes the question files, and with
    them any way to reclaim the token index, permanently (SurveyStore.purge's
    docstring puts a gate on that path for the same reason).

    Every step is idempotent, so retries converge. Residue that still does not
    clear (e.g., a tree rmtree leaves behind because of a permission error deep in
    node_modules) can be handled by an operator deleting `{proto_root}/{pid}`
    directly and deleting again — we choose to make the failure visible rather
    than leave things behind silently.

    If `s3`/`survey_store_factory` is None, the survey step is skipped (local runs
    and tests with no bucket configured: when durable_projects_enabled() is
    False). Local cleanup still runs as usual — the build trees and sessions exist
    independently of S3.
    """
    slugs: set[str] = set()
    try:
        slugs |= set(host.slugs(project_id))
    except Exception:
        _log.exception("local prototype listing failed for %s", project_id)
        return ["slug-list"]
    slugs |= {slug for (pid, slug) in list(sessions) if pid == project_id}
    if s3 is not None:
        try:
            slugs |= await _slugs_from_s3(s3)
        except Exception:
            # If the listing cannot be read, we cannot know what we would leave
            # behind. Deleting S3 after a partial cleanup would orphan the token
            # index of the unread slugs, so stop here.
            _log.exception("prototype record listing failed for %s", project_id)
            return ["slug-list"]

    failures: list[str] = []
    for slug in sorted(slugs):
        session = sessions.get((project_id, slug))
        if session is not None:
            try:
                await session.close()
            except Exception:
                # If the session does not close, the claude subprocess and a concurrent build slot leak.
                _log.exception("delete: session close failed: %s/%s", project_id, slug)
                failures.append(f"session:{slug}")
            else:
                sessions.pop((project_id, slug), None)

        if survey_store_factory is not None:
            try:
                await survey_store_factory(project_id, slug).purge()
            except Exception:
                _log.exception("delete: survey purge failed: %s/%s", project_id, slug)
                failures.append(f"survey:{slug}")
                # Go no further with this slug — the same call as the reset path's
                # gate. Deleting the build tree while the survey remains leaves the
                # questions for a retry to reclaim but nothing left to host.
                continue

        # The root index of preview tokens (proto/store.py). Same situation as the
        # survey token index — the token is read from inside the S3 project
        # prefix, so it must be reclaimed **before** that prefix is deleted, and a
        # failure is reported so the caller does not delete the prefix.
        if preview_store is not None:
            try:
                await preview_store.forget_token(slug)
            except Exception:
                _log.exception("delete: preview token purge failed: %s/%s",
                               project_id, slug)
                failures.append(f"preview-token:{slug}")
                continue

        try:
            await host.purge(project_id, slug)
        except Exception:
            _log.exception("delete: build-tree purge failed: %s/%s", project_id, slug)
            failures.append(f"build-tree:{slug}")

    # The parent directory (`{proto_root}/{pid}`) is deleted **after** the slug loop.
    #
    # The order is load-bearing: each per-slug `purge` calls `stop()` first, but if
    # the parent were deleted first, the tree would vanish from under the running
    # `npm start`, orphaning the process while it keeps holding its port (the very
    # failure `ProtoHost.purge`'s docstring warns about). `purge_project` stops
    # nothing, so this is the only safe place for it.
    #
    # Skip it if anything failed: deleting the remaining slugs' trees along with
    # the parent removes what a retry needs to reclaim — the same call as the
    # per-slug gate.
    if not failures:
        try:
            await host.purge_project(project_id)
        except Exception:
            _log.exception("delete: prototype root purge failed: %s", project_id)
            failures.append("proto-root")
    return failures
