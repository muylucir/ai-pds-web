# backend/aipds/proto/cleanup.py -- project-wide prototype cleanup.
#
# A prototype's **record and its substance live in different places.**
# projects/{pid}/prototypes/** in S3 is the record (the spec, the transcript, the handoff,
# the surveys), while the substance is on the EC2 local disk at
# {proto_root}/{pid}/{slug}/ (the build tree, the hosting process, the access token) and in
# backend memory (the build session, the token cache). So a project deletion that removes
# only the projects/{pid}/ prefix leaves all of the substance behind -- a build tree of
# hundreds of megabytes to gigabytes, a preview process still running and holding its port,
# and **preview links already shared that still open** (the proxy does not look at whether
# the project is registered, only at proto_host's token and state).
#
# The order things run in here is the same as the individual prototype reset
# (reset_prototype in routes/prototypes.py). The reason that order is load-bearing applies
# unchanged: the survey token index (surveys/by-token/, at the bucket root) has no reverse
# lookup, so **the question file has to be read** to learn which token points at this slug.
# The survey purge therefore has to come before the S3 project prefix deletion -- reverse
# the order and that index remains forever, unreachable by any code.
from __future__ import annotations

import logging
from typing import Any, Callable

_log = logging.getLogger(__name__)

#: The prototype record prefix, relative to the project-scoped store (projects/{pid}/).
_PROTO_PREFIX = "prototypes/"


async def _slugs_from_s3(s3: Any) -> set[str]:
    """Extract the slugs from the S3 record. The survey tree lives under this
    (survey.store.survey_prefix == "prototypes/{slug}/survey/"), so this enumeration covers
    every token that needs reclaiming."""
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
) -> list[str]:
    """Run the reset path over **every** slug in this project.

    The per-slug order: close the build session -> purge the surveys (reclaiming the token
    index) -> purge the build tree and access token.

    The return value is a list of failure labels, and an empty list means everything
    succeeded. The caller (the project deletion route) has to return a 500 **without deleting
    the S3 project prefix** if there is even one failure: deleting the prefix while the survey
    purge has failed removes the question files and so permanently removes any way to reclaim
    the token index (SurveyStore.purge's docstring puts a gate on that path for the same
    reason).

    Every step is idempotent, so a retry converges. Leftovers that still will not clear (a
    tree rmtree leaves behind because of a permission error deep inside node_modules, say) can
    be handled by an operator deleting `{proto_root}/{pid}` by hand and deleting again -- the
    choice is to make the failure visible rather than leave it silently behind.

    With a None `s3` or `survey_store_factory` the survey step is skipped (local and tests
    with no bucket configured: where durable_projects_enabled() is False). The local substance
    cleanup still runs -- the build tree and the session exist independently of S3.
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
            # If the listing cannot be read, there is no way to know what is being left
            # behind. Deleting S3 after a partial cleanup would orphan the token index of an
            # unread slug, so it stops here.
            _log.exception("prototype record listing failed for %s", project_id)
            return ["slug-list"]

    failures: list[str] = []
    for slug in sorted(slugs):
        session = sessions.get((project_id, slug))
        if session is not None:
            try:
                await session.close()
            except Exception:
                # If the session does not close, the claude subprocess and the concurrent
                # build slot both leak.
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
                # This slug goes no further -- the same judgement as the gate on the reset
                # path. Deleting the build tree with the surveys still there leaves the
                # questions a retry would reclaim intact while there is no substance left to
                # host.
                continue

        try:
            await host.purge(project_id, slug)
        except Exception:
            _log.exception("delete: build-tree purge failed: %s/%s", project_id, slug)
            failures.append(f"build-tree:{slug}")

    # The parent directory (`{proto_root}/{pid}`) is deleted **after** the slug loop.
    #
    # The order is load-bearing: each slug's `purge` calls `stop()` first, and deleting the
    # parent first pulls the tree out from under a running `npm start`, orphaning the process
    # while it keeps holding its port (the failure `ProtoHost.purge`'s docstring warns
    # about). `purge_project` stops nothing, so this position is the only safe one.
    #
    # It is skipped when there were failures: deleting a remaining slug's tree along with the
    # parent would destroy the substance a retry has to reclaim -- the same judgement as the
    # per-slug gate.
    if not failures:
        try:
            await host.purge_project(project_id)
        except Exception:
            _log.exception("delete: prototype root purge failed: %s", project_id)
            failures.append("proto-root")
    return failures
