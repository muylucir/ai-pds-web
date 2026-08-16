# backend/pathfinder/proto/layout.py — 프로토타입 산출물 레이아웃의 단독 소유자.
#
# **왜 이 모듈이 생겼는가(2026-08-16).** Prototypes 탭이 카드를 만드는 규칙이
# `prototypes/{slug}/PROTOTYPE-{slug}.md` 한 가지뿐이어서, Path A.1(Envision
# 파생, 단일 프로토타입)로 정상 완주한 세션이 카드를 하나도 만들지 못했다
# (keumkang-v5가 그 상태였다).
#
# 상류에 두 레이아웃이 있고, 둘 다 정당하다:
#
#   Path B (use-case 우선순위)  3개  prototypes/{slug}/PROTOTYPE-{slug}.md
#   Path A.1 (Envision 파생)    1개  prototype/prototype-spec.md
#
# `prototype-validation.md`가 선언하는 산출물 7개는 전부 단수 `prototype/`이고
# (556-562행), 그 문서에서 `PROTOTYPE-`이 나오는 유일한 줄(16행)은 "이미 있으면
# 빌드로 간다"는 **진입 조건**이다. 단일 프로토타입에는 구별할 대상이 없으니
# 슬러그가 될 것도 없다. 트리 다이어그램(core-workflow.md:333)이 `prototypes/`를
# "All paths"로 표기하지만, 해당 경로를 직접 규정하는 절차서가 개요보다
# 구체적이므로 그쪽이 이긴다.
#
# `rule/`은 데이터라 고치지 않는다(고치면 재동기화 때 조용히 사라진다). 고칠
# 것은 우리 경로 가정이었다.
#
# **왜 한 모듈인가.** "id → 명세 경로"가 하드코딩 f-string으로 네 곳에 복제돼
# 있었다(routes/prototypes, routes/surveys, proto/session, survey/store).
# 레이아웃이 하나일 때는 보이지 않던 비용이 둘이 되면서 청구됐다 —
# pathsafe.workspace_relative가 세 번 복제돼 있던 것과 같은 모양이고, 규칙이 네
# 벌이면 그중 하나가 구멍으로 드리프트할 기회도 네 번이다.
#
# **순수 함수로 둔다.** S3를 probe해서 레이아웃을 알아내는 방식이 더 "정확"하지만
# hot path마다 왕복이 늘고, `proto/session._spec_key`처럼 동기 헬퍼로 쓰는 자리를
# async로 바꿔야 해서 침습적이다. 단수 레이아웃을 예약 id 하나의 분기로 처리하면
# IO 없이 끝난다.
from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

#: 두 레이아웃이 함께 사는 서브트리. `s3.list()`에 넘기는 접두사다.
DISCOVERY_PREFIX = "aiplc-docs/discovery/"

#: 단일 프로토타입의 id.
#:
#: **왜 디렉터리 이름을 그대로 쓰는가.** id ↔ 경로 대응이 자명해야 한다 — 로그나
#: S3 키에서 `prototypes/prototype/bundle/`(빌드 상태)을 보면 곧바로
#: `discovery/prototype/`의 그 프로토타입이라고 읽힌다. 외울 매핑이 없다.
#:
#: **왜 `_prototype` 같은 예약어를 쓰지 않는가.** 밑줄이 슬러그 문자 클래스
#: (`[a-z0-9-]`) 밖이라 "구조적으로 충돌 불가"처럼 보이지만, 그 문자 클래스는
#: 에이전트가 지키는 규칙일 뿐이고(prototype-context-generation.md) 우리 코드가
#: 강제하지 않는다 — `_SLUGGED_RE`의 캡처는 어떤 세그먼트든 받는다. 에이전트가
#: 룰을 지킬 것이라는 가정은 실측으로 깨졌으므로(같은 날 슬러그·인코딩 지시가
#: 둘 다 무시됐다) 그 위에 안전성을 세우지 않는다. 충돌은 아래 `discover`의
#: 가드가 **코드로** 막는다.
SINGLE_ID = "prototype"

