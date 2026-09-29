# backend/tests/test_proto_history.py — 프로토타입 빌드 대화 복원(proto/history.py).
import json
from datetime import datetime, timezone

from fakes.in_memory_s3 import FakeS3Store
from aipds.agent.answer_store import load_answers
from aipds.proto.history import (AnswerLog, answers_prefix, load_history,
                                 record_opening)
from aipds.proto.session_store import S3SessionStore

SLUG = "demo"


def _ts(minute: int) -> str:
    return f"2026-09-27T15:{minute:02d}:00.000Z"


def _epoch(minute: int) -> float:
    return datetime(2026, 9, 27, 15, minute, tzinfo=timezone.utc).timestamp()


def _user(text, minute, **extra):
    return {"type": "user", "timestamp": _ts(minute),
            "message": {"role": "user", "content": text}, **extra}


def _ai(text, minute, blocks=None):
    return {"type": "assistant", "timestamp": _ts(minute),
            "message": {"role": "assistant",
                        "content": blocks or [{"type": "text", "text": text}]}}


async def _write(s3, session_id, lines):
    """SDK가 하는 그대로 미러에 쓴다 — 키 레이아웃을 테스트가 따로 알지 않게."""
    await S3SessionStore(s3, slug=SLUG).append({"session_id": session_id}, lines)


def _shape(items):
    return [(i.role, i.text) for i in items]


async def test_sessions_are_joined_in_time_order_not_key_order():
    # 개선 세션은 새 id로 갈아탄다. id(uuid)의 사전순은 시간과 무관하므로, 키 순서로
    # 이으면 개선 대화가 첫 빌드보다 앞에 온다.
    s3 = FakeS3Store()
    await _write(s3, "ffff-first", [_user("첫 빌드", 1), _ai("만들었습니다", 2)])
    await _write(s3, "0000-second", [_user("버튼 고쳐줘", 10), _ai("고쳤습니다", 11)])

    items = await load_history(s3, SLUG)

    assert _shape(items) == [("user", "첫 빌드"), ("ai", "만들었습니다"),
                             ("user", "버튼 고쳐줘"), ("ai", "고쳤습니다")]


async def test_an_opening_prompt_is_shown_as_what_the_user_typed():
    s3 = FakeS3Store()
    prompt = "이 프로토타입은 이미 한 번 빌드가 완료됐다. …\n\n버튼 고쳐줘\n\n이전 빌드 요약: …"
    await record_opening(s3, SLUG, prompt=prompt, shown="버튼 고쳐줘")
    await _write(s3, "s1", [_user(prompt, 1), _ai("고쳤습니다", 2)])

    assert _shape(await load_history(s3, SLUG)) == [
        ("user", "버튼 고쳐줘"), ("ai", "고쳤습니다")]


async def test_an_auto_start_prompt_leaves_no_bubble_and_keeps_the_turn_boundary():
    # 자동 개시 줄을 그냥 지우면 앞 턴의 마지막 말풍선과 뒤 턴의 첫 말풍선이 하나로
    # 합쳐진다 — 변환기의 턴 경계가 사용자 발화이기 때문이다.
    s3 = FakeS3Store()
    await record_opening(s3, SLUG, prompt="PLAN PROMPT", shown=None)
    await record_opening(s3, SLUG, prompt="RESUME PROMPT", shown=None)
    await _write(s3, "s1", [_user("PLAN PROMPT", 1), _ai("계획입니다", 2),
                            _user("RESUME PROMPT", 30), _ai("이어서 합니다", 31)])

    assert _shape(await load_history(s3, SLUG)) == [
        ("ai", "계획입니다"), ("ai", "이어서 합니다")]


async def test_an_unrecorded_opening_prompt_is_shown_verbatim():
    # 레코드가 생기기 전의 세션. 템플릿을 역파싱하지 않는다(모듈 헤더 2).
    s3 = FakeS3Store()
    await _write(s3, "s1", [_user("OLD PROMPT", 1), _ai("네", 2)])

    assert _shape(await load_history(s3, SLUG)) == [("user", "OLD PROMPT"), ("ai", "네")]


async def test_turns_the_open_session_will_replay_are_cut_off():
    # 열린 세션의 턴은 화면이 턴 로그로 재생한다. 히스토리가 그 턴을 또 내면 대화가
    # 두 번 보인다.
    s3 = FakeS3Store()
    await _write(s3, "old", [_user("첫 빌드", 1), _ai("완료", 2)])
    await _write(s3, "live", [_user("지금 턴", 20), _ai("진행 중", 21)])

    items = await load_history(s3, SLUG, before=_epoch(20))

    assert _shape(items) == [("user", "첫 빌드"), ("ai", "완료")]


async def test_bookkeeping_lines_after_the_cutoff_go_with_their_turn():
    s3 = FakeS3Store()
    await _write(s3, "s1", [_user("앞 턴", 1), _ai("앞 답", 2),
                            _user("뒤 턴", 20), {"type": "last-prompt"},
                            _ai("뒤 답", 21)])

    assert _shape(await load_history(s3, SLUG, before=_epoch(20))) == [
        ("user", "앞 턴"), ("ai", "앞 답")]


