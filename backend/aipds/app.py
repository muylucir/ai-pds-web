# backend/aipds/app.py
from __future__ import annotations
import asyncio
import functools
import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
import boto3
from botocore.config import Config as BotoConfig
from dotenv import load_dotenv
from fastapi import FastAPI

# backend/.env (gitignored, optional) feeds the AIPDS_*/ANTHROPIC_MODEL
# settings read via os.environ below. Real environment variables win over the
# file (override=False) so shell exports / container env keep working as
# before, and a missing file is a silent no-op (local mode needs no config).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from fastapi.middleware.cors import CORSMiddleware
from aipds import agent_home, launcher
from aipds.workspace import ProjectRegistry, Workspace
from aipds.runner import AgentRunner
from aipds.agent.claude_driver import ClaudeDriver
from aipds.cli_settings import cli_model_id
from aipds.s3store import S3Store, S3StoreLike
from aipds.project_store import restore_projects
from aipds.pathsafe import reject_unsafe_segment
from aipds.turn_job import TurnJobs
from aipds.turn_marker import save_marker

_log = logging.getLogger(__name__)

#: Application log level. Defaults to INFO — most of what diagnosis needs is at that level.
_LOG_LEVEL_ENV = "AIPDS_LOG_LEVEL"

#: Marker that lets configure_logging recognize the handler it attached. Prevents
#: handlers from piling up on re-invocation and printing the same line several times
#: (uvicorn --reload, or TestClient running the lifespan twice).
_HANDLER_TAG = "aipds"


