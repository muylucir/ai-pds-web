# backend/tests/test_no_legacy_brand.py — the product must have exactly one name.
#
# Scans `git ls-files`. Untracked things (SDD artifacts under docs/, node_modules,
# .venv) go into neither deployment nor review, so they are not checked.
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

#: **There are no exceptions.** For a while there were four: the GitHub repo URL, the
#: name of an already-deleted CloudFormation stack (two lines, ko/en), and the uuid5 seed
#: that derives the S3 mirroring keys. All four were there because "something outside our
#: code carries the old name", and all four disappeared as that thing was moved or
#: deleted: the stack was actually destroyed, the seed was resolved by having the
#: migration procedure move the prefix, and the repo was renamed to `ai-pds-web`.
#:
#: So this assertion has no allowlist machinery. Add it back when it is needed again:
#: having that decision visible in review beats keeping an empty list ready, and an
#: unused exception channel becomes a place that can be widened without breaking any test.
_NAME = re.compile(r"pathfinder", re.IGNORECASE)

#: Relative path of this file itself. This is the place that **talks about** the banned
#: word: allowlist values, the comments explaining them, and this constant's name all
#: contain "pathfinder". That is not the old name surviving in the wild but an
#: explanation of what this assertion blocks, so it is excluded from the scan, for the
#: same reason node_modules and .venv are excluded.
_SELF = Path(__file__).resolve().relative_to(REPO).as_posix()


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    return [line for line in out.splitlines() if line and line != _SELF]


def _offending_path(rel: str) -> str | None:
    """If `rel` itself contains the old name, flag that path.

    Most of this branch was **file renames** (83 of them), and `_offending_lines`, which
    scans contents, never looks at that category: a revived
    `infra/scripts/pathfinder-update` or `backend/pathfinder/` would pass straight through
    without this check, even if its contents said `aipds` throughout.
    """
    if not _NAME.search(rel):
        return None
    return f"{rel}: 파일 경로 자체가 옛 제품 이름을 담고 있다"


def _offending_lines(rel: str) -> list[str]:
    path = REPO / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []          # binary or unreadable files are not subject to this check
    bad = []
    path_offender = _offending_path(rel)
    if path_offender is not None:
        bad.append(path_offender)
    for number, line in enumerate(text.splitlines(), 1):
        if _NAME.search(line):
            bad.append(f"{rel}:{number}: {line.strip()}")
    return bad


def test_no_tracked_file_mentions_the_old_product_name():
    """With two layers of product name, the screens and the logs/docs point to different products.

    Zero, with no exceptions. Only this file itself is excluded (`_SELF`): this is the
    place that **talks about** the banned word.
    """
    offenders = [line for rel in _tracked() for line in _offending_lines(rel)]
    assert not offenders, (
        f"옛 제품 이름이 {len(offenders)}곳에 남아 있다:\n"
        + "\n".join(offenders[:40]))


def test_the_path_check_flags_a_resurrected_old_name_path():
    """`_offending_path` looks at **the path itself**, not the file's **contents**.

    Most of this branch (83 files) was file renames, and the line-by-line loop in
    `_offending_lines`, which scans contents, cannot see that category at all. This calls
    the helper directly without creating a real file: that way, removing this check (i.e.,
    making `_offending_path` always return `None`) breaks this one test immediately. This
    pins down the existence of the check more directly than a regression test that needs
    a file at that path to actually exist in the repo.
    """
    assert _offending_path("infra/scripts/pathfinder-update") is not None
    assert _offending_path("backend/pathfinder/app.py") is not None
    # Control: a path with a clean name is not flagged.
    assert _offending_path("infra/scripts/aipds-update") is None


def test_this_guard_has_no_exception_mechanism():
    """Prevent an exception channel from appearing silently.

    For a while there was an allowlist (repo URL, deleted stack name, uuid5 seed). All four
    were "something outside our code carries the old name", and all four disappeared as
    that thing was moved or deleted. There are zero exceptions now, so there is no list either.

    Why not leave an empty list: an unused exception channel becomes a place that can be
    widened without breaking any test. If it is needed again, the commit that revives the
    machinery surfaces that decision in review; this test breaking first makes sure of that.
    """
    assert not hasattr(_this_module(), "ALLOWED"), (
        "허용 기계를 되살렸다면 왜 필요한지, 왜 그 범위인지를 이 테스트와 함께 "
        "고쳐라 — 목록의 존재 자체가 리뷰에 보여야 한다")


def _this_module():
    import sys
    return sys.modules[__name__]


def test_the_session_id_seed_derives_the_migrated_uuid():
    """The uuid5 seed must produce UUIDs that match the S3 prefixes the migration moves data to.

    **This value cannot be changed freely.** The seed is not a name but a hash input, and
    `session_store._session_prefix` uses its output as the S3 key: changing the seed makes
    existing transcripts silently unreachable, and every project's Discovery conversation
    starts with an empty session. The price of changing the seed to `aipds:` in this rename
    is that the migration procedure in the operations manual has a step that moves the
    prefix. The next person to touch this value must do the same move, and this test
    breaking first tells them so.

    Observed mapping (before -> after the rename):

        ship       e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3 -> 95822f5b-0507-5622-b85e-669f7da9bbe5
        test1111   7565ab2e-fc2a-5909-9df8-d4dcb5b66661 -> 7a2934e1-45dd-5cf1-9576-fa1c160b8282
        ws-n   bbeda823-6b7e-5612-90cf-f706829a6c41 -> a4650830-9720-554b-83d2-6b1ab127307b

    The left side is the prefix that actually existed in the deployed bucket; the right
    side is the value after the move. `resume` is correctly `bool(None)` = `False`
    because `session` has no such key (the second line of `_sdk_session_id`).
    """
    from aipds.agent.claude_driver import _sdk_session_id

    assert _sdk_session_id({"session_id": "ship"}) == (
        "95822f5b-0507-5622-b85e-669f7da9bbe5", False)


def test_the_session_prefix_still_lands_on_the_measured_s3_path():
    """Even with the right seed, real data still silently disappears if the shape of the key holding it changes.

    The test above pins only the seed (the uuid5 output); it does not look at where
    `_session_prefix` places that seed. Someone rewriting `_session_prefix` could pass the
    seed test without touching it, so without this test, every project's discovery history
    could silently vanish and no test would catch it.

    Observed: before the rename, the path in the deployed bucket was
    `projects/ship/discovery/transcript/e23e6c8d-6ddf-559a-b05b-7a0db5c44fa3/main/`,
    and the migration procedure moves it under the seed below. The
    `projects/ship/` part is attached outside this module (`app.driver_factory` passes the
    store that `app.s3_store_factory` scoped to `projects/{project_id}/` to
    `ClaudeDriver`, and `ClaudeDriver` builds `DiscoverySessionStore` with that store), so
    here we only append the rest that `_session_prefix` itself returns
    (`discovery/transcript/<uuid>/main/`) and check that it matches.
    """
    from aipds.agent.session_store import _session_prefix

    seed = "95822f5b-0507-5622-b85e-669f7da9bbe5"
    prefix = _session_prefix({"session_id": seed})
    assert prefix == f"discovery/transcript/{seed}/main/"
    # With the project scope ("projects/ship/") prepended, it must equal the post-move path exactly.
    assert f"projects/ship/{prefix}" == (
        "projects/ship/discovery/transcript/"
        "95822f5b-0507-5622-b85e-669f7da9bbe5/main/")
