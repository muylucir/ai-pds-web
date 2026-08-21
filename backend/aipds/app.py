# backend/aipds/app.py
from __future__ import annotations
import asyncio
import logging
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
import boto3
from dotenv import load_dotenv
from fastapi import FastAPI

# backend/.env (gitignored, optional) feeds the AIPDS_*/ANTHROPIC_MODEL
# settings read via os.environ below. Real environment variables win over the
# file (override=False) so shell exports / container env keep working as
# before, and a missing file is a silent no-op (local mode needs no config).
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from fastapi.middleware.cors import CORSMiddleware
from aipds.workspace import ProjectRegistry, Workspace
from aipds.runner import AgentRunner
from aipds.agent.claude_driver import ClaudeDriver
from aipds.cli_settings import cli_model_id
from aipds.s3store import S3Store, S3StoreLike
from aipds.project_store import restore_projects
from aipds.pathsafe import reject_unsafe_segment
from aipds.turn_handles import TurnHandleStore

_log = logging.getLogger(__name__)

#: The application log level. INFO by default -- most of what diagnosis needs is at
#: that level.
_LOG_LEVEL_ENV = "AIPDS_LOG_LEVEL"

#: The marker by which configure_logging recognises the handler it attached. It keeps
#: handlers from stacking up on a second call and printing the same line several times
#: (uvicorn --reload, or TestClient running the lifespan twice).
_HANDLER_TAG = "aipds"