def configure_logging() -> None:
    """Attach a handler to the root logger.

    Without it, application logs effectively disappear. uvicorn configures only
    its own loggers and leaves the root alone, so with no handler INFO is
    silently dropped and only WARNING leaks out, unformatted, via Python's lastResort.

    Measured: in the workshop box's journald, output from the `aipds` logger was
    **0** of 2905 lines. We were chasing a chat-history restore bug at the time, and
    the logs that pointed at the cause (the resume decision in `_resolve_resume`, the
    SDK's "dropping mirror frame" warning) all vanished down this hole, so we had to
    reproduce and instrument repeatedly in production.

    That warning is why the SDK logger is opened too: transcript-mirroring failures
    are reported only through the `claude_agent_sdk` logger, so opening only our own
    logger still leaves "the frame was dropped" indistinguishable from "the frame never arrived".

    We add a handler to the root only, without overhauling uvicorn's configuration —
    the goal is to leave the shape of the access log as it is.
    """
    level = getattr(logging, os.environ.get(_LOG_LEVEL_ENV, "INFO").upper(),
                    logging.INFO)
    root = logging.getLogger()
    if not any(getattr(h, "_aipds_tag", None) == _HANDLER_TAG
               for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(levelname)s:    %(name)s: %(message)s"))
        handler._aipds_tag = _HANDLER_TAG  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(min(root.level or level, level) if root.level else level)
    # Open both loggers explicitly. The root level alone is not enough — if a third
    # party has raised its own logger's level, records are filtered even with the root open.
    for name in ("aipds", "claude_agent_sdk"):
        logging.getLogger(name).setLevel(level)


registry = ProjectRegistry()



@functools.lru_cache(maxsize=None)
def s3_client(region: str):
    """One S3 client per region, shared by the whole process.

    The factories below are called on every request (ten times for one `GET /projects`).
    Creating a `boto3.client` each time there is a problem less for the construction
    cost than for the connection pool — the pool belongs to the client, so every
    request opens a fresh TCP/TLS connection to S3. boto3 clients are thread-safe
    and refresh instance-role credentials on their own, so sharing loses nothing.

    The pool size is set comfortably above the default `to_thread` pool (cores+4) and
    concurrent gathers — with the default of 10, parallel GETs serialize again in the pool queue.

    The factory functions stay as they are: they are the points tests swap out.
    """
    return boto3.client("s3", region_name=region,
                        config=BotoConfig(max_pool_connections=50))


# Monkeypatchable in tests to inject a FakeS3Store (no AWS). Durable store keeps
# the project's aiplc-docs/prototype/uploads subtree (S3 = source of truth); the
# in-process AgentRunner restores it to a local workspace at the start of a turn.
def s3_store_factory(project_id: str) -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = s3_client(region)
    return S3Store(bucket=bucket, prefix=f"projects/{project_id}/", client=client)


# Monkeypatchable in tests. Scoped to the `sessions/` prefix
# that S3SessionManager writes; the backend only READS them for history.
def session_s3_factory() -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = s3_client(region)
    return S3Store(bucket=bucket, prefix="sessions/", client=client)


# For the manifest/deletion — a root store that sees all of projects/. Monkeypatched in tests.
def projects_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = s3_client(region)
    return S3Store(bucket=bucket, prefix="projects/", client=client)


# For the model catalog — a bucket-root store. The catalog has to exist before any
# project (the project creation screen reads it without a project), so it lives
# outside projects/. Monkeypatched in tests.
def models_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = s3_client(region)
    return S3Store(bucket=bucket, prefix="", client=client)


# For the brand profile — a bucket-root store. There is only profile.json under design/,
# and it lives outside projects/ for the same reason as the model catalog. Monkeypatched in tests.
def design_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = s3_client(region)
    return S3Store(bucket=bucket, prefix="", client=client)


def design_profile_store():
    """DesignProfileStore factory (monkeypatchable in tests).

    With no bucket, returns a read-only (None) store -- same reason as
    model_catalog(): local development without a bucket must not block a build session's start().
    """
    from aipds.design_profile import DesignProfileStore
    if not durable_projects_enabled():
        return DesignProfileStore(None)
    return DesignProfileStore(design_root_s3_factory())


def model_catalog():
    """ModelCatalog factory (monkeypatchable in tests).

    With no bucket, returns a read-only catalog (seed only) — local development must
    be able to create a project with no configuration at all, and that screen's combo box must be populated too.
    """
    from aipds.model_catalog import ModelCatalog
    if not durable_projects_enabled():
        return ModelCatalog(None)
    return ModelCatalog(models_root_s3_factory())


def project_model(project_id: str) -> str | None:
    """The Bedrock model id this project runs on.

    The fallback order is project → env → None, and each slot has a reason:
      - project: the value chosen at creation (copied into the manifest).
      - env(ANTHROPIC_MODEL): the path that keeps projects created before this feature running.
        In deployment, MODEL in backend-permissions.ts supplies this value.
      - None: local development with no env either. When the driver receives None it
        does not set ANTHROPIC_MODEL, so the SDK default applies (the previous behavior).
    """
    return registry.get_model_id(project_id) or os.environ.get("ANTHROPIC_MODEL")


def project_language(project_id: str) -> str:
    """This project's output language ("ko"|"en"). Always has a value.

    Unlike project_model there is no env fallback: language has no reason to have a
    process-wide default (it is natural for deployment to decide the model, but
    language is a property of the project), and the registry already settles it to "ko".

    This function exists so the callers (driver_factory, proto_session_factory,
    survey_store_factory) do not touch the registry directly — it keeps the same
    shape as project_model.
    """
    return registry.get_language(project_id)


def project_effort(project_id: str) -> str | None:
    """This project's effort. With None the driver does not pass `--effort`, so it runs at
    the CLI default (medium for Opus 5.5, high for the other current-generation models).

    There is no env fallback for the same reason as project_language: effort is a property
    of the project, chosen as a pair with the model. The deployment-wide default lives in the catalog (model_catalog.SEED_MODELS).
    """
    return registry.get_effort(project_id)


# ---- Authentication (routes/*, auth/deps.py) ----

_jwks_singleton = None

#: Explicit switch that turns authentication off. Only local development and tests turn it on (.env.example, tests/conftest.py).
AUTH_DISABLED_ENV = "AIPDS_AUTH_DISABLED"
_TRUTHY = {"1", "true", "yes", "on"}


def cognito_config() -> dict | None:
    """Cognito settings. None = authentication bypass (every request is a virtual admin).

    **The bypass turns on only through the switch.** Both values being empty is not enough —
    if both lines go missing from the deployment config (a unit-file regeneration mistake, a
    manual edit), the app would be open to the internet with auth off. So if both are empty and
    `AIPDS_AUTH_DISABLED` is not set either, we raise. This is also called at import time
    (`_docs_openapi_url`), so the backend does not start at all.

    A state with only **one** of the pool id and client id, however, is not "unconfigured"
    but a deployment accident. This case used to be treated as None (bypass) as well, which
    meant auth was off and every request silently passed as a virtual admin (LOCAL_PRINCIPAL)
    — no crash, no warning, no trace. So a half-configured state raises immediately
    (fail-closed): those requests become 500s, but a visible failure is better than admin
    privileges leaking without anyone knowing. cognito_config() is called on every
    request (require_user), so the moment a deploy script deletes one of the two variables,
    this exception appears immediately, even without a restart.
    """
    pool = os.environ.get("AIPDS_COGNITO_USER_POOL_ID", "").strip()
    client = os.environ.get("AIPDS_COGNITO_CLIENT_ID", "").strip()
    if not pool and not client:
        if os.environ.get(AUTH_DISABLED_ENV, "").strip().lower() in _TRUTHY:
            return None
        raise RuntimeError(
            "AIPDS_COGNITO_USER_POOL_ID and AIPDS_COGNITO_CLIENT_ID are both unset. "
            "Set them to enable authentication, or set AIPDS_AUTH_DISABLED=1 to "
            "run without it (local development only — every request becomes admin)")
    if not pool or not client:
        raise RuntimeError(
            "AIPDS_COGNITO_USER_POOL_ID and AIPDS_COGNITO_CLIENT_ID "
            "must both be set or both be unset — exactly one is set, which "
            "would otherwise silently bypass authentication as admin for "
            "every request")
    region = (os.environ.get("AIPDS_COGNITO_REGION", "").strip()
              or os.environ.get("AIPDS_S3_REGION", "ap-northeast-2"))
    return {"region": region, "user_pool_id": pool, "client_id": client}


def jwks_cache():
    """JWKS cache singleton (monkeypatchable in tests)."""
    global _jwks_singleton
    if _jwks_singleton is None:
        from aipds.auth.verifier import JwksCache
        cfg = cognito_config() or {}
        _jwks_singleton = JwksCache(region=cfg.get("region", "ap-northeast-2"),
                                    user_pool_id=cfg.get("user_pool_id", ""))
    return _jwks_singleton


_revocations_singleton = None


def revocations():
    """Singleton for the record of sessions revoked by an admin (auth/revocation.py) (monkeypatchable in tests)."""
    global _revocations_singleton
    if _revocations_singleton is None:
        from aipds.auth.revocation import Revocations, default_path
        _revocations_singleton = Revocations(default_path())
    return _revocations_singleton


def cognito_admin():
    """CognitoAdmin factory (monkeypatchable in tests).

    Why it is not a singleton: boto3 clients are thread-safe, but tests need to be able
    to swap in a fake per request, and the construction cost is negligible.
    """
    from aipds.auth.cognito import CognitoAdmin
    cfg = cognito_config()
    if cfg is None:
        raise RuntimeError(
            "user management requires AIPDS_COGNITO_USER_POOL_ID / "
            "AIPDS_COGNITO_CLIENT_ID")
    client = boto3.client("cognito-idp", region_name=cfg["region"])
    return CognitoAdmin(client, cfg["user_pool_id"])


def cognito_idp_client():
    """A raw cognito-idp client not bound to a pool (monkeypatchable in tests).

    It is separate from `cognito_admin()` because the targets differ. That one is for the
    Admin* APIs and must always carry the UserPoolId, while the self-service API
    (`ChangePassword`) does not accept it — the access token is the identity. Wrapping it in
    CognitoAdmin would mean passing a pool id that is never used, and would blur that
    class's invariant that "every call is an admin call against this pool".
    """
    cfg = cognito_config()
    if cfg is None:
        raise RuntimeError(
            "self-service Cognito calls require AIPDS_COGNITO_USER_POOL_ID / "
            "AIPDS_COGNITO_CLIENT_ID")
    return boto3.client("cognito-idp", region_name=cfg["region"])


def import_staging():
    """ImportStaging factory (monkeypatchable in tests).

    Unlike the other factories, this one **raises** when there is no bucket. model_catalog()
    and design_profile_store() degrade to a None store because the screen has to work even
    without that feature, but silently degrading here produces a state of "got an upload URL,
    but the import finds nothing" — and the user learns it only after uploading hundreds of MB.
    The route translates this exception into a 503.
    """
    from aipds.import_staging import ImportStaging
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    if not bucket:
        raise RuntimeError(
            "project import requires AIPDS_S3_BUCKET — the bundle is staged in "
            "S3 so the browser can upload it directly")
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    return ImportStaging(bucket=bucket,
                         client=s3_client(region))


def durable_projects_enabled() -> bool:
    """When the bucket is not configured (local/tests), skip list persistence entirely."""
    return bool(os.environ.get("AIPDS_S3_BUCKET"))


def _rules_dir() -> str:
    #: `steering-files/` is a **submodule** of the upstream repo (aws-samples/sample-ai-plc),
    #: and `aiplc-rules/` is a directory inside it. Because it is a submodule, a clone alone
    #: leaves it empty — `git submodule update --init` appears in both boot
    #: (infra/lib/user-data.ts) and update (infra/scripts/aipds-update). If it is empty,
    #: place_rules cannot find core-workflow.md, and that failure only shows on the first turn.
    default = str(Path(__file__).resolve().parent.parent.parent
                  / "steering-files" / "aiplc-rules")
    return os.environ.get("AIPDS_RULES_DIR", default)


def _workspaces_dir() -> Path:
    root = os.environ.get("AIPDS_WORKSPACES_DIR")
    return Path(root) if root else Path(tempfile.gettempdir()) / "aipds-workspaces"


async def purge_local_workspace(project_id: str) -> None:
    """Delete this project's local workspace directory. Idempotent.

    **Why `runner.stop()` is not enough.** It has an rmtree too, but it is gated by two
    conditions. First, the delete route calls stop only when `registry.has_workspace(pid)`,
    and that flag is set only by `attach()`, while restore at startup only does
    `register()` (the lifespan above: "restore only the project 'list'") — so **every
    project not opened even once since the restart is False**, and that is the common
    state for this product, which is redeployed for every workshop. Second, stop's
    failures are deliberately swallowed, so if the driver shutdown fails, the rmtree is skipped along with it.

    Measured (2026-08-19, deployed instance): `/opt/aipds/workspaces/` still had directories
    for 6 projects that were not in S3. We had promised users that "chat history and documents
    are permanently deleted" (`project.deleteConfirmBody`).

    **Leftovers raise.** `ignore_errors=True` exists to go as far as it can without stopping
    at the first failure, so it cannot serve as a success signal — if a permission error deep
    in node_modules is reported as success, the documents remain while we say "deleted".
    `ProtoHost.purge` has the same shape for the same reason.

    **`reject_unsafe_segment` comes first.** This is a spot where a single URL parameter
    becomes a directory name, and `pathlib` does not normalize, so `".."` really is the
    parent — without validation, deleting one project becomes an rmtree of all of
    `workspaces/`. The route blocks it too (that is the first line of defense), but a
    dangerous primitive refuses to become a weapon no matter who calls it.

    The agent home (per-project CLI config dir — transcripts and session state) is deleted
    here too. If left behind, the first turn of a project recreated with the same id would `--resume` the old conversation.
    """
    reject_unsafe_segment(project_id)
    target = _workspaces_dir() / project_id
    if target.is_dir():
        await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
        if target.exists():
            raise RuntimeError(f"workspace purge left residue: {target}")
    await agent_home.purge_project(project_id)
    active = launcher.current()
    if active is not None:
        active.revoke_project(project_id)


def _discovery_config_dir() -> Path:
    return Path(os.environ.get("AIPDS_DISCOVERY_CONFIG_DIR",
                               "~/aipds-discovery-config")).expanduser()


# The Discovery driver. There is only one: the Claude Agent SDK — it is the runtime the
# AI-PLC rules assume, and the `strands` fallback that used to be here has been removed.
#
# **Why the fallback was removed.** It was kept as an escape hatch to revert with a single
# env var during a workshop, but in reality it had rotted into something that could not be
# pulled: StrandsDriver did not accept `language`, so English projects ran in Korean (the
# defect 7f33652 fixed), and without session_store, pending_store, and answer_store,
# transcript mirroring and question/answer restore were all missing. Those facts only come
# to light the moment it is actually pulled — that is, when an incident happens mid-workshop.
# The rollback that works is git revert + `aipds-update` (the deployment points at a branch,
# so it reverts without replacing the instance).
#
# Monkeypatchable in tests: this function itself is swapped for a fake agent_factory.
def driver_factory(project_id: str, local_root: Path):
    return ClaudeDriver(
        workspace=str(local_root),
        rules_dir=_rules_dir(),
        # Per-project config dir (aipds/agent_home.py). The shared discovery-config is
        # passed as the source of its contents, and the driver copies it on each connection.
        config_dir=str(agent_home.discovery(project_id).prepare().config),
        shared_config_dir=str(_discovery_config_dir()),
        launch=launcher.launch_for("discovery", project_id),
        s3=s3_store_factory(project_id),
        # cli_model_id is applied here (not inside project_model) — `[1m]` is a CLI
        # alias, not a Bedrock model id, so if it flowed into the survey generation path
        # (BedrockModel), which uses project_model as is, it would become a ValidationException.
        anthropic_model=cli_model_id(project_model(project_id)),
        effort=project_effort(project_id),
        language=project_language(project_id),
    )


# ---- prototype build/hosting wiring (routes/prototypes.py) ----

# Registry of live build sessions — (pid, slug) → PrototypeSession. In memory: it is
# lost when the backend restarts (the build directory and transcript remain and continue via resume).
proto_sessions: dict = {}

_proto_host_singleton = None


def _proto_root() -> Path:
    return Path(os.environ.get("AIPDS_PROTO_ROOT",
                               "~/aipds-protos")).expanduser()


def _proto_config_dir() -> Path:
    """CLAUDE_CONFIG_DIR dedicated to the build agent. If unset, the bundled binary reads
    the backend user's ~/.claude (personal skills/agents/CLAUDE.md)."""
    return Path(os.environ.get("AIPDS_PROTO_CONFIG_DIR",
                               "~/aipds-proto-config")).expanduser()


def _proto_permission_mode() -> str:
    """Builds run unattended — there is nobody to grant approvals, so bypassPermissions is
    the default. To tighten it, override with the environment variable (an invalid value raises ValueError immediately)."""
    from aipds.proto.builder import DEFAULT_PERMISSION_MODE
    return os.environ.get("AIPDS_PROTO_PERMISSION_MODE",
                          DEFAULT_PERMISSION_MODE)


# Global cap on concurrent builds (monkeypatchable in tests).
from aipds.proto.limits import BuildSemaphore  # noqa: E402

build_semaphore = BuildSemaphore(
    max_concurrent=int(os.environ.get("AIPDS_PROTO_MAX_CONCURRENT", "10")))


def proto_host():
    """ProtoHost singleton (monkeypatchable in tests)."""
    global _proto_host_singleton
    if _proto_host_singleton is None:
        from aipds.proto.host import ProtoHost
        # The wrapper is decided by the startup check (lifespan). The lifespan runs the check
        # first so this singleton is not created before it.
        _proto_host_singleton = ProtoHost(root=_proto_root(), launcher=launcher.current())
    return _proto_host_singleton


def proto_session_factory(project_id: str, slug: str):
    """Assemble a PrototypeSession (monkeypatchable in tests). There is no VM — the builder
    launches a claude subprocess inside the backend process."""
    from aipds.proto.builder import PrototypeBuilder
    from aipds.proto.session import PrototypeSession
    from aipds.proto.session_store import S3SessionStore
    from aipds.proto.history import AnswerLog

    s3 = s3_store_factory(project_id)
    build_root = _proto_root()
    config_dir = _proto_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    durable = bool(os.environ.get("AIPDS_S3_BUCKET"))
    store = S3SessionStore(s3, slug=slug) if durable else None
    # Same condition as the transcript — answer records only mean something when joined with the transcript.
    answer_log = AnswerLog(s3, slug) if durable else None
    # Read once and give the builder and the session the same value — if the two disagree,
    # the language of the prompt and of the tool descriptions diverges.
    language = project_language(project_id)

    def builder_factory(session_id: str, resume: bool):
        return PrototypeBuilder(
            workspace=str(build_root / project_id / slug),
            # Same layout as Discovery — a per-prototype config dir, with the shared proto-config as the source.
            config_dir=str(agent_home.build(project_id, slug).prepare().config),
            shared_config_dir=str(config_dir),
            launch=launcher.launch_for("build", project_id, slug),
            session_id=session_id,
            resume=resume,
            session_store=store,
            answer_log=answer_log,
            # The CLI-specific assembly happens here for the same reason as in driver_factory.
            anthropic_model=cli_model_id(project_model(project_id)),
            # Builds also run with the same model/effort pair as Discovery — the values chosen in the project.
            effort=project_effort(project_id),
            language=language,
            permission_mode=_proto_permission_mode(),
        )

    return PrototypeSession(
        project_id=project_id, slug=slug, s3=s3,
        build_root=build_root,
        builder_factory=builder_factory,
        semaphore=build_semaphore,
        language=language,
        design_profiles=design_profile_store(),
    )


# ---- validation survey wiring (routes/surveys.py) ----


def surveys_root_s3_factory() -> S3StoreLike:
    """Bucket-root store: the token index must be readable before we know
    which project a token belongs to."""
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = s3_client(region)
    return S3Store(bucket=bucket, prefix="", client=client)


def survey_store_factory(project_id: str, slug: str):
    from aipds.survey.store import SurveyStore
    return SurveyStore(s3_store_factory(project_id), surveys_root_s3_factory(),
                       slug=slug, project_id=project_id,
                       language=project_language(project_id))


def questionnaire_agent_factory(project_id: str):
    """A one-shot `async (prompt) -> str` callable. Deliberately NOT
    the Discovery driver: that one carries the AI-PLC rule prompt, workspace tools, and
    session management, and a stateless generation call needs none of them.

    Why it takes project_id: question generation must also run on that project's model.
    Previously it read os.environ["ANTHROPIC_MODEL"] directly, so even with a per-project
    model chosen, this path alone used the global env.
    """
    return one_shot_agent_factory(project_id, max_tokens=8000)


def handoff_writer_factory(project_id: str):
    """Single-shot caller that writes the handoff package (open decisions and options).

    Same shape as question generation, with a different output cap. Models from Opus 5.5 on
    cannot turn thinking off, and thinking tokens count toward max_tokens — measured (ws-g,
    Opus 5.5), 32000 cut off at 25,279 characters of PRD body (MaxTokensReachedException).
    Together with per-stage length caps (handoff/package), we give it 64000. The call runs in
    a background task outside the request.

    The read limit differs too. Strands BedrockModel cuts the connection if nothing arrives on
    the stream for 120 seconds — measured (ws-g, 25 sources, 154KB), the first output came later
    than that and it failed twice with `The read operation timed out`. We give it the same value
    as the generation cap (package.CALL_TIMEOUT_S). It is a background task, not a request, so a long wait blocks nothing.
    """
    from aipds.handoff.package import CALL_TIMEOUT_S
    return one_shot_agent_factory(project_id, max_tokens=64000, read_timeout=CALL_TIMEOUT_S)


def one_shot_agent_factory(project_id: str, *, max_tokens: int,
                           read_timeout: int | None = None):
    """`async (prompt, progress=None) -> str` running on that project's model. No tools, no session.

    If `read_timeout` is None, the Strands default (120 seconds) applies. If `progress(char count, thinking)`
    is given, it is called on every stream chunk — the handoff package uses it to show stage progress on screen.
    """
    model_id = project_model(project_id)

    async def call(prompt: str, progress=None) -> str:
        if not model_id:
            # This is the only point that requires a model (the other two pass None
            # through as the SDK default). The route wraps it in a 502, and this sentence
            # stays in the log to say the cause is the project settings.
            raise RuntimeError(
                f"no model for project {project_id!r}: neither the project's "
                "model_id nor ANTHROPIC_MODEL is set")
        from strands import Agent
        from strands.models import BedrockModel
        client: dict = {}
        if read_timeout is not None:
            from botocore.config import Config
            client["boto_client_config"] = Config(
                read_timeout=read_timeout, connect_timeout=10,
                retries={"max_attempts": 3, "mode": "standard"})
        model = BedrockModel(model_id=model_id, max_tokens=max_tokens, **client)

        def on_event(**event) -> None:
            text = event.get("data")
            if isinstance(text, str) and text:
                progress(len(text), False)
            elif event.get("reasoning"):
                # Thinking content is an empty string by default (display "omitted") — treat it as an indicator, not content.
                progress(0, True)
        agent = Agent(model=model, tools=[],
                      callback_handler=on_event if progress is not None else None)
        result = await agent.invoke_async(prompt)
        return str(result)
    return call


def design_token_extractor():
    """Single-shot caller that extracts tokens from a prose-only DESIGN.md. None if there is no model.

    Same shape as `questionnaire_agent_factory` (single-shot Strands + BedrockModel), but
    the two differ in two ways.

    1. It cannot use `project_model()` — the brand profile is a single global sheet outside
       any project (design_profile.py), and there is no project at upload time. So it reads
       the default model the deployment exports (`ANTHROPIC_MODEL`, MODEL in
       infra/lib/backend-permissions.ts).
    2. With no model it returns None instead of raising RuntimeError. There, question
       generation is the very purpose of the request, so it should fail; here, failing would
       block **the upload itself** on extraction — applying only the prose without tokens is
       also a valid state (the route translates it into a warning).

    `temperature` is not passed — Opus 4.7+ and Sonnet 5 reject sampling parameters with a 400.
    max_tokens is small because the output is a single ```tokens block (at most 14 lines).
    """
    model_id = os.environ.get("ANTHROPIC_MODEL")
    if not model_id:
        return None

    async def call(prompt: str) -> str:
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(model_id=model_id, max_tokens=2000)
        agent = Agent(model=model, tools=[], callback_handler=None)
        return str(await agent.invoke_async(prompt))
    return call


async def make_workspace(project_id: str) -> Workspace:
    s3 = s3_store_factory(project_id)
    local_root = _workspaces_dir() / project_id
    # Session descriptor the in-process driver's S3SessionManager needs to
    # resume conversation state across /turns, /answers, and /pending. The
    # durable store's bucket/region, keyed by project_id.
    session = {
        "session_id": project_id,
        "bucket": os.environ.get("AIPDS_S3_BUCKET", ""),
        "region": os.environ.get("AIPDS_S3_REGION", "ap-northeast-2"),
        "prefix": "sessions",
    }
    driver = driver_factory(project_id, local_root)
    runner = AgentRunner(project_id=project_id, driver=driver, s3=s3,
                         local_root=local_root, session=session)

    async def mark(job) -> None:
        await save_marker(s3, job)

    # The marker exists to report "interrupted" after a restart (turn_marker.py), so with
    # no bucket there is nowhere to write it and nobody to read it.
    on_state = mark if durable_projects_enabled() else None
    return Workspace(runner, turns=TurnJobs(on_state=on_state))


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # First of all. Everything below reports failures through logs, and logs emitted before
    # the handler is attached are lost (see configure_logging — we really did lose them that way).
    configure_logging()
    # The launch wrapper (aipds/launcher.py). Everything that spawns processes (the proto_host
    # singleton, the driver/builder factories) reads this result, so it is decided before them.
    active_launcher = launcher.probe()
    launcher.set_current(active_launcher)
    if active_launcher is None:
        launcher.sweep_if_installed()
    broker = active_launcher.broker if active_launcher is not None else None
    if broker is not None:
        try:
            await broker.start()
        except Exception:
            # If the port cannot be opened, sandboxed processes do not get credentials. Startup is
            # not blocked — if IMDS is still open they work through it (same nature as launcher.probe's warning).
            _log.exception("credential endpoint failed to start")
            broker = None
    # At startup, restore only the project 'list' from the S3 manifest. Workspaces are
    # lazily initialized on the first request (deps.ensure_workspace) — keeps startup fast.
    # A restore failure does not block startup.
    if durable_projects_enabled():
        try:
            for p in await restore_projects(projects_root_s3_factory()):
                registry.register(p.project_id, p.name, created_at=p.created_at,
                                  model_id=p.model_id, language=p.language,
                                  effort=p.effort, owner=p.owner, members=p.members)
        except Exception:
            _log.exception("project-list restore failed; starting with empty registry")
    # Clean up orphaned hosting processes left by in-memory sessions lost to the restart
    # (replaces the old orphan-VM sweep — those children are now our process's children).
    # What the previous backend was hosting. Read **before** the sweep below deletes the pid
    # files — re-hosting (_rehost below) carries it over as the desired state (proto/hosting.py).
    try:
        previously_running = proto_host().previously_running()
    except Exception:
        _log.exception("reading previously hosted prototypes failed")
        previously_running = []
    try:
        swept = proto_host().sweep_orphans()
        if swept:
            _log.info("swept %d orphan prototype hosting process(es)", swept)
    except Exception:
        _log.exception("orphan hosting sweep failed; continuing startup")
    # Reload prototype access tokens from disk. This pairs with the sweep above but runs in
    # the opposite direction: the sweep discards what lost its meaning on restart (orphan
    # processes), and this revives what must survive a restart (links already distributed).
    # Without it, when the backend restarts mid-workshop, every URL handed out to participants
    # becomes a 404, and re-hosting does not fix it — because the token in that URL does not change.
    try:
        loaded = proto_host().load_tokens()
        if loaded:
            _log.info("loaded %d prototype access token(s)", loaded)
    except Exception:
        _log.exception("prototype token load failed; continuing startup")
    # Bring back up the prototypes that should be hosted (proto/hosting.rehost_desired).
    # Reviving only the tokens leaves the link alive but no server behind it — a restart used
    # to mean "participants stare at a 502 while the PM presses start hosting again on every card".
    # Runs in the background so it does not block startup, bringing them up one at a time (the reason is in the function docstring).
    rehost_task = None
    if durable_projects_enabled():
        from aipds.proto.hosting import adopt_local_state, rehost_desired

        async def _rehost() -> None:
            try:
                await adopt_local_state(previously_running)
                count = await rehost_desired()
                if count:
                    _log.info("re-hosted %d prototype(s) after startup", count)
            except Exception:
                _log.exception("re-hosting prototypes after startup failed")

        rehost_task = asyncio.create_task(_rehost())
    yield
    if rehost_task is not None and not rehost_task.done():
        rehost_task.cancel()
    if broker is not None:
        await broker.close()
    from aipds.routes.proto_public import aclose_proxy_http
    await aclose_proxy_http()


def _docs_openapi_url() -> str | None:
    """Whether the schema/docs UI (/openapi.json, /docs, /redoc) is enabled.

    FastAPI registers these routes itself — they do not go through app.include_router(...,
    dependencies=_AUTH), so they return an anonymous 200 regardless of the auth wiring
    below. In a deployment with auth configured, that amounts to handing the app's entire
    route table, parameters, and schemas to anonymous visitors, so we turn them off
    (openapi_url=None turns off /docs and /redoc as well — both are registered on the
    premise of openapi_url). In local development (auth not configured) they are useful, so they stay on.

    Why it is a separate function: FastAPI(...)'s openapi_url argument is evaluated exactly
    once at import time, so to verify this logic in tests with only monkeypatch(cognito_config),
    it has to be a pure function separate from app creation — importlib.reload() of the whole
    module recreates other module-global singletons such as registry, breaking other test
    files that already reference those objects (measured: a flood of KeyErrors).
    """
    return None if cognito_config() else "/openapi.json"


# cognito_config() is the same pure env read as on every request, so calling it at import
# time is safe too (if the state is half-configured, clamping down right here with a
# RuntimeError is better than waiting until the first request).
app = FastAPI(title="AI-PDS", lifespan=_lifespan,
             openapi_url=_docs_openapi_url())

# CORS: the frontend (:3000 in dev, Playwright e2e) calls this API (:8000)
# from a real browser and needs the preflight/simple-request headers.
# allow_credentials=True: frontend/lib/auth.ts's CREDENTIALS constant sends
# `credentials: "include"` on every client call (the same-origin /api proxy
# needs the browser to send its httpOnly session cookie so it can translate
# that into Authorization: Bearer -- see app/api/[...path]/route.ts). Without
# this the browser silently drops every cross-origin response in the
# README's documented default setup (:3000 -> :8000). This is safe only
# because allow_origins below is an explicit allowlist, never "*" --
# Starlette's CORSMiddleware refuses to combine allow_credentials with a
# wildcard origin's shortcut path anyway (it falls back to echoing the
# specific Origin), but keep the allowlist explicit regardless.
_cors_origins = [
    o.strip()
    for o in os.environ.get("AIPDS_CORS_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
# Records the identity of authenticated requests (aipds/access_log.py). Reads, at response
# start, the requester that require_user planted in the request state.
from aipds.access_log import AccessLogMiddleware  # noqa: E402
app.add_middleware(AccessLogMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ---- Router registration ----
#
# Authentication is attached here, not in route bodies: applying it as router-level
# dependencies protects everything without touching a single route function. In
# local/tests where auth is not configured, require_user lets everything through (auth/deps.py).
from aipds.auth.deps import require_user  # noqa: E402
from aipds.project_access import require_project_access  # noqa: E402
from fastapi import Depends  # noqa: E402

_AUTH = [Depends(require_user)]
# Routers that have `{pid}` routes. After authentication they check project access (owner/member/admin)
# (project_access.py). It is attached at the same spot for the same reason as auth — it applies
# without exception and without touching route bodies. test_project_access_route_coverage catches a missed router.
_PROJECT_AUTH = [Depends(require_user), Depends(require_project_access)]

from aipds.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router, dependencies=_PROJECT_AUTH)
app.include_router(artifacts.router, dependencies=_PROJECT_AUTH)

# Project transfer (export/import). It is separate from projects.router because of contract,
# not file size — that one is the project lifecycle (create/list/delete), while this one is a
# different concern: the bundle format and S3 staging. Registration order does not matter:
# `/projects/import` differs from `/projects/{pid}` in method (POST vs GET) and segment count.
from aipds.routes import transfer  # noqa: E402
app.include_router(transfer.router, dependencies=_PROJECT_AUTH)

# The handoff tab. It is a read-only judgment over artifacts, so it is the same kind as artifacts, but the
# handoff package (open decisions, supplementary questions, download) grows under this prefix (plans/2026-10-09-handoff-package.md).
from aipds.routes import handoff  # noqa: E402
app.include_router(handoff.router, dependencies=_PROJECT_AUTH)

from aipds.routes import answers  # noqa: E402
app.include_router(answers.router, dependencies=_PROJECT_AUTH)

from aipds.routes import approval  # noqa: E402
app.include_router(approval.router, dependencies=_PROJECT_AUTH)

from aipds.routes import turns  # noqa: E402
app.include_router(turns.router, dependencies=_PROJECT_AUTH)

from aipds.routes import discovery  # noqa: E402
app.include_router(discovery.router, dependencies=_PROJECT_AUTH)

from aipds.routes import history  # noqa: E402
app.include_router(history.router, dependencies=_PROJECT_AUTH)

from aipds.routes import uploads  # noqa: E402
app.include_router(uploads.router, dependencies=_PROJECT_AUTH)

from aipds.routes import prototypes  # noqa: E402
app.include_router(prototypes.router, dependencies=_PROJECT_AUTH)

from aipds.routes import surveys  # noqa: E402
app.include_router(surveys.router, dependencies=_PROJECT_AUTH)

from aipds.routes import admin_users  # noqa: E402
app.include_router(admin_users.router, dependencies=_AUTH)

# Operations on one's own account. Unlike admin_users, this is not admin-only — a pm must
# also be able to change their own password. So only _AUTH (require_user) is attached.
from aipds.routes import account  # noqa: E402
app.include_router(account.router, dependencies=_AUTH)

from aipds.routes import models as models_routes  # noqa: E402
app.include_router(models_routes.router, dependencies=_AUTH)
app.include_router(models_routes.admin_router, dependencies=_AUTH)

from aipds.routes import design as design_routes  # noqa: E402
app.include_router(design_routes.admin_router, dependencies=_AUTH)

# ---- Public (unauthenticated) routers — exactly two (2 routers, 3 paths — see below) ----
#
# Adding a router here is the same as publishing it to the internet. Both paths serve end
# users without accounts: they receive a survey link and respond (surveys_public), and they
# actually try out a prototype under evaluation (proto_public).
# tests/test_auth_route_coverage.py enforces this list.
from aipds.routes import surveys_public  # noqa: E402
app.include_router(surveys_public.router)

from aipds.routes import proto_public  # noqa: E402
app.include_router(proto_public.router)
