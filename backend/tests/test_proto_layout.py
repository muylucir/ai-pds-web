# backend/tests/test_proto_layout.py — 프로토타입 산출물 레이아웃 두 가지.
#
# **왜 이 모듈이 생겼는가(2026-08-16의 오판).** Prototypes 탭이 카드를 만드는
# 규칙이 `prototypes/{slug}/PROTOTYPE-{slug}.md` **한 가지**뿐이었다. 그래서
# Path A.1(Envision 파생, 단일 프로토타입)로 정상 완주한 세션이 카드를 하나도
# 만들지 못했다 — keumkang-v5가 그 상태다.
#
# 처음에는 "상류 Path A.1이 슬러그 파일 생성을 빠뜨렸다"고 판단해 에이전트가 그
# 파일을 쓰게 하려 했다. **그 판단이 틀렸다.** `prototype-validation.md`가
# 선언하는 산출물 7개는 전부 단수 `prototype/`이고(그 문서 556-562행),
# `PROTOTYPE-`이 나오는 유일한 줄(16행)은 "이미 있으면 빌드로 간다"는 **진입
# 조건**이다. 단일 프로토타입에는 구별할 대상이 없으니 슬러그가 될 것도 없다.
#
#   Path B (use-case 우선순위)  3개  prototypes/{slug}/PROTOTYPE-{slug}.md  슬러그 필요
#   Path A.1 (Envision 파생)    1개  prototype/prototype-spec.md            슬러그 없음
#
# 상류 트리 다이어그램(core-workflow.md:333)은 `prototypes/`를 "All paths"로
# 표기하지만, 해당 경로를 직접 규정하는 절차서가 개요보다 구체적이므로 그쪽이
# 이긴다. `rule/`은 데이터라 고치지 않는다 — 고칠 것은 우리 경로 가정이다.
#
# 이 모듈이 레이아웃을 **단독 소유**한다. 예전에는 "슬러그 → 명세 경로"가
# 하드코딩 f-string으로 네 곳에 복제돼 있었고(routes/prototypes, routes/surveys,
# proto/session, survey/store), 레이아웃이 둘이 되면서 그 복제가 비용으로
# 청구됐다 — pathsafe.workspace_relative가 세 번 복제돼 있던 것과 같은 모양이다.
from __future__ import annotations

import logging

from aipds.proto.layout import (SINGLE_ID, SINGLE_SPEC_KEY, artifact_dir,
                                     discover, spec_key)

SLUGGED = "aiplc-docs/discovery/prototypes/maint-support/PROTOTYPE-maint-support.md"


# ---- discover: 두 레이아웃을 모두 인식한다 ----

def test_discovers_the_slugged_layout():
    assert discover([SLUGGED]) == {"maint-support": SLUGGED}


def test_discovers_the_single_prototype_layout():
    """**이 케이스가 이 모듈의 존재 이유다.** keumkang-v5가 이 상태였고 카드가 0개였다."""
    assert discover([SINGLE_SPEC_KEY]) == {SINGLE_ID: SINGLE_SPEC_KEY}


def test_both_layouts_coexist():
    """Path B로 3개를 만든 뒤 Path A.1을 돌린 프로젝트는 명세가 4개다 —
    카드도 4개가 맞다."""
    keys = [SINGLE_SPEC_KEY] + [
        f"aiplc-docs/discovery/prototypes/{s}/PROTOTYPE-{s}.md"
        for s in ("a-one", "b-two", "c-three")]
    got = discover(keys)
    assert set(got) == {SINGLE_ID, "a-one", "b-two", "c-three"}


def test_ignores_everything_else():
    # 같은 트리의 다른 산출물이 카드가 되면 안 된다.
    for key in (
        "aiplc-docs/discovery/prototype/design-context.md",
        "aiplc-docs/discovery/prototype/build-instructions.md",
        "aiplc-docs/discovery/prototypes/x/design-context.md",
        # 디렉터리명과 파일명이 어긋난 것 — 상류 규약 위반이므로 카드가 아니다.
        "aiplc-docs/discovery/prototypes/foo/PROTOTYPE-bar.md",
        # 유사 접두사. 세그먼트가 아니라 문자열로 비교하면 여기가 통과한다.
        "aiplc-docs/discovery/prototypes-backup/x/PROTOTYPE-x.md",
        "aiplc-docs/discovery/prototype-old/prototype-spec.md",
    ):
        assert discover([key]) == {}, key


