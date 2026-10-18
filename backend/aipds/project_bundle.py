# backend/aipds/project_bundle.py — sole owner of the project bundle (.zip) format.
#
# This module defines the shape of the single file that moves one project to another instance.
# export (project_export.py) and import (project_import.py) must use the **same** layout
# constants, exclusion rules, and key conversions; with two copies, one of them drifts.
# `proto/layout.py` exists for the same reason — there, "id → spec path" was duplicated in
# four places, and four copies of a rule are four chances for one of them to drift into a hole.
#
# Layout:
#
#   export.json                        manifest (build_manifest below)
#   project/**                         the projects/{pid}/ subtree
#   prototypes/{slug}/source/**        local build tree (not in S3 — see below)
#
# **Why an exclusion list rather than an inclusion list.** `project/` carries the **entire**
# project prefix and enumerates only what is left out. Inclusion lists rot: when a new subtree
# appears (surveys did, approval history did), an export that forgot to add it to the list
# silently produces an incomplete bundle, and the receiving side cannot tell it apart from "a
# project that never had that feature". An exclusion list, by contrast, **carries more** when
# you forget — and that failure is visible as a bigger zip.
#
# **Why prototype source is not carried as S3 keys.** The in-process builder writes directly to
# the local build tree and ProtoHost serves it in place, so the newest source is on disk.
# The source generations in S3 (`prototypes/{slug}/source/NNNNNN/`, proto/store.py) are snapshots
# of it. The bundle carries a single copy via `proto/source.source_entries` (local first,
# otherwise the current generation), and that path lives separately under `prototypes/{slug}/source/`
# inside the bundle so it does not mix with S3 keys. That is why the S3-side generation keys are
# dropped below — carrying them would ship the same source twice, past generations included.
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from aipds.pathsafe import reject_unsafe
from aipds.proto.host import TOKEN_FILENAME

#: Bundle format version. Import accepts only versions it knows — interpreting an unknown
#: version on a best-effort basis yields "a half-imported project", and that state is
#: indistinguishable from a normal one on screen. When bumping it, keep the old version in `SUPPORTED_SCHEMA_VERSIONS`.
SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

#: A self-describing marker of what this zip is. Dropping the wrong zip (a prototype handoff
#: archive, an artifact archive) into the import box is a common mistake, and in that case
#: "this is not a project bundle" is something the user can act on, unlike "export.json is missing".
KIND = "aipds-project-export"

MANIFEST_NAME = "export.json"
PROJECT_DIR = "project/"
PROTO_DIR = "prototypes/"
PROTO_SOURCE_LEAF = "source/"

#: S3 key of the project manifest. **Not put in the bundle** — the same facts (name, creation
#: date, model, language) must also be in `export.json` (that is where a person opens the zip to
#: check, and import must read those values before it creates the project), and keeping them in
#: two places produces diverging copies. Import writes its own fresh one with `write_manifest`.
PROJECT_MANIFEST_KEY = "project.json"

#: Pending questions. A convenience for restoring a question parked by a live turn after a
#: refresh (agent/pending_store.py), and that turn is an in-memory Future. An imported project
#: has no such turn, so restoring it shows **a question form whose answer nobody receives**.
#: Not carrying it at all is more honest than putting it in the bundle and ignoring it on import.
PENDING_PREFIX = "pending/"

#: Root of the Discovery transcript (project-relative). See the session-segment conversion below.
TRANSCRIPT_ROOT = "discovery/transcript/"

#: The prototype build agent's transcript, and the records used to restore it as the build
#: conversation (proto/history.py). It is mostly build chatter and large — it is context for a
#: session that **improves** the prototype, but an imported instance starts a fresh build session
#: anyway (it has no local CLI transcript). The records only mean something when joined with the
#: transcript, so they are dropped together. The handoff zip drops the same things for the same
#: reason (the `_ARCHIVE_*` comments in routes/prototypes.py).
_PROTO_CONVERSATION_RE = re.compile(r"^prototypes/[^/]+/(?:transcript|history)/")