async def test_injected_skill_text_is_not_a_user_bubble():
    # 실측(novadesk-2 빌드 트랜스크립트): 모델이 Skill 도구를 부르면 CLI가 스킬 전문을
    # isMeta user 텍스트로 넣는다. 사용자 말풍선이 되면 한 턴이 둘로 쪼개진다.
    s3 = FakeS3Store()
    await _write(s3, "s1", [
        _user("디자인 바꿔줘", 1),
        _ai("스킬을 씁니다", 2),
        {"type": "user", "timestamp": _ts(3), "isMeta": True,
         "message": {"role": "user", "content": [
             {"type": "text", "text": "Base directory for this skill: /opt/…"}]}},
        _ai("바꿨습니다", 4),
    ])

    assert _shape(await load_history(s3, SLUG)) == [
        ("user", "디자인 바꿔줘"), ("ai", "스킬을 씁니다\n바꿨습니다")]


async def test_subagent_transcripts_are_not_bubbles():
    s3 = FakeS3Store()
    await _write(s3, "s1", [_user("만들어줘", 1), _ai("나눠서 합니다", 2)])
    await S3SessionStore(s3, slug=SLUG).append(
        {"session_id": "s1", "subpath": "subagents/agent-a1"},
        [_user("You are redesigning the app shell…", 3, isSidechain=True)])

    assert _shape(await load_history(s3, SLUG)) == [
        ("user", "만들어줘"), ("ai", "나눠서 합니다")]


async def test_a_question_round_restores_the_card_and_the_recorded_answer():
    s3 = FakeS3Store()
    qfile = {"name": "prototype-questions",
             "questions": [{"number": 1, "text": "색은?",
                            "options": [{"letter": "A", "text": "파랑"}]}]}
    await AnswerLog(s3, SLUG).save(tool_use_id="toolu_1", interrupt_id="iid",
                                   questions=qfile, answers={"1": "A"})
    await _write(s3, "s1", [
        _user("만들어줘", 1),
        _ai(None, 2, blocks=[
            {"type": "text", "text": "하나 묻겠습니다"},
            {"type": "tool_use", "id": "toolu_1", "name": "AskUserQuestion",
             "input": {"questions": [{"question": "색은?", "header": "색",
                                      "options": [{"label": "파랑", "description": ""}],
                                      "multiSelect": False}]}}]),
        {"type": "user", "timestamp": _ts(3), "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": 'Your questions have been answered: "색은?"="파랑".'}]}},
        _ai("파랑으로 합니다", 4),
    ])

    items = await load_history(s3, SLUG)

    assert [i.role for i in items] == ["user", "ai", "card", "user", "ai"]
    card, answer = items[2], items[3]
    assert card.name == "prototype-questions"
    assert answer.answers == {"1": "A"}
    assert answer.questions == qfile


async def test_answer_records_live_under_the_prototype_not_the_project():
    # 프로토타입 초기화가 prototypes/{slug}/째 지운다 — 대화와 함께 사라져야 하는
    # 레코드가 Discovery의 answers/에 섞이면 초기화 뒤에도 남는다.
    s3 = FakeS3Store()
    await AnswerLog(s3, SLUG).save(tool_use_id="toolu_1", interrupt_id="iid",
                                   questions={"questions": []}, answers={"1": "A"})

    assert await load_answers(s3) == {}
    assert list(await load_answers(s3, prefix=answers_prefix(SLUG))) == ["toolu_1"]
    assert all(k.startswith(f"prototypes/{SLUG}/") for k in s3.blobs)


async def test_an_answer_without_a_tool_use_id_is_not_recorded():
    s3 = FakeS3Store()
    await AnswerLog(s3, SLUG).save(tool_use_id="", interrupt_id="iid",
                                   questions={"questions": []}, answers={"1": "A"})
    assert dict(s3.blobs) == {}


async def test_a_broken_store_degrades_to_an_empty_history():
    class _Boom(FakeS3Store):
        async def list(self, prefix):
            raise RuntimeError("s3 down")

    assert await load_history(_Boom(), SLUG) == []


async def test_a_failed_opening_record_does_not_raise():
    class _Boom(FakeS3Store):
        async def put(self, key, content):
            raise RuntimeError("s3 down")

    await record_opening(_Boom(), SLUG, prompt="P", shown=None)


async def test_an_unreadable_opening_record_is_skipped():
    s3 = FakeS3Store()
    await record_opening(s3, SLUG, prompt="GOOD", shown="보인 말")
    key = next(k for k in s3.blobs if "/history/inputs/" in k)
    s3.blobs[key.replace(key.rsplit("/", 1)[1], "broken.json")] = "{not json"
    await _write(s3, "s1", [_user("GOOD", 1)])

    assert _shape(await load_history(s3, SLUG)) == [("user", "보인 말")]
    assert json.loads(s3.blobs[key]) == {"shown": "보인 말"}
