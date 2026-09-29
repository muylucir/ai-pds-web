# backend/aipds/proto/history.py — 프로토타입 빌드 대화의 히스토리.
#
# 빌드 패널이 다시 열릴 때 보여줄 지난 대화다. 정본은 빌드 에이전트의 SDK
# 트랜스크립트(proto/session_store.py가 S3에 미러링한다)이고, 이 모듈은 그것을
# Discovery와 같은 HistoryItem으로 바꾼다 — 변환 자체는 session_history의
# transform_cli_transcript를 그대로 쓴다. 두 화면이 같은 ChatTimeline으로 그리므로
# 표현이 갈라지면 안 된다.
#
# Discovery와 다른 점이 셋이고, 각각 이 모듈이 존재하는 이유다:
#
#   1. 대화가 **여러 SDK 세션**에 걸쳐 있다. 완료된 빌드를 개선하면 세션이 새 id로
#      갈아탄다(proto/session._resolve_session_id의 handoff 분기) — 긴 빌드 맥락
#      대신 요약만 지고 가기 위해서다. session.json은 현재 id 하나만 기억하므로,
#      트랜스크립트 prefix 아래의 세션 전부를 읽어 **첫 줄의 타임스탬프 순**으로
#      잇는다. 순서를 따로 적어 두지 않는 이유: 그 값은 CLI가 이미 줄마다 쓰고
#      있고, 따로 둔 순서표는 트랜스크립트와 어긋날 수 있는 두 번째 사본이다.
#
#   2. 트랜스크립트의 사용자 줄이 **사용자가 한 말이 아닐 수 있다.** 세션의 첫
#      메시지는 서버가 개시 프롬프트(plan/resume/handoff)로 감싸서 보낸다
#      (routes/prototypes._opening_text). 그대로 복원하면 긴 지시문이 사용자
#      말풍선으로 뜬다. 그래서 개시 턴마다 "보낸 프롬프트 → 화면에 보인 말"을
#      기록하고(`record_opening`), 복원이 프롬프트의 해시로 조인한다. 템플릿을
#      역파싱하지 않는다 — 프롬프트는 언어별로 바뀌는 우리 문장이고, 그 모양에
#      기대는 파서는 문구를 고치는 순간 조용히 틀린다. 레코드가 없는 개시 턴(이
#      기록이 생기기 전의 세션)은 트랜스크립트 그대로 보인다.
#
#   3. 열린 세션의 턴은 **세션이 재생한다**(usePrototypeStream.resume이 턴 로그를
#      처음부터 흘린다). 완료 카드와 진행 중인 질문 폼은 그 경로에만 있으므로
#      재생을 없앨 수 없고, 히스토리가 같은 턴을 또 내면 대화가 두 번 보인다.
#      그래서 히스토리는 열린 세션의 첫 턴이 시작된 시각(`before`) 앞에서 자른다.
#
# 레코드는 전부 `prototypes/{slug}/history/` 아래에 있다. 프로토타입 초기화가
# `prototypes/{slug}/`째 지우므로(proto/session.purge_session_state) 대화와 함께
# 사라지고, 프로젝트 번들은 트랜스크립트와 같은 이유로 담지 않는다
# (project_bundle의 _PROTO_CONVERSATION_RE).
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime

from aipds.agent.answer_store import load_answers, save_answers
from aipds.context_usage import load_record
from aipds.models import HistoryItem
from aipds.proto.session_store import transcript_prefix
from aipds.s3store import S3StoreLike
from aipds.session_history import transform_cli_transcript

_log = logging.getLogger(__name__)

#: 라이브 질문 카드의 이름(proto/builder.py의 _on_can_use_tool과 같은 값).
QUESTION_NAME = "prototype-questions"


def history_prefix(slug: str) -> str:
    return f"prototypes/{slug}/history/"


def _inputs_prefix(slug: str) -> str:
    return f"{history_prefix(slug)}inputs/"


def answers_prefix(slug: str) -> str:
    return f"{history_prefix(slug)}answers/"


def context_key(slug: str) -> str:
    """턴의 마지막 컨텍스트 사용량(proto/session.py가 쓴다)."""
    return f"{history_prefix(slug)}context.json"


def _prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


