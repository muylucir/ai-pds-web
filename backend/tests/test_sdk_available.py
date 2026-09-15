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
    """번역부는 `isinstance`가 아니라 `type(msg).__name__`으로 분기한다 —
    tests/fakes가 실제 SDK 없이 동작하게 하려는 의도적 선택이다. 그 선택의 값은
    **이름이 바뀌면 조용히 죽는다**는 것이고, 실제로 죽은 적이 있다:
    `mirror_error` 경고 분기는 `"SystemMessage"`만 봤지만 파서는 언제나
    `MirrorErrorMessage`를 만들어, 한 번도 참이 되지 않았다(그 가짜가 __name__을
    덮어써서 테스트는 초록불이었다).

    같은 함정이 프로토타입 빌드에서 반복됐다: `Task*` 네 종류도 `SystemMessage`의
    서브클래스라서 번역부를 그냥 통과했고, 그것이 빌드 화면이 멈춘 듯 보인 원인이다.
    이 테스트가 그 드리프트를 잡는 그물이다 — SDK를 올린 뒤 반드시 돌린다.
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
    """서브에이전트 행이 읽는 필드들. 하나라도 사라지면 행이 라벨 없이 뜨거나
    영원히 닫히지 않는다(backend/aipds/agent_activity.py).

    `parent_tool_use_id`가 특히 중요하다 — 그 값이 `Task*`의 `tool_use_id`와
    조인되는 것이 행에 파일 경로를 붙일 수 있는 유일한 근거다.
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
    """서브에이전트 텍스트는 **끄고 간다**(서사 셋이 말풍선에 섞이면 진행 표시를
    대화 흐름 밖으로 옮긴 이유가 없어진다). 그 결정이 유효하려면 옵션이 존재하고
    기본값이 False여야 한다 — 기본값이 뒤집히면 우리가 고르지 않은 동작이 들어온다.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    field = ClaudeAgentOptions.__dataclass_fields__.get("forward_subagent_text")
    assert field is not None, "옵션이 사라졌다 — 서브에이전트 텍스트 정책을 재확인할 것"
    assert field.default is False