#: Prototype keys that are specific to this instance and therefore not moved (proto/store.py).
#:   source/        source generations — the bundle carries the source separately (header above).
#:   access-token   credential for the public preview. Same reason the local `.proto-token` is dropped
#:                  (SOURCE_EXCLUDED_FILES) — the receiving side issues a new one when it hosts.
#:   hosting.json   "this should be hosted here" — it must not come up on its own on the receiving instance.
_PROTO_INSTANCE_RE = re.compile(r"^prototypes/[^/]+/(?:source/|access-token$|hosting\.json$)")


class BundleError(ValueError):
    """The bundle is not a shape we know. The route translates it to 400."""


# ---- Exclusion rules for the prototype source tree ----
#
# This must be the **same set** as the dev-team handoff zip (routes/prototypes.py), which is why
# that module imports this one. Two copies mean only one gets fixed, and this set's failure modes
# are not symmetric: missing a build output only makes the zip bigger, but missing `.proto-token`
# means **a live access credential ships in the download**. The person receiving that zip may be
# exactly who the token is meant to keep out.
SOURCE_EXCLUDED_DIRS = frozenset({"node_modules", ".next", ".git"})

#: `.proto-host.*` is this box's bookkeeping (logs, pid) and means nothing on another instance. Hosting
#: keeps its bookkeeping outside the build tree (the state tree, proto/host.py), but files with those
#: names may remain in the tree, so they are still dropped.
#: `TOKEN_FILENAME` is imported from the module that owns that filename instead of copying the literal —
#: a copy silently stops excluding on the day the name changes.
SOURCE_EXCLUDED_FILES = frozenset({
    ".proto-host.log", ".proto-host.pid", TOKEN_FILENAME,
})


def source_excluded(rel: str) -> bool:
    """Whether this relative path in the build tree is dropped from the bundle."""
    parts = PurePosixPath(rel).parts
    if any(p in SOURCE_EXCLUDED_DIRS for p in parts):
        return True
    return parts[-1] in SOURCE_EXCLUDED_FILES if parts else True


# ---- Size limits (zip bomb defense) ----
#
# Import downloads the staging object to a temp file and opens it with `zipfile` (to keep
# memory bounded). So without limits, what a compression bomb kills first is not the process but
# **the instance's disk** — and that instance also hosts the prototype build trees of other
# workshop projects.
MAX_ENTRIES = 20_000
MAX_ENTRY_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024


def safe_entry_name(name: str) -> str:
    """Validate a zip entry name and return it unchanged. Raises `BundleError` if unsafe.

    A zip entry name is **an arbitrary string chosen by whoever built the archive**, and we
    use it in two places: as an S3 key and as a local path. That is why `zipfile.extractall`
    is not used — it normalizes for you, but we read entries one by one and send each to a
    different destination, so we do not get that protection.

    `reject_unsafe` blocks absolute paths and `..`; two more are blocked here:
    backslashes from zips made on Windows (`a\\..\\b` looks like one segment on POSIX but is
    an escape in tools that extract it) and drive letters.
    """
    if not name or name.endswith("/"):
        raise BundleError(f"unsafe bundle entry: {name!r}")
    if "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise BundleError(f"unsafe bundle entry: {name!r}")
    try:
        reject_unsafe(name)
    except ValueError as e:
        raise BundleError(str(e)) from e
    return name


# ---- S3 key ↔ bundle path ----
#
# **The session segment is the reason this conversion exists.** Discovery transcripts live at
# `discovery/transcript/{uuid5("aipds:"+pid)}/main/NNNNNNNN.jsonl`
# (agent/session_store.py), and that uuid is **derived from the project id**. So if a project is
# imported under a different id while keeping the keys as-is, the history reader lists a prefix
# nothing was written to, and since `list_history` downgrades every failure to `[]`, the result
# is **an empty conversation with no error**. That silence is the most expensive failure in this
# feature, so the bundle stores keys with the session segment stripped, and import re-inserts the target project's value.


def s3_key_to_bundle_path(key: str, *, transcript_prefix: str) -> str | None:
    """Project-relative S3 key → path inside the bundle. None if excluded.

    `transcript_prefix` is this project's **active** session prefix
    (`agent/session_store.project_transcript_prefix`).
    """
    if key == PROJECT_MANIFEST_KEY:
        return None
    if key.startswith(PENDING_PREFIX):
        return None
    if _PROTO_CONVERSATION_RE.match(key):
        return None
    if _PROTO_INSTANCE_RE.match(key):
        return None
    if key.startswith(TRANSCRIPT_ROOT):
        if not key.startswith(transcript_prefix):
            # A transcript under another session id. It is not the active session, so it is
            # dead data that history never reads (load_transcript only looks at the active prefix),
            # and stripping the session segment would make its batch numbers **collide** with the
            # active session's, silently overwriting one side. Not carrying it is the only safe choice.
            return None
        return PROJECT_DIR + TRANSCRIPT_ROOT + key[len(transcript_prefix):]
    return PROJECT_DIR + key


