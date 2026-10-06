# backend/aipds/parsers/doc_references.py — 문서가 가리키는 산출물 중 없는 것.
#
# **왜 있는가(2026-10-06 실측).** Discovery Document는 AI-DLC Inception의 입구
# 문서이고, 근거를 다른 산출물로 미룬다("전체 결과는 `prototype/validation-results.md`에
# 있다"). 드릴 버킷에서 그 참조가 가리키는 파일이 없는 프로젝트가 둘 나왔다
# (novadesk-2, test1111). 원인은 서로 달랐지만(가져오기 이전에 이미 없었음 / 버킷 이전이
# 하루 전 스냅숏만 복사함) 결과는 같았다: 링크가 깨진 핸드오프 문서가 아무 경고 없이
# 내려받아진다. 상류 Step 8 템플릿은 파일이 있든 없든 그 참조를 쓰게 하므로, 원인을
# 하나씩 막는 것으로는 닫히지 않는다 — 내려받기 직전에 드러내는 것이 이 모듈이다.
#
# **라벨이 아니라 경로 모양으로 찾는다.** ko 프로젝트는 템플릿 라벨을 번역한다
# ("전체 결과:", "상세:", "전체 명세는 … 있습니다"). 문구로 찾으면 ko에서 항상 놓친다.
#
# 고치지 않는다. 무엇이 없는지 보여줄 뿐이다 — 어느 쪽이 틀렸는지(문서인지 파일인지)는
# 사람이 판단한다.
from __future__ import annotations

import re

from aipds.proto import layout

#: 경로처럼 생긴 `.md` 토큰. 플레이스홀더 문자(`{}*<>`)까지 한 토큰으로 잡아야 아래에서
#: 통째로 버릴 수 있다 — 빼고 잡으면 `{slug}/design-context.md`가 `/design-context.md`로
#: 잘려 실제 경로처럼 보인다. 끝은 `\b`가 아니라 ASCII 단어 문자 부정이다: 파이썬의
#: `\w`는 한글을 포함하므로 `.md에`의 `d`와 `에` 사이는 경계가 아니다.
_CANDIDATE = re.compile(r"(?<![A-Za-z0-9_./{}*<>-])[A-Za-z0-9_./{}*<>-]+\.md(?![A-Za-z0-9_])")

#: 템플릿 경로(`prototypes/{slug}/PROTOTYPE-{slug}.md`)와 글롭의 표지.
_PLACEHOLDER = re.compile(r"[{}*<>]")

#: 없는 것이 정상인 파일. `change-history.md`는 승인 뒤 변경이 없으면 생기지 않고, 그
#: 부재가 "명세가 그대로"라는 뜻이다(discovery-config/CLAUDE.md의 Spec reconciliation 절).
#: 실측 tobacco의 문장이 그것이었다: "변경 이력(`change-history.md`)이 없으므로".
_ABSENCE_IS_MEANINGFUL = frozenset({layout.CHANGE_HISTORY})

_DOCS_PREFIX = "aiplc-docs/"


def _normalize(ref: str) -> str:
    while ref.startswith("./"):
        ref = ref[2:]
    return ref[len(_DOCS_PREFIX):] if ref.startswith(_DOCS_PREFIX) else ref


def missing_references(text: str, artifact_paths: list[str]) -> list[str]:
    """`text`가 언급하는 `.md` 경로 중 `artifact_paths`에 없는 것, 처음 나온 순서로.

    `artifact_paths`는 `aiplc-docs/`로 시작하는 워크스페이스 상대 경로다. 참조는 문서마다
    기준이 다르다(`prototype/x.md`, `discovery/prototype/x.md`, `x.md`). 그래서 어느
    산출물의 **경로 꼬리**와 세그먼트 단위로 맞으면 있는 것으로 본다 — 기준을 하나로
    정하면 나머지 둘이 전부 오탐이 된다.
    """
    known = [_normalize(p) for p in artifact_paths]
    missing: list[str] = []
    for match in _CANDIDATE.finditer(text):
        ref = match.group(0)
        if _PLACEHOLDER.search(ref) or ref.startswith("/"):
            continue  # 템플릿 경로, 또는 절대경로·URL 꼬리 — 산출물 참조가 아니다
        name = _normalize(ref)
        if not name or name.rsplit("/", 1)[-1] in _ABSENCE_IS_MEANINGFUL:
            continue
        if any(k == name or k.endswith("/" + name) for k in known):
            continue
        if ref not in missing:
            missing.append(ref)
    return missing
