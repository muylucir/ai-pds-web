# backend/tests/test_turn_handles.py
#
# 턴 핸들은 긴 입력을 URL에서 빼기 위한 장치다. EventSource는 GET만 지원해
# 본문을 실을 수 없으므로, 텍스트를 POST 본문으로 받아 짧은 핸들로 바꾸고
# SSE URL에는 그 핸들만 실린다.
#
# 왜 이 장치가 필요한가(실측): 한글 2,164자 입력이 encodeURIComponent로
# 14,376바이트 요청 라인이 되고, 인증 쿠키(JWT 3개 ~3.7KB)와 합쳐 Node의
# maxHeaderSize 16,384바이트를 넘겨 HTTP 431이 났다. EventSource는 상태
# 코드를 노출하지 않아 화면에는 "연결이 끊어졌습니다"만 떴다.
from __future__ import annotations

import pytest

from aipds.turn_handles import (
    TurnHandleStore, HANDLE_TTL_SECONDS,
)


def test_a_handle_round_trips_the_payload():
    store = TurnHandleStore()
    tid = store.create("p1", {"text": "안녕하세요"})
    assert store.consume("p1", tid) == {"text": "안녕하세요"}


def test_a_handle_is_short_regardless_of_payload_size():
    """이것이 이 모듈의 존재 이유다 — 핸들이 URL에 들어가므로 길면 안 된다."""
    store = TurnHandleStore()
    tid = store.create("p1", {"text": "가" * 50_000})
    assert len(tid) <= 64, len(tid)
    # URL에 그대로 들어가므로 인코딩이 필요한 문자가 없어야 한다.
    assert tid.isascii() and tid.isalnum(), tid


def test_a_handle_is_single_use():
    """재사용을 허용하면 URL에 남은 핸들로 같은 턴을 다시 돌릴 수 있다 —
    브라우저 히스토리·리퍼러·로그에 URL이 남는다."""
    store = TurnHandleStore()
    tid = store.create("p1", {"text": "한 번만"})
    assert store.consume("p1", tid) is not None
    assert store.consume("p1", tid) is None


def test_an_unknown_handle_is_none():
    store = TurnHandleStore()
    assert store.consume("p1", "deadbeef") is None


def test_a_handle_is_scoped_to_its_project():
    """다른 프로젝트의 핸들로 이 프로젝트의 턴을 열 수 없다 — 핸들은 인증이
    아니지만, 프로젝트 경계를 넘는 값이 되면 안 된다."""
    store = TurnHandleStore()
    tid = store.create("p1", {"text": "p1의 입력"})
    assert store.consume("p2", tid) is None
    # 원래 프로젝트에서는 여전히 유효하다(실패한 조회가 소비하지 않았다).
    assert store.consume("p1", tid) == {"text": "p1의 입력"}


def test_an_expired_handle_is_none():
    # 시계를 주입해 만료를 결정적으로 시험한다 — sleep을 쓰면 느리고 불안정하다.
    now = [1000.0]
    store = TurnHandleStore(clock=lambda: now[0])
    tid = store.create("p1", {"text": "곧 만료"})
    now[0] += HANDLE_TTL_SECONDS + 1
    assert store.consume("p1", tid) is None


def test_a_handle_within_ttl_still_works():
    now = [1000.0]
    store = TurnHandleStore(clock=lambda: now[0])
    tid = store.create("p1", {"text": "아직 유효"})
    now[0] += HANDLE_TTL_SECONDS - 1
    assert store.consume("p1", tid) == {"text": "아직 유효"}


def test_creating_a_handle_purges_expired_ones():
    """만료된 핸들이 쌓이면 메모리가 는다. 새 핸들을 만들 때 함께 정리한다 —
    별도 타이머를 두지 않는 이유는 이 저장소가 프로세스 수명 동안만 살고
    턴 개시마다 반드시 create가 불리기 때문이다."""
    now = [1000.0]
    store = TurnHandleStore(clock=lambda: now[0])
    store.create("p1", {"text": "낡음"})
    assert store.size() == 1
    now[0] += HANDLE_TTL_SECONDS + 1
    store.create("p1", {"text": "새것"})
    assert store.size() == 1


def test_handles_are_unpredictable():
    """추측 가능한 핸들은 다른 사용자의 턴 텍스트를 읽는 경로가 된다."""
    store = TurnHandleStore()
    ids = {store.create("p1", {"text": str(i)}) for i in range(200)}
    assert len(ids) == 200


def test_answers_payloads_round_trip_too():
    # 답변 제출도 같은 배관을 쓴다 — 자유 서술이 길면 같은 한도에 걸린다.
    store = TurnHandleStore()
    tid = store.create("p1", {"answers": {"1": "A", "2": "긴 자유 서술" * 500}})
    got = store.consume("p1", tid)
    assert got is not None and got["answers"]["1"] == "A"
