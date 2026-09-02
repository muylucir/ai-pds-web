# backend/aipds/parsers/proto_spec.py — 명세 문서 → 사람이 읽는 프로토타입 이름.
#
# **왜 필요한가.** Prototypes 탭의 카드 제목은 `slug`였고, Path A.1(Envision 파생,
# 단일 프로토타입)의 slug는 상수 `"prototype"`이다(proto/layout.py의 `SINGLE_ID`).
# 단일 프로토타입에는 구별할 대상이 없으니 슬러그가 될 것도 없다는 그 판단은
# 맞지만, 그 결과로 **모든 Path A.1 프로젝트의 카드 제목이 리터럴 "prototype"**
# 이었다. 제품명은 명세 본문에만 있었고 목록 응답에는 실려 오지 않았다.
#
# **왜 순수 함수인가.** 경로는 layout.py가 소유하고, 이 모듈은 그 경로에서 읽은
# 본문만 본다 — 레이아웃을 분기하지 않는다. 두 상류 템플릿의 이름 줄이 라벨만
# 다르고 형태가 같으므로, 라벨 두 개를 함께 받으면 레이아웃을 알 필요가 없다.
from __future__ import annotations

import re

#: 카드 제목 한 줄에 실리는 값이므로 페이로드에서 자른다. 모델이 이름 자리에
#: 문장을 쓰는 일이 있고(관측된 습성), CSS truncate는 이미 넘어온 뒤의 표시
#: 문제만 다룬다 — 목록 응답 크기는 그대로 커진다.
MAX_NAME_CHARS = 120

#: 이름이 사는 줄. 두 상류 템플릿이 라벨만 다르다:
#:
#:   Path A.1  `- **Product**: [From PR/FAQ headline]`  (prototype-validation.md:42)
#:   Path B    `**Use Case**: {Use Case Name}`          (prototype-md-format.md:28)
#:
#: **라벨은 언어마다 두 벌이다.** 템플릿의 라벨은 영어지만 ko 프로젝트의 산출물은
#: `- **제품**:`으로 나온다 — 우연이 아니라 지시된 동작이다(`agent/workspace_rules.py`
#: 의 ko 언어 규약: "질문 문구·헤딩·**라벨**·선택지는 한국어로 옮긴다"). 영어만
#: 받던 규칙은 ko에서 항상 None이었고, ko가 기본값이므로 그것이 곧 전부였다 —
#: 실측(2026-09-02) S3의 명세 4개가 모두 `**제품**`이다. 모델이 읽고 쓰는 텍스트를
#: 코드가 언어별 두 벌로 소유하는 것은 이 리포의 기존 규약이다(`agent/prompts.py`,
#: `proto/prompts.py`, `survey/builder.py`). 업스트림 룰셋은 건드리지 않는다.
#:
#: Path B의 ko 라벨은 실측 표본이 없다. "Use Case"는 기술용어로 남을 수도 있고
#: (그러면 영어 판이 받는다) 같은 규약이 번역도 지시하므로, 표기가 하나로 정해지지
#: 않는 자리라서 흔한 세 판을 함께 받는다.
#:
#: **줄 시작에 물린다.** 본문이 라벨을 인용하는 문장("**Product**: 라고 적는다")을
#: 이름으로 읽지 않게 한다. 목록 표시(`- `)는 Path A.1이 쓰므로 선택적으로 받는다.
_NAME_LINE = re.compile(
    r"^(?:[-*]\s*)?\*\*(?:Product|Use Case"
    r"|제품|유즈케이스|유스케이스|사용 사례)\*\*:\s*(.*)$",
    re.MULTILINE)

#: 이름과 한 줄 설명의 경계. 실측 4개 중 3개가 `제품명 — 설명` 형태로 쓴다
#: (`트래블프렌드 — 여행 상품을 권하지 않는 AI 여행 친구`). 카드 제목은 이름
#: 자리이므로 설명은 버린다.
#:
#: **길이 절단으로는 대체되지 않는다.** 그 설명은 다음 줄로 이어지기도 하는데
#: (실측: novadesk), 줄 끝까지만 잡는 규칙은 제목을 문장 조각(`… PM이 무엇을`)으로
#: 만들고 `MAX_NAME_CHARS`는 조각을 조각으로 남긴다. 자를 자리는 길이가 아니라
#: 경계다. em dash만 받는다 — 하이픈은 이름 안에 들어갈 수 있다(`AI-PDS`).
_TAGLINE = re.compile(r"\s+—\s+")

#: 값에 남은 강조 기호. 라벨만 굵게 하라는 템플릿을 모델이 값까지 굵게 쓰는
#: 경우가 있어, 제목에 `**`가 그대로 보이는 것을 막는다.
_EMPHASIS = "*_ \t"

#: 채워지지 않은 템플릿 자리표시자의 여는 괄호. `[From PR/FAQ headline]`을 제목으로
#: 걸면 슬러그보다 나쁘다 — 사용자가 자기 프로토타입이 아니라고 읽는다.
_PLACEHOLDER_OPENERS = ("[", "{")


def spec_name(markdown: str) -> str | None:
    """명세 본문 → 카드에 쓸 이름. 없으면 None(호출부가 슬러그로 되돌아간다).

    빈 문자열이 아니라 None인 이유: "이름을 못 찾았다"와 "이름이 빈 문자열이다"가
    호출부에서 같은 분기여야 하고, 그 분기는 값의 유무여야 한다.

    첫 일치가 이긴다 — 문서 순서라서 s3 순회 순서나 dict 순서에 의존하지 않는다.
    """
    for match in _NAME_LINE.finditer(markdown):
        name = re.sub(r"\s+", " ", match.group(1)).strip(_EMPHASIS).strip()
        # 설명을 버리는 것이 자리표시자 판정보다 앞선다: `{Use Case Name} — 설명`은
        # 자른 뒤에도 자리표시자여야 거부된다.
        name = _TAGLINE.split(name, 1)[0].strip(_EMPHASIS).strip()
        if not name or name.startswith(_PLACEHOLDER_OPENERS):
            continue
        return name[:MAX_NAME_CHARS]
    return None
