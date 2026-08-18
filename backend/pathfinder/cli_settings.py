# backend/pathfinder/cli_settings.py — 번들 CLI에 넘기는 컨텍스트 설정.
#
# 두 에이전트(Discovery 드라이버, 프로토타입 빌더)가 같은 CLI를 서브프로세스로
# 띄우므로 두 곳이 같은 값을 쓴다. 그래서 값을 만드는 곳을 여기 하나로 둔다 —
# 한쪽만 켜지면 같은 프로젝트에서 Discovery는 컴팩션이 늦고 빌드는 이른, 설명할
# 수 없는 비대칭이 생긴다.
#
# **왜 이 파일이 생겼는가(2026-08-13 실측).** 한국어 프로젝트의 후반 문서가
# 영어보다 빈약한 원인을 쫓다가 컴팩션을 만났다. 실제 빌드 세션(claude-opus-4-8)이
# 컨텍스트 **264,040 → 53,375 토큰**으로 요약됐다 — 1M 근처가 아니라 26만에서
# 잘린다. 그 뒤에 쓰이는 문서는 근거가 아니라 요약에서 나오므로 뒤 스테이지로
# 갈수록 얇아진다. 한국어는 같은 내용에 토큰을 1.66배 쓰므로 그 지점에 40%
# 일찍 도달한다.
#
# 지배 원인은 이것이 아니라 분량 기준의 부재였고(pathfinder/agent/language/*.md의
# 깊이 기준), 여기는 **증폭기**를 다룬다. 둘을 같이 고쳐야 후반 문서가 회복된다.
from __future__ import annotations

import logging
import os

_log = logging.getLogger(__name__)

#: 불리언 env의 해석. routes/proto_public.py의 _TRUTHY와 같은 규율이다.
_TRUTHY = {"1", "true", "yes", "on"}

#: 1M 컨텍스트를 켤지. 기본 꺼짐 — 켜는 것이 무료가 아니다(아래 docstring).
LONG_CONTEXT_ENV = "PATHFINDER_LONG_CONTEXT"

#: 자동 컴팩션이 발동하는 컨텍스트 크기(토큰). 미설정이면 CLI 기본값.
AUTO_COMPACT_WINDOW_ENV = "PATHFINDER_AUTO_COMPACT_WINDOW"

#: CLI가 받아들이는 범위. 번들 CLI(2.1.231)의 설정 스키마에서 온 값이다
#: (`autoCompactWindow: int().min(1e5).max(1e6)`). 밖의 값을 넘기면 CLI가
#: 설정을 거부하는데, 그 거부는 우리 로그에 남지 않으므로 여기서 막는다.
_WINDOW_MIN = 100_000
_WINDOW_MAX = 1_000_000

#: 번들 CLI가 1M 컨텍스트 별칭으로 받는 접미사. `opus[1m]`/`sonnet[1m]` 형태가
#: CLI의 정식 별칭 목록에 있고, 내부적으로 `context-1m-2025-08-07` 베타를 켠다.
_LONG_CONTEXT_SUFFIX = "[1m]"


def long_context_enabled() -> bool:
    """1M 컨텍스트를 켤지. **기본은 꺼짐.**

    기본을 꺼 두는 이유는 켜는 것이 상위호환이 아니기 때문이다:

    - **턴당 비용**이 는다. 컴팩션이 늦어지면 전체 이력이 매 턴 재전송된다.
      캐시 리드가 0.1배라도 90만 토큰이면 턴마다 그만큼이고, 한국어는 같은
      대화에 1.66배 토큰을 쓴다.
    - **품질이 되레 나빠질 수 있다.** 초장문 컨텍스트는 주의가 희석되므로, 잘
      압축된 26만 토큰 세션이 90만 토큰 세션보다 좋은 문서를 낼 수 있다.
    - Bedrock의 200k 초과 과금은 계정마다 다르다(퍼스트파티와 요금 체계가
      별개다).

    즉 이것은 배포가 비용을 보고 정하는 스위치다. 프로젝트별 필드로 두지 않은
    것도 같은 이유다 — 워크숍 참가자가 프로젝트를 만들며 판단할 성질의 값이
    아니고, 프로젝트별로 갈리면 매니페스트·복원·레지스트리·생성 화면까지
    필드가 번져 나간다.
    """
    return os.environ.get(LONG_CONTEXT_ENV, "").strip().lower() in _TRUTHY


