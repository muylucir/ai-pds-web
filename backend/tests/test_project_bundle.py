# backend/tests/test_project_bundle.py — the project bundle format.
from __future__ import annotations

import json

import pytest

from aipds.agent.session_store import project_transcript_prefix
from aipds.project_bundle import (
    KIND,
    MAX_ENTRIES,
    SCHEMA_VERSION,
    BundleError,
    build_manifest,
    bundle_path_to_s3_key,
    parse_manifest,
    parse_proto_source_path,
    proto_source_path,
    s3_key_to_bundle_path,
    safe_entry_name,
    source_excluded,
)
from aipds.proto.host import TOKEN_FILENAME

PID = "bundle-fmt"


@pytest.fixture
def tp() -> str:
    return project_transcript_prefix(PID)


# ---- S3 key ↔ bundle path ----


def test_ordinary_keys_land_under_project(tp):
    assert s3_key_to_bundle_path("aiplc-docs/audit.md", transcript_prefix=tp) == \
        "project/aiplc-docs/audit.md"
    assert s3_key_to_bundle_path("uploads/ab/x.md", transcript_prefix=tp) == \
        "project/uploads/ab/x.md"
    assert s3_key_to_bundle_path("approvals/2026-01-01.json", transcript_prefix=tp) == \
        "project/approvals/2026-01-01.json"


def test_project_manifest_is_not_copied(tp):
    """The same fact must also be in export.json (the import reads it before creating the
    project), so it is not kept in two places."""
    assert s3_key_to_bundle_path("project.json", transcript_prefix=tp) is None


def test_pending_is_excluded(tp):
    assert s3_key_to_bundle_path("pending/questions.json", transcript_prefix=tp) is None
    assert s3_key_to_bundle_path("pending/question-file.json",
                                 transcript_prefix=tp) is None


def test_prototype_build_transcript_is_excluded(tp):
    assert s3_key_to_bundle_path("prototypes/demo/transcript/abc/main/00000001.jsonl",
                                 transcript_prefix=tp) is None
    # Build chat restore records (proto/history.py) only mean something when joined with the transcript.
    assert s3_key_to_bundle_path("prototypes/demo/history/inputs/ab.json",
                                 transcript_prefix=tp) is None
    assert s3_key_to_bundle_path("prototypes/demo/history/answers/toolu_1.json",
                                 transcript_prefix=tp) is None
    # Surveys remain — they share the same prototypes/{slug}/ prefix, but they are something to move.
    assert s3_key_to_bundle_path("prototypes/demo/survey/questionnaire.json",
                                 transcript_prefix=tp) == \
        "project/prototypes/demo/survey/questionnaire.json"


def test_instance_only_prototype_keys_are_excluded_both_ways(tp):
    """Source generations, access tokens, and hosting intent are this instance's own business (proto/store.py).
    They are not exported, and even if a hand-made bundle carries them, they are not imported — if they were,
    the prototype would come up on its own on the receiving side, or the preview would open with a token the sender knows."""
    for key in ("prototypes/demo/source/current.json",
                "prototypes/demo/source/000003/prototype/app.js",
                "prototypes/demo/access-token",
                "prototypes/demo/hosting.json"):
        assert s3_key_to_bundle_path(key, transcript_prefix=tp) is None, key
        assert bundle_path_to_s3_key("project/" + key, transcript_prefix=tp) is None, key
    # Everything else in the same tree is moved as is.
    assert s3_key_to_bundle_path("prototypes/demo/session.json", transcript_prefix=tp) == \
        "project/prototypes/demo/session.json"


def test_keys_the_export_leaves_out_are_not_imported_either(tp):
    """If a hand-made bundle carries build transcripts or the question queue, the receiving agent takes the
    conversation the sender wrote as its own and resumes it. Keys that export drops are not imported either."""
    for key in ("prototypes/demo/transcript/main/00000001.jsonl",
                "prototypes/demo/history/0001.json",
                "pending/interrupt.json",
                "project.json"):
        assert s3_key_to_bundle_path(key, transcript_prefix=tp) is None, key
        assert bundle_path_to_s3_key("project/" + key, transcript_prefix=tp) is None, key


def test_discovery_transcript_loses_its_session_segment(tp):
    key = f"{tp}main/00000007.jsonl"

    path = s3_key_to_bundle_path(key, transcript_prefix=tp)

    assert path == "project/discovery/transcript/main/00000007.jsonl"
    # If the session uuid stays in the path, it carries over as is when importing under a different id.
    assert tp.split("/")[2] not in path


def test_subagent_transcript_keeps_its_subpath(tp):
    key = f"{tp}sub/agent-x/00000002.jsonl"

    assert s3_key_to_bundle_path(key, transcript_prefix=tp) == \
        "project/discovery/transcript/sub/agent-x/00000002.jsonl"


def test_transcript_of_another_session_is_dropped(tp):
    """Transcripts that are not of the active session are dead data the history does not read, and
    stripping the session segment would make the batch numbers collide with the active session so one side gets overwritten."""
    other = "discovery/transcript/11111111-2222-3333-4444-555555555555/main/00000001.jsonl"

    assert s3_key_to_bundle_path(other, transcript_prefix=tp) is None