def bundle_path_to_s3_key(path: str, *, transcript_prefix: str) -> str | None:
    """Path inside the bundle → project-relative S3 key. None if outside `project/`.

    This is the inverse of `s3_key_to_bundle_path`, and having it in the same module is the
    point — handling the session segment on only one side makes the conversation disappear.
    """
    if not path.startswith(PROJECT_DIR):
        return None
    key = path[len(PROJECT_DIR):]
    if not key:
        return None
    # Keys that export does not carry are not imported either (same set as `s3_key_to_bundle_path`) —
    # if a hand-made bundle carries `hosting.json`, the prototype comes up on its own on the
    # receiving instance, and if it carries `access-token`, the preview opens with a token the
    # sender knows. If it carries build transcripts or `pending/`, the build agent and Discovery
    # resume the sender's conversation and questions as their own. The route writes the manifest last.
    if key == PROJECT_MANIFEST_KEY or key.startswith(PENDING_PREFIX):
        return None
    if _PROTO_CONVERSATION_RE.match(key) or _PROTO_INSTANCE_RE.match(key):
        return None
    if key.startswith(TRANSCRIPT_ROOT):
        return transcript_prefix + key[len(TRANSCRIPT_ROOT):]
    return key


def proto_source_path(slug: str, rel: str) -> str:
    """Relative path in the build tree → path inside the bundle."""
    return f"{PROTO_DIR}{slug}/{PROTO_SOURCE_LEAF}{rel}"


def parse_proto_source_path(path: str) -> tuple[str, str] | None:
    """Path inside the bundle → (slug, path relative to the build tree). None if not that shape."""
    if not path.startswith(PROTO_DIR):
        return None
    rest = path[len(PROTO_DIR):]
    slug, sep, tail = rest.partition("/")
    if not sep or not slug or not tail.startswith(PROTO_SOURCE_LEAF):
        return None
    rel = tail[len(PROTO_SOURCE_LEAF):]
    return (slug, rel) if rel else None


# ---- Manifest ----


def build_manifest(*, exported_at: str, source_project_id: str,
                   project: dict, counts: dict, prototypes: list[dict]) -> str:
    """Body of `export.json`.

    It also carries `excluded`: the receiving side must be able to answer "why didn't this
    come over" from one file. This is where what is absent is stated as absent.
    """
    return json.dumps({
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "exported_at": exported_at,
        "source_project_id": source_project_id,
        "project": project,
        "counts": counts,
        "prototypes": prototypes,
        "excluded": [
            *sorted(SOURCE_EXCLUDED_DIRS),
            *sorted(SOURCE_EXCLUDED_FILES),
            PENDING_PREFIX,
            "prototypes/*/transcript/",
            "prototypes/*/history/",
        ],
    }, ensure_ascii=False, indent=2)


def parse_manifest(raw: bytes) -> dict:
    """`export.json` → dict. `BundleError` if it is not a shape we know.

    The order of checks decides the quality of the message: `kind` is checked before the
    version — telling a user who dropped the wrong zip "unsupported version" gives them nothing they can fix.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BundleError(f"{MANIFEST_NAME} is not valid JSON") from e
    if not isinstance(data, dict):
        raise BundleError(f"{MANIFEST_NAME} is not an object")
    if data.get("kind") != KIND:
        raise BundleError(f"not an AI-PDS project bundle (kind={data.get('kind')!r})")
    if data.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        raise BundleError(
            f"unsupported bundle schema_version {data.get('schema_version')!r}")
    project = data.get("project")
    if not isinstance(project, dict):
        raise BundleError(f"{MANIFEST_NAME} has no project metadata")
    source_id = data.get("source_project_id")
    if not isinstance(source_id, str) or not source_id:
        raise BundleError(f"{MANIFEST_NAME} has no source_project_id")
    return data