#: Path A.1의 명세 경로. `prototype-validation.md:557`이 선언하는 값이다.
SINGLE_SPEC_KEY = "aiplc-docs/discovery/prototype/prototype-spec.md"

#: Path B의 명세 경로. 백레퍼런스가 load-bearing이다 — 디렉터리명과 파일명
#: 접미사가 같아야 한다(상류 규약). 어긋난 파일은 카드가 아니다.
_SLUGGED_RE = re.compile(
    r"^aiplc-docs/discovery/prototypes/([^/]+)/PROTOTYPE-\1\.md$")


def _id_for(key: str) -> str | None:
    """이 S3 키가 명세라면 그 프로토타입의 id, 아니면 None."""
    if key == SINGLE_SPEC_KEY:
        return SINGLE_ID
    match = _SLUGGED_RE.match(key)
    return match.group(1) if match else None


def discover(keys: list[str]) -> dict[str, str]:
    """S3 키 목록 → {id: 명세 경로}. 명세가 아닌 키는 무시한다.

    **id 유일성을 보장한다.** 예전 구현은 `slugs[id] = key`로 덮어썼고, 두
    명세가 한 id를 주장하면 카드 하나가 조용히 사라졌다 — 살아남는 쪽이
    `s3.list()` 순회 순서에 달렸으니 에러 없이 틀린 결과다.

    충돌 시 **슬러그 쪽을 남긴다**: 상류가 `PROTOTYPE-{slug}.md`를 ★ Shareable로
    표시하고 빌드·설문이 그 파일로 키되므로 이미 참조가 붙어 있을 가능성이
    높다. 그리고 그 선택이 순회 순서와 무관해야 가드다 — 아래는 키를 정렬하지
    않고도 결정적이다(단수 키는 정확히 하나뿐이므로 우선순위 비교로 충분하다).

    떨어진 쪽은 WARNING에 **두 키를 모두** 적는다. "카드가 하나 안 보인다"를
    로그 한 줄로 진단할 수 있어야 한다.
    """
    found: dict[str, str] = {}
    for key in keys:
        pid = _id_for(key)
        if pid is None:
            continue
        current = found.get(pid)
        if current is None:
            found[pid] = key
            continue
        if current == key:
            continue
        # 슬러그 레이아웃이 이긴다. 단수 키는 하나뿐이므로 "둘 중 단수인 쪽이
        # 진다"로 순서와 무관하게 결정된다.
        winner, loser = ((key, current) if current == SINGLE_SPEC_KEY
                         else (current, key))
        found[pid] = winner
        _log.warning(
            "prototype id %r claimed by two specs — keeping %s, skipping %s",
            pid, winner, loser)
    return found


def spec_key(prototype_id: str) -> str:
    """id → 명세 경로. `discover`가 돌려준 id와 왕복해야 한다.

    어긋나면 카드는 뜨는데 빌드·설문이 명세를 못 찾는다 —
    tests/test_proto_layout.py가 그 왕복을 고정한다.
    """
    if prototype_id == SINGLE_ID:
        return SINGLE_SPEC_KEY
    return (f"aiplc-docs/discovery/prototypes/{prototype_id}"
            f"/PROTOTYPE-{prototype_id}.md")


def artifact_dir(prototype_id: str) -> str:
    """이 프로토타입의 산출물 디렉터리(설문지 등이 들어갈 곳).

    명세와 **같은 디렉터리**를 돌려준다. 읽기 경로만 고치고 쓰기를 두면 단수
    프로토타입의 설문지가 에이전트가 쓰지도 않는 `prototypes/prototype/` 트리에
    홀로 생기고, 삭제·아카이브 경로가 그 트리를 잊는다.
    """
    if prototype_id == SINGLE_ID:
        return "aiplc-docs/discovery/prototype"
    return f"aiplc-docs/discovery/prototypes/{prototype_id}"