def test_round_trip_restores_the_target_projects_session(tp):
    """When importing into a different project, the transcripts must go back under **that project's**
    session prefix. If this round trip breaks, the conversation disappears without an error."""
    target = project_transcript_prefix("some-other-project")
    assert target != tp

    path = s3_key_to_bundle_path(f"{tp}main/00000003.jsonl", transcript_prefix=tp)
    back = bundle_path_to_s3_key(path, transcript_prefix=target)

    assert back == f"{target}main/00000003.jsonl"


def test_ordinary_keys_round_trip_unchanged(tp):
    for key in ("aiplc-docs/discovery/envision/business-context.md",
                "answers/toolu_123.json",
                "prototypes/demo/survey/responses/r1.json"):
        path = s3_key_to_bundle_path(key, transcript_prefix=tp)
        assert bundle_path_to_s3_key(path, transcript_prefix=tp) == key


def test_paths_outside_project_dir_are_not_s3_keys(tp):
    assert bundle_path_to_s3_key("export.json", transcript_prefix=tp) is None
    assert bundle_path_to_s3_key("prototypes/demo/source/app.js",
                                 transcript_prefix=tp) is None
    assert bundle_path_to_s3_key("project/", transcript_prefix=tp) is None


# ---- Prototype source paths ----


def test_proto_source_path_round_trip():
    path = proto_source_path("demo", "prototype/app/page.tsx")

    assert path == "prototypes/demo/source/prototype/app/page.tsx"
    assert parse_proto_source_path(path) == ("demo", "prototype/app/page.tsx")


def test_parse_proto_source_rejects_other_shapes():
    for path in ("prototypes/demo/app.js",          # no source/
                 "prototypes/demo/source/",         # no file
                 "prototypes//source/app.js",       # no slug
                 "project/aiplc-docs/audit.md"):
        assert parse_proto_source_path(path) is None


# ---- Exclusion rules ----


def test_source_excluded_covers_build_artifacts_and_bookkeeping():
    assert source_excluded("node_modules/pkg/index.js")
    assert source_excluded("prototype/.next/cache/x")
    assert source_excluded(".git/HEAD")
    assert source_excluded(".proto-host.log")
    assert source_excluded(".proto-host.pid")
    assert not source_excluded("prototype/app/page.tsx")
    assert not source_excluded("prototype/package.json")


def test_source_excluded_never_ships_the_access_token():
    """`.proto-token` is a **credential** that guards the preview. It is kept separate from the test
    above because the failure means something different — if that one breaks, the zip gets bigger; if
    this one breaks, a credential leaks."""
    assert source_excluded(TOKEN_FILENAME)
    assert source_excluded(f"prototype/{TOKEN_FILENAME}")


# ---- zip entry safety ----


def test_safe_entry_name_accepts_ordinary_paths():
    assert safe_entry_name("project/aiplc-docs/audit.md") == \
        "project/aiplc-docs/audit.md"
    assert safe_entry_name("프로젝트/문서.md") == "프로젝트/문서.md"


@pytest.mark.parametrize("name", [
    "/etc/passwd",                      # absolute path
    "../../etc/passwd",                 # escape upward
    "project/../../../etc/passwd",      # escape midway
    "..\\..\\windows\\system32",        # backslash escape
    "C:/Windows/system.ini",            # drive letter
    "project/",                         # directory entry
    "",                                 # empty name
])
def test_safe_entry_name_rejects_traversal(name):
    with pytest.raises(BundleError):
        safe_entry_name(name)


def test_entry_cap_is_a_real_number():
    """Without a cap, what a zip bomb kills first is not the process but the instance's
    disk, and that disk also hosts other projects' build trees."""
    assert MAX_ENTRIES > 0


# ---- Manifest ----


def _manifest(**over) -> bytes:
    raw = build_manifest(
        exported_at="2026-09-12T00:00:00+00:00",
        source_project_id=PID,
        project={"name": "n", "created_at": "c", "model_id": None, "language": "ko"},
        counts={"artifacts": 1},
        prototypes=[],
    )
    data = json.loads(raw)
    data.update(over)
    return json.dumps(data).encode("utf-8")


def test_manifest_round_trip():
    data = parse_manifest(_manifest())

    assert data["kind"] == KIND
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["source_project_id"] == PID
    assert data["project"]["language"] == "ko"


def test_manifest_documents_what_was_left_out():
    """The receiving side must be able to answer 'why didn't this come along' from a single file."""
    excluded = parse_manifest(_manifest())["excluded"]

    assert "node_modules" in excluded
    assert TOKEN_FILENAME in excluded
    assert "pending/" in excluded
    assert "prototypes/*/transcript/" in excluded
    assert "prototypes/*/history/" in excluded


def test_wrong_kind_is_reported_before_version():
    """For a user who dropped in the wrong zip, 'unsupported version' is a message they cannot act on."""
    with pytest.raises(BundleError, match="not an AI-PDS project bundle"):
        parse_manifest(_manifest(kind="aipds-artifacts", schema_version=999))


def test_unsupported_version_is_refused():
    with pytest.raises(BundleError, match="schema_version"):
        parse_manifest(_manifest(schema_version=SCHEMA_VERSION + 1))


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"\xff\xfe"])
def test_malformed_manifest_is_a_bundle_error(raw):
    with pytest.raises(BundleError):
        parse_manifest(raw)


def test_manifest_without_project_metadata_is_refused():
    with pytest.raises(BundleError, match="project metadata"):
        parse_manifest(_manifest(project=None))


def test_manifest_without_source_id_is_refused():
    with pytest.raises(BundleError, match="source_project_id"):
        parse_manifest(_manifest(source_project_id=""))