async def record_opening(s3: S3StoreLike, slug: str, *, prompt: str,
                         shown: str | None) -> None:
    """개시 턴 하나: 에이전트에게 보낸 `prompt`를 화면에서는 `shown`으로 보인다.

    `shown`이 None이면 자동 개시(사용자가 아무 말도 하지 않았다) — 복원에서 사용자
    말풍선을 만들지 않는다. 실패는 삼킨다: 이 레코드는 복원의 문구일 뿐이고, 없으면
    그 턴이 트랜스크립트 그대로 보일 뿐이다. 턴을 막을 이유가 아니다.
    """
    try:
        await s3.put(f"{_inputs_prefix(slug)}{_prompt_digest(prompt)}.json",
                     json.dumps({"shown": shown}, ensure_ascii=False))
    except Exception:
        _log.exception("opening-turn record failed for prototype %s", slug)


class AnswerLog:
    """빌더가 받은 답을 기록한다 — Discovery의 answer_store와 같은 레코드, 다른 prefix.

    빌더는 S3를 모르고(세션이 안다) 슬러그도 모르므로, 기록할 곳을 묶어서 넘긴다.
    레코드의 근거는 agent/answer_store.py 헤더와 같다: 트랜스크립트의 tool_result는
    CLI가 영어로 옮겨 적은 문장이라 답을 펼 수 없다.
    """

    def __init__(self, s3: S3StoreLike, slug: str):
        self._s3 = s3
        self._slug = slug

    async def save(self, *, tool_use_id: str, interrupt_id: str,
                   questions: dict, answers: dict[str, str]) -> None:
        """절대 턴을 실패시키지 않는다. tool_use_id가 없으면 조인할 수 없으므로
        쓰지 않는다(claude_driver._save_answers_quietly와 같은 규율)."""
        if not tool_use_id:
            return
        try:
            await save_answers(self._s3, tool_use_id=tool_use_id,
                               interrupt_id=interrupt_id, questions=questions,
                               answers=answers, prefix=answers_prefix(self._slug))
        except Exception:
            _log.exception("prototype answer record failed for %s", self._slug)


async def _load_inputs(s3: S3StoreLike, slug: str) -> dict[str, str | None]:
    """프롬프트 해시 → 보인 말. 어떤 실패도 그 레코드만 건너뛴다."""
    prefix = _inputs_prefix(slug)
    try:
        keys = await s3.list(prefix)
    except Exception:
        _log.exception("opening-turn record listing failed for %s", slug)
        return {}
    bodies = await asyncio.gather(*(s3.get(k) for k in keys),
                                 return_exceptions=True)
    out: dict[str, str | None] = {}
    for key, body in zip(keys, bodies):
        try:
            if isinstance(body, BaseException):
                raise body
            shown = json.loads(body)["shown"]
        except Exception:
            _log.warning("unreadable opening-turn record skipped: %s", key)
            continue
        if shown is None or isinstance(shown, str):
            out[key[len(prefix):].removesuffix(".json")] = shown
    return out


async def _load_sessions(s3: S3StoreLike, slug: str) -> dict[str, list[dict]]:
    """세션 id → 메인 트랜스크립트 줄(쓴 순서). 서브에이전트 줄은 읽지 않는다.

    서브에이전트는 라이브에서도 말풍선이 아니다 — 총괄의 Agent 도구 한 줄과 끝난
    뒤의 요약 한 줄로만 보인다. 그 대화를 펼치면 빌드 한 번이 수백 개의 말풍선이 된다.
    """
    root = transcript_prefix(slug)
    # 키 모양: {root}{session_id}/main/NNNNNNNN.jsonl (proto/session_store._session_prefix)
    keys = sorted(k for k in await s3.list(root)
                  if k[len(root):].split("/")[1:2] == ["main"])
    # 병렬 GET — agent/session_store.load_transcript와 같은 판단이다(빌드
    # 트랜스크립트는 Discovery보다 배치가 많다).
    bodies = await asyncio.gather(*(s3.get(k) for k in keys),
                                 return_exceptions=True)
    sessions: dict[str, list[dict]] = {}
    for key, body in zip(keys, bodies):
        sid = key[len(root):].split("/", 1)[0]
        lines = sessions.setdefault(sid, [])
        if isinstance(body, BaseException):
            _log.warning("unreadable transcript batch skipped: %s", key)
            continue
        for line in body.splitlines():
            if not line:
                continue
            try:
                lines.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # 쓰는 도중 잘린 줄 하나가 세션 전체를 잃게 하지 않는다
    return sessions