def test_discovery_is_order_independent():
    keys = [SINGLE_SPEC_KEY, SLUGGED]
    assert discover(keys) == discover(list(reversed(keys)))


# ---- id 유일성 가드 ----

def test_a_colliding_id_does_not_silently_replace_the_other(caplog):
    """**침묵이 이 가드의 이유다.**

    예전 구현은 `slugs[id] = key`로 덮어썼다. 두 명세가 한 id를 주장하면 카드
    하나가 사라지고, 살아남는 쪽은 `s3.list()` 순회 순서에 달렸다 — 에러 없이
    틀린 결과다.

    id를 `prototype`으로 정한 것은 경로 대응이 자명해서다. 대신 충돌은 이름
    규칙이 아니라 **코드**로 막는다: 에이전트가 슬러그 새니타이즈 규칙을 지킬
    것이라는 가정은 오늘 두 번 깨졌고, 코드로 막은 것만 막혔다.
    """
    colliding = (f"aiplc-docs/discovery/prototypes/{SINGLE_ID}/"
                 f"PROTOTYPE-{SINGLE_ID}.md")

    with caplog.at_level(logging.WARNING):
        got = discover([SINGLE_SPEC_KEY, colliding])

    # 슬러그 쪽을 남긴다 — 상류가 ★ Shareable로 표시하고 빌드·설문이 그 파일로
    # 키되므로 이미 참조가 붙어 있을 가능성이 높다.
    assert got == {SINGLE_ID: colliding}
    # 두 키를 모두 적어야 "카드가 하나 안 보인다"를 로그 한 줄로 진단할 수 있다.
    assert any(SINGLE_SPEC_KEY in r.message and colliding in r.message
               for r in caplog.records), [r.message for r in caplog.records]


def test_the_guard_is_order_independent():
    """순회 순서가 결과를 바꾸면 가드가 아니다."""
    colliding = (f"aiplc-docs/discovery/prototypes/{SINGLE_ID}/"
                 f"PROTOTYPE-{SINGLE_ID}.md")
    assert discover([SINGLE_SPEC_KEY, colliding]) == \
        discover([colliding, SINGLE_SPEC_KEY])


# ---- spec_key / artifact_dir: 네 곳이 부르는 해석기 ----

def test_spec_key_resolves_both_layouts():
    assert spec_key("maint-support") == SLUGGED
    assert spec_key(SINGLE_ID) == SINGLE_SPEC_KEY


def test_artifact_dir_keeps_a_prototype_s_files_together():
    """설문지 쓰기 경로가 이것을 쓴다. 읽기만 고치고 쓰기를 두면 단수
    프로토타입의 설문지가 에이전트가 쓰지도 않는 트리에 홀로 생기고, 삭제·
    아카이브 경로가 그 트리를 잊는다."""
    assert artifact_dir("maint-support") == \
        "aiplc-docs/discovery/prototypes/maint-support"
    assert artifact_dir(SINGLE_ID) == "aiplc-docs/discovery/prototype"


def test_spec_key_round_trips_with_discover():
    """해석기와 탐색기가 어긋나면 카드는 뜨는데 빌드가 명세를 못 찾는다."""
    for key in (SLUGGED, SINGLE_SPEC_KEY):
        (found_id, found_key), = discover([key]).items()
        assert spec_key(found_id) == found_key


def test_the_single_id_is_the_directory_name():
    """id ↔ 경로 대응이 자명해야 한다 — 로그의 `prototypes/prototype/bundle/`이
    곧 `discovery/prototype/`의 그 프로토타입이라고 읽혀야 한다."""
    assert SINGLE_ID == "prototype"
    assert SINGLE_SPEC_KEY == "aiplc-docs/discovery/prototype/prototype-spec.md"