def cli_model_id(model_id: str | None) -> str | None:
    """CLI의 `ANTHROPIC_MODEL`에 넣을 값. 꺼져 있으면 인자 그대로.

    **`[1m]`은 CLI의 별칭 형식이고 Bedrock 모델 id가 아니다.** 그래서 이 조립을
    `app.project_model`에 넣으면 안 된다: 그 함수는 설문 생성 경로에서
    `BedrockModel(model_id=...)`로도 흐르고(app.questionnaire_agent_factory),
    거기에 대괄호가 들어가면 Bedrock이 ValidationException을 던진다 — 즉 설문
    생성만 조용히 깨진다. 붙이는 자리는 CLI를 띄우는 두 팩토리
    (`driver_factory`, `proto_session_factory`)뿐이다.

    Bedrock에서 이 접미사가 필요한 이유: 번들 CLI(2.1.231)의 모델 테이블에서
    `claude-opus-5`는 `context:{window:1e6, native_1m:true, supports_1m_beta:true}`
    지만 **`native_1m_3p`가 없다.** 그 판정 함수는 서드파티 프로바이더에 대해
    `case "bedrock": return native_1m_3p?.bedrock === true`이므로, Bedrock에서
    네이티브 1M을 받는 것은 `native_1m_3p:{bedrock,vertex,foundry}`를 가진
    `claude-sonnet-5`뿐이다. Opus는 베타를 켜야 하고 `[1m]`이 그것을 켠다.

    모델이 None이면 None을 돌려준다 — 드라이버는 None을 받으면 ANTHROPIC_MODEL을
    넣지 않고 CLI 기본값으로 간다(app.project_model의 마지막 칸). 없는 값에
    접미사를 붙이면 그 폴백이 깨진다.

    **어느 모델이 이 접미사를 받는지 실측했다(2026-08-13, ap-northeast-2).**
    이 스위치는 배포 단위로 켜지므로 카탈로그의 모든 모델이 받아야 한다:
    model_catalog의 시드 4종(`claude-opus-5`, `claude-opus-4-6-v1`,
    `claude-sonnet-5`, `claude-sonnet-4-6`)과 배포 폴백(`claude-opus-4-8`)
    전부 `[1m]`을 붙인 채로 정상 응답했다.

    한 번 헷갈렸던 것을 남긴다: `global.anthropic.claude-opus-4-6[1m]`은 400
    "provided model identifier is invalid"인데 **접미사 탓이 아니다** —
    `-v1` 없는 그 id 자체가 무효다(접미사 없이 불러도 같은 400). 카탈로그 시드가
    `-v1`을 쓰는 이유가 그것이고, `...-4-6-v1[1m]`은 정상이다. 새 모델을
    카탈로그에 등록할 때는 접미사를 붙인 형태로 한 번 불러 보는 것이 맞다.
    """
    if model_id is None or not long_context_enabled():
        return model_id
    if model_id.endswith(_LONG_CONTEXT_SUFFIX):
        # 멱등: env 기본값에 이미 접미사가 박혀 있는 배포도 있을 수 있다.
        return model_id
    return f"{model_id}{_LONG_CONTEXT_SUFFIX}"


def auto_compact_window() -> str | None:
    """`CLAUDE_CODE_AUTO_COMPACT_WINDOW`에 넣을 값. 미설정이면 None.

    문자열을 돌려주는 이유는 목적지가 서브프로세스 env이기 때문이다 — 호출부가
    다시 str()하지 않게 한다.

    범위를 벗어난 값이나 숫자가 아닌 값은 **경고 후 None**이다. 그대로 넘기면
    CLI가 설정을 거부하고, 그 거부는 우리 로그에 남지 않아서 "왜 여전히 26만에서
    컴팩션하는가"를 추적할 수 없다. 여기서 경고를 남기면 오타가 배포 로그에
    보인다.
    """
    raw = os.environ.get(AUTO_COMPACT_WINDOW_ENV, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        _log.warning("%s=%r is not an integer — ignoring",
                     AUTO_COMPACT_WINDOW_ENV, raw)
        return None
    if not _WINDOW_MIN <= value <= _WINDOW_MAX:
        _log.warning("%s=%d is outside the CLI's accepted range %d..%d — ignoring",
                     AUTO_COMPACT_WINDOW_ENV, value, _WINDOW_MIN, _WINDOW_MAX)
        return None
    return str(value)


def cli_context_env() -> dict[str, str]:
    """CLI 서브프로세스에 더할 컨텍스트 관련 env. 없으면 빈 dict.

    두 클라이언트 팩토리가 이것을 `env`에 병합한다. dict를 돌려주는 이유는
    "미설정이면 키를 아예 넣지 않는다"를 호출부가 매번 다시 쓰지 않게 하는
    것이다 — 빈 문자열을 넣으면 CLI가 그것을 값으로 읽는다.
    """
    window = auto_compact_window()
    return {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": window} if window else {}
