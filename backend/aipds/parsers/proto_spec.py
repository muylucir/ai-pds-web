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
#:   Path A.1  `- **Product**: [From PR/FAQ headline]`  (prototype-validation.md:39)
#:   Path B    `**Use Case**: {Use Case Name}`          (prototype-md-format.md:28)
#:
#: **줄 시작에 물린다.** 본문이 라벨을 인용하는 문장("**Product**: 라고 적는다")을
#: 이름으로 읽지 않게 한다. 목록 표시(`- `)는 Path A.1이 쓰므로 선택적으로 받는다.
_NAME_LINE = re.compile(
    r"^(?:[-*]\s*)?\*\*(?:Product|Use Case)\*\*:\s*(.*)$",
    re.MULTILINE)

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
        if not name or name.startswith(_PLACEHOLDER_OPENERS):
            continue
        return name[:MAX_NAME_CHARS]
    return None