def _timestamp(line: dict) -> float | None:
    raw = line.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def _first_timestamp(lines: list[dict]) -> float:
    for line in lines:
        ts = _timestamp(line)
        if ts is not None:
            return ts
    return float("inf")  # 시각이 없는 세션은 맨 뒤 — 순서를 지어낼 근거가 없다


def _before(lines: list[dict], cutoff: float) -> list[dict]:
    """`cutoff` 이후의 줄을 버린다. 시각이 없는 부기 줄은 앞 줄을 따른다."""
    for i, line in enumerate(lines):
        ts = _timestamp(line)
        if ts is not None and ts >= cutoff:
            return lines[:i]
    return lines


def _prompt_text(line: dict) -> str | None:
    """사용자 줄의 프롬프트 문자열. SDK의 query(prompt)는 평문 문자열로 기록된다."""
    msg = line.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return None
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if (isinstance(content, list) and len(content) == 1
            and isinstance(content[0], dict) and content[0].get("type") == "text"):
        return content[0].get("text")
    return None


def _segments(lines: list[dict], inputs: dict[str, str | None]) -> list[list[dict]]:
    """개시 프롬프트를 보인 말로 바꾸고, 대화를 턴 경계가 사라지지 않게 자른다.

    자동 개시는 사용자 말풍선이 없다. 그 줄을 그냥 지우면 앞 턴의 마지막
    말풍선과 이 턴의 첫 말풍선이 하나로 합쳐진다 — transform_cli_transcript의 턴
    경계가 사용자 발화이기 때문이다. 그래서 거기서 대화를 끊고 조각마다 따로
    변환한다.
    """
    out: list[list[dict]] = [[]]
    for line in lines:
        prompt = _prompt_text(line)
        digest = _prompt_digest(prompt) if prompt is not None else None
        if digest is None or digest not in inputs:
            out[-1].append(line)
            continue
        shown = inputs[digest]
        if shown is None:
            out.append([])
            continue
        out[-1].append({**line, "message": {**line["message"], "content": shown}})
    return [seg for seg in out if seg]


async def load_history(s3: S3StoreLike, slug: str, *,
                       before: float | None = None) -> list[HistoryItem]:
    """이 프로토타입의 지난 빌드 대화. `before`(epoch 초) 이후의 줄은 뺀다.

    어떤 실패도 빈 목록으로 강등한다 — Discovery의 list_history와 같은 원칙이다
    (히스토리는 보조 데이터이고 패널을 막지 않는다).
    """
    try:
        sessions, inputs, answers = await asyncio.gather(
            _load_sessions(s3, slug), _load_inputs(s3, slug),
            load_answers(s3, prefix=answers_prefix(slug)))
        ordered = sorted(sessions.items(),
                         key=lambda kv: (_first_timestamp(kv[1]), kv[0]))
        items: list[HistoryItem] = []
        for _, lines in ordered:
            if before is not None:
                lines = _before(lines, before)
            for seg in _segments(lines, inputs):
                items.extend(transform_cli_transcript(
                    seg, answer_records=answers, question_name=QUESTION_NAME))
        return items
    except Exception:
        _log.exception("prototype history read failed for %s", slug)
        return []


async def load_context(s3: S3StoreLike, slug: str) -> dict | None:
    """세션이 닫힌 뒤 보일 컨텍스트 사용량. **다음 대화에 대해서도 맞을 때만** 준다.

    완료된 빌드를 개선하면 새 SDK 세션으로 시작하므로(handoff) 사용량이 거의 0에서
    다시 시작한다 — 닫힌 세션의 85%를 보이면 그 사실과 반대로 읽힌다. 그래서 레코드의
    세션 id가 지금의 session.json과 같고, 다음 start()를 새 대화로 만드는 handoff가
    없을 때만 돌려준다.
    """
    from aipds.proto.session import handoff_key, session_key
    record = await load_record(s3, context_key(slug))
    if record is None:
        return None
    try:
        current = json.loads(await s3.get(session_key(slug))).get("session_id")
    except Exception:
        return None
    if record.get("session_id") != current:
        return None
    try:
        await s3.get(handoff_key(slug))
    except FileNotFoundError:
        return record
    except Exception:
        return None
    return None