def configure_logging() -> None:
    """Attach a handler to the root logger.

    Without one the application log effectively disappears. uvicorn configures only its
    own loggers and does not touch the root, so with no handler an INFO record is dropped
    silently and only WARNING leaks out unformatted through Python's lastResort.

    Measured: the `aipds` logger's output in a workshop box's journald was **0 lines out
    of 2905**. A chat-history restore bug was being chased at the time, and the log
    records that pointed at the cause (`_resolve_resume`'s resume decision, the SDK's
    "dropping mirror frame" warning) all vanished through this hole, forcing repeated
    reproduction and measurement in production.

    That warning is why the SDK logger is opened alongside ours: a transcript mirroring
    failure is reported only through the `claude_agent_sdk` logger, so opening ours alone
    still leaves "the frame was dropped" and "the frame never arrived"
    indistinguishable.

    A handler is added to the root without overhauling uvicorn's configuration -- the
    point is to leave the access log's shape alone.
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
    # Both loggers are opened explicitly. The root level alone is not enough -- if a
    # third party has raised its own logger's level, records are filtered even with the
    # root open.
    for name in ("aipds", "claude_agent_sdk"):
        logging.getLogger(name).setLevel(level)


registry = ProjectRegistry()

# Turn input handles. They exist to keep long chat text out of the SSE URL -- the
# turn_handles.py header records the measured cause, an HTTP 431. In-memory with the same
# character as proto_sessions: a value that lives for seconds, and losing it on a restart
# fails only that turn.
turn_handles = TurnHandleStore()


# Monkeypatchable in tests to inject a FakeS3Store (no AWS). Durable store keeps
# the project's aiplc-docs/prototype/uploads subtree (S3 = source of truth); the
# in-process AgentRunner restores it to a local workspace at the start of a turn.
def s3_store_factory(project_id: str) -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix=f"projects/{project_id}/", client=client)


# Monkeypatchable in tests. Scoped to the `sessions/` prefix
# that S3SessionManager writes; the backend only READS them for history.
def session_s3_factory() -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="sessions/", client=client)


# For manifests and deletion -- the root store, which sees all of projects/.
# Monkeypatched in tests.
def projects_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="projects/", client=client)


# For the model catalogue -- a bucket-root store. The catalogue has to exist before any
# project does (the project creation screen reads it with no project), so it lives outside
# projects/. Monkeypatched in tests.
def models_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="", client=client)


# For the brand profile -- a bucket-root store. It is one profile.json under design/ and
# sits outside projects/ for the same reason as the model catalogue. Monkeypatched in
# tests.
def design_root_s3_factory() -> S3StoreLike:
    region = os.environ.get("AIPDS_S3_REGION", "ap-northeast-2")
    bucket = os.environ.get("AIPDS_S3_BUCKET", "")
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="", client=client)


def design_profile_store():
    """The DesignProfileStore factory (monkeypatchable in tests).

    With no bucket it returns a read-only (None) store -- the same reason as
    model_catalog(): a build session's start() must not be blocked in local development
    without a bucket.
    """
    from aipds.design_profile import DesignProfileStore
    if not durable_projects_enabled():
        return DesignProfileStore(None)
    return DesignProfileStore(design_root_s3_factory())


def model_catalog():
    """The ModelCatalog factory (monkeypatchable in tests).

    With no bucket it returns a read-only catalogue (seeds only) -- local development has
    to be able to create a project with no configuration at all, and that screen's
    combobox has to be populated too.
    """
    from aipds.model_catalog import ModelCatalog
    if not durable_projects_enabled():
        return ModelCatalog(None)
    return ModelCatalog(models_root_s3_factory())


def project_model(project_id: str) -> str | None:
    """The Bedrock model id this project runs on.

    The fallback order is project -> env -> None, and each slot has a reason:
      - project: the value chosen at creation (copied into the manifest).
      - env (ANTHROPIC_MODEL): the path by which a project created before this feature
        keeps running. In a deployment, MODEL in backend-permissions.ts supplies it.
      - None: local development with no env either. Given None, the driver does not set
        ANTHROPIC_MODEL and falls through to the SDK default (the previous behaviour).
    """
    return registry.get_model_id(project_id) or os.environ.get("ANTHROPIC_MODEL")


def project_language(project_id: str) -> str:
    """This project's output language ("ko"|"en"). Always has a value.

    Unlike project_model there is no env fallback: a language has no reason to have a
    process-global default (a model being set by the deployment is natural, but a language
    is a property of the project), and the registry already settles it to "ko".

    This function exists so that its callers (driver_factory, proto_session_factory,
    survey_store_factory) do not touch the registry directly -- keeping the same shape as
    project_model.
    """
    return registry.get_language(project_id)


# ---- Authentication (routes/*, auth/deps.py) ----

_jwks_singleton = None


def cognito_config() -> dict | None:
    """The Cognito configuration. With neither set, None = authentication bypass.

    The same discipline as durable_projects_enabled(): with none of the required env
    present, the whole feature is skipped so local and tests run with no configuration.

    But having **only one** of the pool id and the client id is not "unconfigured", it is
    a deployment accident. This case used to be treated as None (bypass) too, which leaves
    authentication off while every request quietly passes as a virtual admin
    (LOCAL_PRINCIPAL) -- no crash, no warning, no trace. So a half-set configuration
    raises immediately (fail-closed): those requests become 500s, but a visible failure
    beats admin privileges leaking with nobody aware. cognito_config() is called on every
    request (require_user), so the moment a deployment script drops one of the two
    variables this exception appears, without a restart.
    """
    pool = os.environ.get("AIPDS_COGNITO_USER_POOL_ID", "").strip()
    client = os.environ.get("AIPDS_COGNITO_CLIENT_ID", "").strip()
    if not pool and not client:
        return None
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
    """The JWKS cache singleton (monkeypatchable in tests)."""
    global _jwks_singleton
    if _jwks_singleton is None:
        from aipds.auth.verifier import JwksCache
        cfg = cognito_config() or {}
        _jwks_singleton = JwksCache(region=cfg.get("region", "ap-northeast-2"),
                                    user_pool_id=cfg.get("user_pool_id", ""))
    return _jwks_singleton


def cognito_admin():
    """The CognitoAdmin factory (monkeypatchable in tests).

    Why it is not a singleton: a boto3 client is thread-safe, but tests have to be able to
    swap in a fake per request, and the construction cost is negligible.
    """
    from aipds.auth.cognito import CognitoAdmin
    cfg = cognito_config()
    if cfg is None:
        raise RuntimeError(
            "user management requires AIPDS_COGNITO_USER_POOL_ID / "
            "AIPDS_COGNITO_CLIENT_ID")
    client = boto3.client("cognito-idp", region_name=cfg["region"])
    return CognitoAdmin(client, cfg["user_pool_id"])


def durable_projects_enabled() -> bool:
    """With no bucket configured (local, tests), list persistence is skipped entirely."""
    return bool(os.environ.get("AIPDS_S3_BUCKET"))


def _rules_dir() -> str:
    #: `steering-files/` is a **submodule** of the upstream repo
    #: (aws-samples/sample-ai-plc) and `aiplc-rules/` is a directory inside it. Being a
    #: submodule, a clone alone leaves it empty -- `git submodule update --init` is in
    #: both the boot path (infra/lib/user-data.ts) and the update path
    #: (infra/scripts/aipds-update). Left empty, place_rules cannot find
    #: core-workflow.md, and that failure only shows up on the first turn.
    default = str(Path(__file__).resolve().parent.parent.parent
                  / "steering-files" / "aiplc-rules")
    return os.environ.get("AIPDS_RULES_DIR", default)


def _workspaces_dir() -> Path:
    root = os.environ.get("AIPDS_WORKSPACES_DIR")
    return Path(root) if root else Path(tempfile.gettempdir()) / "aipds-workspaces"


async def purge_local_workspace(project_id: str) -> None:
    """Delete this project's local workspace directory. Idempotent.

    **Why `runner.stop()` is not enough.** It contains an rmtree too, but two conditions
    get in the way. First, the delete route calls stop only when
    `registry.has_workspace(pid)`, and that flag is set only by `attach()` while the
    startup restore calls `register()` alone (the lifespan above: "only the project *list*
    is restored") -- meaning **every project not opened since the restart is False**, and
    that is the common state for a product redeployed for every workshop. Second, a
    failure in stop is swallowed deliberately, so a failed driver shutdown skips the
    rmtree along with it.

    Measured (2026-08-19, a deployed instance): `/opt/aipds/workspaces/` still held
    directories for 6 projects that were not in S3. The user has been promised that "chat
    history and documents are permanently deleted" (`project.deleteConfirmBody`).

    **Leftovers raise.** `ignore_errors=True` exists to get as far as possible rather than
    stopping at the first failure, so it cannot be used as a success signal -- a
    permission error deep inside node_modules reported as success turns into "deleted"
    with the documents still there. `ProtoHost.purge` has the same shape for the same
    reason.

    **`reject_unsafe_segment` comes first.** This is a place where one URL parameter
    becomes a directory name, and `pathlib` does not normalise, so `".."` really is the
    parent -- without validation, deleting one project becomes an rmtree of all of
    `workspaces/`. The route blocks it too (that is the first line of defence), but a
    dangerous primitive refuses to be a weapon whoever calls it.
    """
    reject_unsafe_segment(project_id)
    target = _workspaces_dir() / project_id
    if not target.is_dir():
        return
    await asyncio.to_thread(shutil.rmtree, target, ignore_errors=True)
    if target.exists():
        raise RuntimeError(f"workspace purge left residue: {target}")


def _discovery_config_dir() -> Path:
    return Path(os.environ.get("AIPDS_DISCOVERY_CONFIG_DIR",
                               "~/aipds-discovery-config")).expanduser()


# The Discovery driver. There is exactly one, the Claude Agent SDK -- it is the execution
# environment the AI-PLC rules assume, and the `strands` fallback that used to be here was
# deleted.
#
# **Why the fallback was removed.** It was kept as an escape route, a single env var to
# revert during a workshop, but it had in fact rotted into a state that could not be
# pulled: StrandsDriver does not accept `language`, so an English project would run in
# Korean (the very defect 7f33652 fixed), and with no session_store, pending_store or
# answer_store, transcript mirroring and question/answer restore drop out entirely. That
# fact would first surface at the moment it was actually pulled -- that is, when something
# went wrong mid-workshop. The rollback that works is git revert plus `aipds-update` (the
# deployment points at a branch, so it reverts without replacing the instance).
#
# Monkeypatchable in tests: this function itself is swapped for a fake agent_factory.
def driver_factory(project_id: str, local_root: Path):
    return ClaudeDriver(
        workspace=str(local_root),
        rules_dir=_rules_dir(),
        config_dir=str(_discovery_config_dir()),
        s3=s3_store_factory(project_id),
        # cli_model_id is applied here rather than inside project_model -- `[1m]` is a
        # CLI alias and not a Bedrock model id, so leaking it into the survey generation
        # path (BedrockModel), which uses project_model as-is, would be a
        # ValidationException.
        anthropic_model=cli_model_id(project_model(project_id)),
        language=project_language(project_id),
    )


# ---- prototype build/hosting wiring (routes/prototypes.py) ----

# The registry of live build sessions -- (pid, slug) -> PrototypeSession. In-memory: it
# dies with a backend restart (the build directory and transcript survive, and resume
# picks up from them).
proto_sessions: dict = {}

_proto_host_singleton = None


def _proto_root() -> Path:
    return Path(os.environ.get("AIPDS_PROTO_ROOT",
                               "~/aipds-protos")).expanduser()


def _proto_config_dir() -> Path:
    """The CLAUDE_CONFIG_DIR dedicated to the build agent. Unset, the bundled binary reads
    the backend user's ~/.claude (their personal skills/agents/CLAUDE.md)."""
    return Path(os.environ.get("AIPDS_PROTO_CONFIG_DIR",
                               "~/aipds-proto-config")).expanduser()


def _proto_permission_mode() -> str:
    """A build runs unattended -- with nobody there to approve, bypassPermissions is the
    default. Tighten it by overriding the environment variable (a bad value is an
    immediate ValueError)."""
    from aipds.proto.builder import DEFAULT_PERMISSION_MODE
    return os.environ.get("AIPDS_PROTO_PERMISSION_MODE",
                          DEFAULT_PERMISSION_MODE)


# The global cap on concurrent builds (monkeypatchable in tests).
from aipds.proto.limits import BuildSemaphore  # noqa: E402

build_semaphore = BuildSemaphore(
    max_concurrent=int(os.environ.get("AIPDS_PROTO_MAX_CONCURRENT", "10")))


def proto_host():
    """The ProtoHost singleton (monkeypatchable in tests)."""
    global _proto_host_singleton
    if _proto_host_singleton is None:
        from aipds.proto.host import ProtoHost
        _proto_host_singleton = ProtoHost(root=_proto_root())
    return _proto_host_singleton


def proto_session_factory(project_id: str, slug: str):
    """Assemble a PrototypeSession (monkeypatchable in tests). There is no VM -- the builder
    spawns a claude subprocess inside the backend process."""
    from aipds.proto.builder import PrototypeBuilder
    from aipds.proto.session import PrototypeSession
    from aipds.proto.session_store import S3SessionStore

    s3 = s3_store_factory(project_id)
    build_root = _proto_root()
    config_dir = _proto_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    store = S3SessionStore(s3, slug=slug) if os.environ.get("AIPDS_S3_BUCKET") else None
    # Read once and given to both the builder and the session -- if the two diverge, the
    # prompt and the tool descriptions end up in different languages.
    language = project_language(project_id)

    def builder_factory(session_id: str, resume: bool):
        return PrototypeBuilder(
            workspace=str(build_root / project_id / slug),
            config_dir=str(config_dir),
            session_id=session_id,
            resume=resume,
            session_store=store,
            # The CLI-specific assembly happens here for the same reason as in
            # driver_factory.
            anthropic_model=cli_model_id(project_model(project_id)),
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
    client = boto3.client("s3", region_name=region)
    return S3Store(bucket=bucket, prefix="", client=client)


def survey_store_factory(project_id: str, slug: str):
    from aipds.survey.store import SurveyStore
    return SurveyStore(s3_store_factory(project_id), surveys_root_s3_factory(),
                       slug=slug, project_id=project_id,
                       language=project_language(project_id))


def questionnaire_agent_factory(project_id: str):
    """A one-shot `async (prompt) -> str` callable. Deliberately **not** the Discovery
    driver: that one carries the AI-PLC rule prompt, the workspace tools and session
    management, and a stateless generation call needs none of it.

    Why it takes project_id: question generation has to run on that project's model too.
    It used to read os.environ["ANTHROPIC_MODEL"] directly, so this one path used the
    global env even when a per-project model had been chosen.
    """
    model_id = project_model(project_id)

    async def call(prompt: str) -> str:
        if not model_id:
            # This is the one place a model is mandatory (the other two pass None
            # through to the SDK default). The route wraps it as a 502, and this sentence
            # remains in the log to say the cause is the project's configuration.
            raise RuntimeError(
                f"no model for project {project_id!r}: neither the project's "
                "model_id nor ANTHROPIC_MODEL is set")
        from strands import Agent
        from strands.models import BedrockModel
        model = BedrockModel(model_id=model_id, max_tokens=8000)
        agent = Agent(model=model, tools=[], callback_handler=None)
        result = await agent.invoke_async(prompt)
        return str(result)
    return call


def design_token_extractor():
    """The one-shot caller that extracts tokens from a prose-only DESIGN.md. None when there
    is no model.

    The same shape as `questionnaire_agent_factory` (a one-shot Strands + BedrockModel),
    but two things differ.

    1. `project_model()` cannot be used -- the brand profile is a single global document
       outside any project (design_profile.py), and at upload time there is no project. So
       it reads the default model the deployment exports (`ANTHROPIC_MODEL`, MODEL in
       infra/lib/backend-permissions.ts).
    2. With no model it returns None rather than raising RuntimeError. There, question
       generation *is* the purpose of the request and so has to fail; here, failing would
       let extraction block **the upload itself** -- and applying the prose without tokens
       is a valid state too (the route translates it into a warning).

    `temperature` is not passed -- Opus 4.7+ and Sonnet 5 reject sampling parameters with
    a 400. max_tokens is small because the output is a single ```tokens block (at most 14
    lines).
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
    # resume conversation state across /message, /answers, and /pending. The
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
    return Workspace(runner)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # First of all. Everything below reports failure through the log, and a log record
    # from before the handler is attached is gone (see configure_logging -- that is how
    # records were actually lost).
    configure_logging()
    # On startup only the project *list* is restored from the S3 manifest. A workspace is
    # initialised lazily on its first request (deps.ensure_workspace) -- keeping startup
    # fast. A restore failure does not block startup.
    if durable_projects_enabled():
        try:
            for pid, name, created_at, model_id, language in await restore_projects(
                    projects_root_s3_factory()):
                registry.register(pid, name, created_at=created_at,
                                  model_id=model_id, language=language)
        except Exception:
            _log.exception("project-list restore failed; starting with empty registry")
    # Clean up orphaned hosting processes left behind by in-memory sessions that died
    # with the restart (the replacement for the old orphaned-VM sweep -- those children
    # are now children of our own process).
    try:
        swept = proto_host().sweep_orphans()
        if swept:
            _log.info("swept %d orphan prototype hosting process(es)", swept)
    except Exception:
        _log.exception("orphan hosting sweep failed; continuing startup")
    # Re-read prototype access tokens from disk. This pairs with the sweep above but runs
    # in the opposite direction: the sweep discards what the restart robbed of meaning
    # (orphaned processes), while this revives what has to survive a restart (links
    # already handed out). Without it, a backend restart mid-workshop turns every URL
    # given to participants into a 404, and re-hosting does not recover them -- because
    # the token inside those URLs does not change.
    try:
        loaded = proto_host().load_tokens()
        if loaded:
            _log.info("loaded %d prototype access token(s)", loaded)
    except Exception:
        _log.exception("prototype token load failed; continuing startup")
    yield


def _docs_openapi_url() -> str | None:
    """Whether the schema and docs UI (/openapi.json, /docs, /redoc) is enabled.

    FastAPI registers these routes itself -- they do not go through
    app.include_router(..., dependencies=_AUTH), so they return an anonymous 200
    regardless of the authentication wiring below. In a deployment with authentication
    configured that amounts to handing this app's entire route table, parameters and
    schemas to an anonymous visitor, so it is turned off (openapi_url=None also turns off
    /docs and /redoc, since both are registered on the premise of openapi_url). In local
    development (authentication unconfigured) it is useful, so it stays on.

    Why it is extracted as its own function: FastAPI(...)'s openapi_url argument is
    evaluated exactly once at import time, so verifying this logic in a test with nothing
    but a monkeypatch of cognito_config requires a pure function separate from app
    construction -- importlib.reload() of the whole module would rebuild other modules'
    global singletons such as the registry and break other test files already holding
    those objects (measured: KeyErrors en masse).
    """
    return None if cognito_config() else "/openapi.json"


# cognito_config() is the same pure env read as on every request, so calling it at import
# time is safe too (and if the configuration is half-set, tightening straight into a
# RuntimeError here is better than waiting for the first request).
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# ---- Router registration ----
#
# Authentication is attached here rather than in the route bodies: applied as
# router-level dependencies, every route is protected without touching a single route
# function. Where authentication is not configured (local and tests), require_user passes
# everything through (auth/deps.py).
from aipds.auth.deps import require_user  # noqa: E402
from fastapi import Depends  # noqa: E402

_AUTH = [Depends(require_user)]

from aipds.routes import projects, artifacts  # noqa: E402
app.include_router(projects.router, dependencies=_AUTH)
app.include_router(artifacts.router, dependencies=_AUTH)

from aipds.routes import answers  # noqa: E402
app.include_router(answers.router, dependencies=_AUTH)

from aipds.routes import approval  # noqa: E402
app.include_router(approval.router, dependencies=_AUTH)

from aipds.routes import turns  # noqa: E402
app.include_router(turns.router, dependencies=_AUTH)

from aipds.routes import discovery  # noqa: E402
app.include_router(discovery.router, dependencies=_AUTH)

from aipds.routes import history  # noqa: E402
app.include_router(history.router, dependencies=_AUTH)

from aipds.routes import uploads  # noqa: E402
app.include_router(uploads.router, dependencies=_AUTH)

from aipds.routes import prototypes  # noqa: E402
app.include_router(prototypes.router, dependencies=_AUTH)

from aipds.routes import surveys  # noqa: E402
app.include_router(surveys.router, dependencies=_AUTH)

from aipds.routes import admin_users  # noqa: E402
app.include_router(admin_users.router, dependencies=_AUTH)

from aipds.routes import models as models_routes  # noqa: E402
app.include_router(models_routes.router, dependencies=_AUTH)
app.include_router(models_routes.admin_router, dependencies=_AUTH)

from aipds.routes import design as design_routes  # noqa: E402
app.include_router(design_routes.admin_router, dependencies=_AUTH)

# ---- Public (unauthenticated) routers -- exactly two (2 routers, 3 paths; see below) --
#
# Adding a router here is the same as publishing it to the internet. Both exist for end
# users who have no account: receiving a survey link and responding (surveys_public), and
# actually using the prototype under evaluation (proto_public).
# tests/test_auth_route_coverage.py enforces this list.
from aipds.routes import surveys_public  # noqa: E402
app.include_router(surveys_public.router)

from aipds.routes import proto_public  # noqa: E402
app.include_router(proto_public.router)
