# backend/tests/test_sdk_available.py — the SDK is a NEW backend dependency
# (it used to live only in harness/). These tests fail loudly if the wheel is
# missing or its bundled Claude Code binary can't run on this platform --
# which is exactly the failure that would otherwise surface as an opaque
# "session start failed" 502 at workshop time.
from __future__ import annotations

import subprocess
from pathlib import Path


def test_sdk_imports_with_expected_options():
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient  # noqa: F401
    from claude_agent_sdk.types import AgentDefinition, HookMatcher  # noqa: F401

    # The four options this feature depends on must exist on the dataclass.
    fields = ClaudeAgentOptions.__dataclass_fields__
    for name in ("session_store", "resume", "setting_sources", "skills"):
        assert name in fields, f"ClaudeAgentOptions lacks {name}"


def test_bundled_binary_is_executable():
    import claude_agent_sdk

    binary = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    assert binary.is_file(), f"bundled binary missing at {binary}"
    # argv[0] is the literal name and `executable=` is what actually runs --
    # equivalent to putting the path in argv[0], but it keeps the argv static
    # in the source, which is the form command-injection analysis accepts. No
    # shell, and `binary` is a path inside the installed wheel.
    proc = subprocess.run(["claude", "--version"], executable=str(binary),
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "Claude Code" in proc.stdout


def test_the_class_names_our_translators_match_on_still_exist():
    """The translator branches on `type(msg).__name__`, not `isinstance` —
    a deliberate choice so that tests/fakes work without the real SDK. The price of that choice
    is that **it dies silently when a name changes**, and it actually has died:
    the `mirror_error` warning branch looked only at `"SystemMessage"`, but the parser always
    produced `MirrorErrorMessage`, so it was never once true (that fake overwrote __name__,
    so the tests were green).

    The same trap recurred in prototype builds: the four `Task*` kinds are also subclasses of
    `SystemMessage`, so they passed straight through the translator, and that is why the build
    screen looked frozen. This test is the net that catches that drift — always run it after upgrading the SDK.
    """
    import claude_agent_sdk as sdk

    for name in ("AssistantMessage", "UserMessage", "SystemMessage",
                 "ResultMessage", "StreamEvent", "MirrorErrorMessage",
                 "TaskStartedMessage", "TaskProgressMessage",
                 "TaskNotificationMessage", "TaskUpdatedMessage"):
        cls = getattr(sdk, name, None)
        assert cls is not None, f"SDK에 {name}이 없다 — 번역부의 분기가 죽는다"
        assert cls.__name__ == name, (
            f"{name}의 클래스 이름이 {cls.__name__}로 바뀌었다")


def test_the_fields_our_subagent_rows_read_still_exist():
    """Fields the subagent rows read. If even one disappears, rows show up without labels or
    never close (backend/aipds/agent_activity.py).

    `parent_tool_use_id` is especially important — its join with `Task*`'s `tool_use_id`
    is the only basis for attaching file paths to rows.
    """
    import claude_agent_sdk as sdk

    expected = {
        "AssistantMessage": ("parent_tool_use_id",),
        "TaskStartedMessage": ("task_id", "tool_use_id", "description"),
        "TaskProgressMessage": ("task_id", "tool_use_id", "last_tool_name"),
        "TaskNotificationMessage": ("task_id", "status", "summary"),
        "TaskUpdatedMessage": ("task_id", "status", "patch"),
    }
    for cls_name, fields in expected.items():
        dataclass_fields = getattr(sdk, cls_name).__dataclass_fields__
        for field in fields:
            assert field in dataclass_fields, f"{cls_name}에 {field}가 없다"


def test_forwarding_subagent_text_stays_an_option_we_can_choose():
    """Subagent text **stays off** (if three narratives mix into the bubble, the reason for moving
    the progress display out of the conversation flow is gone). For that decision to hold, the option must
    exist and default to False — if the default flips, behavior we did not choose comes in.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    field = ClaudeAgentOptions.__dataclass_fields__.get("forward_subagent_text")
    assert field is not None, "옵션이 사라졌다 — 서브에이전트 텍스트 정책을 재확인할 것"
    assert field.default is False


def test_the_bundled_cli_still_knows_the_background_switch():
    """`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` (cli_settings.cli_context_env) is an env the bundled CLI
    reads. If the name changes or disappears, the CLI silently ignores the unknown env, and subagents run
    in the background again by default — which shows up only as the screen missing progress.

    It looks at the strings inside the binary. The behavior itself (foreground even without the key, and
    with `true`) was confirmed by measurement (SDK 0.2.164); here we only guard that the switch still exists.
    """
    import mmap

    import claude_agent_sdk
    from aipds.cli_settings import DISABLE_BACKGROUND_TASKS_ENV

    binary = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    with binary.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        assert m.find(DISABLE_BACKGROUND_TASKS_ENV.encode()) != -1, (
            f"번들 CLI가 {DISABLE_BACKGROUND_TASKS_ENV}를 모른다 — 백그라운드 실행을 끄는 "
            "방법을 다시 확인할 것")
