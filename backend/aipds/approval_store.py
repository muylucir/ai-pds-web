# backend/aipds/approval_store.py — 승인 결정의 구조화된 기록.
#
# **왜 이 파일이 생겼는가.** 승인 여부를 `audit.md`의 산문에서 정규식으로
# 복원하고 있었다. 사용자가 게이트 버튼을 누른 사실은 그 순간 확실히 알려져
# 있는데, 그것을 버리고 에이전트가 자연어로 옮겨 적은 것을 되찾는 구조다.
#
# 실측(pilot1의 audit.md 41건): 승인 게이트 5건 중 2건만 인식됐다. 사용자가
# 채팅으로 답하기 때문이다 — "승인"은 인식되지만 "동의"(최종 승인!), "진행",
# 객관식의 "A"는 전부 실패했다. 게다가 정상 진행 서술에 'update'가 들어 있어
# 무효화 판정이 남은 승인마저 지웠다. 화면에는 "기록된 승인 이력이 없습니다"만
# 남았다.
#
# 같은 증상을 세 번 고쳤다(ca8c508 파서, 68e143f 표시조건, e18d681 언어).
# 전부 "에이전트의 출력을 어떻게 읽을까"였고, 그것은 우리가 통제하지 못하는
# 값이다. 그래서 판정의 근거를 **우리가 쓰는 값**으로 바꾼다. 이 레코드가
# 1순위 근거이고, 감사 로그 파싱은 레코드가 없는 기존 프로젝트를 위한 폴백으로
# 강등된다.
#
# **audit.md를 대체하지 않는다.** 사람이 읽는 감사 추적은 계속 에이전트가
# 상류 룰대로 쓴다(rule/은 데이터다 — 고치지 않는다). 이 레코드는 기계 판정용
# 사본이며, 둘의 역할이 다르므로 둘 다 남는다.
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from aipds.s3store import S3StoreLike

_log = logging.getLogger("aipds.approval")

#: S3Store가 projects/{pid}/를 붙이므로 프로젝트 상대 경로다(pending_store와
#: 같은 규율). 접두어로 list하면 프로젝트의 승인 이력 전체가 나온다.
APPROVALS_PREFIX = "approvals/"


@dataclass(frozen=True)
class ApprovalRecord:
    """한 번의 승인 결정.

    doc_hash가 무효화 판정의 핵심이다. 종전에는 감사 로그의 산문에서
    `수정|update|갱신`을 찾아 "문서가 바뀌었으니 재승인 필요"를 추측했는데,
    정상 진행 서술이 그 단어를 흔히 포함해 오탐이 났다(실측: idx=40).
    승인 시점의 문서 해시를 남겨 두면 현재 문서와 비교해 **사실로** 판정할 수
    있다.
    """
    document: str
    doc_hash: str
    approved_at: str


def _key(approved_at: str) -> str:
    """타임스탬프 + 랜덤 접미. 접미가 없으면 같은 초에 두 번 승인할 때(재승인
    연타) 뒤가 앞을 덮어써 감사 기록이 사라진다. 콜론은 S3 키에서 다루기
    번거로워 '-'로 바꾼다."""
    stamp = approved_at.replace(":", "-")
    return f"{APPROVALS_PREFIX}{stamp}-{uuid.uuid4().hex[:8]}.json"


async def save_approval(s3: S3StoreLike, *, document: str, doc_hash: str,
                        approved_at: str) -> None:
    """승인을 기록한다. 덮어쓰지 않고 쌓는다 — 승인은 이력이다."""
    await s3.put(_key(approved_at), json.dumps({
        "document": document,
        "doc_hash": doc_hash,
        "approved_at": approved_at,
    }, ensure_ascii=False))


def _parse(raw: str) -> ApprovalRecord | None:
    """손상된 한 건이 이력 전체를 잃게 만들지 않는다(pending_store가 손상
    페이로드에 None을 돌려주는 것과 같은 판단)."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _log.warning("approval record is not valid JSON — skipping")
        return None
    if not isinstance(data, dict):
        return None
    document, doc_hash = data.get("document"), data.get("doc_hash")
    approved_at = data.get("approved_at")
    # 필드 누락은 나중에 해시 비교에서 터진다 — 읽는 자리에서 걸러낸다.
    # `all(...)` 대신 각 값을 따로 좁히는 이유는 타입 검사기가 제너레이터
    # 안의 isinstance를 밖으로 전파하지 못하기 때문이다.
    if not (isinstance(document, str) and document
            and isinstance(doc_hash, str) and doc_hash
            and isinstance(approved_at, str) and approved_at):
        _log.warning("approval record missing required fields — skipping")
        return None
    return ApprovalRecord(document=document, doc_hash=doc_hash,
                          approved_at=approved_at)


async def load_approvals(s3: S3StoreLike) -> list[ApprovalRecord]:
    """승인 이력을 시간순으로. 없으면 빈 리스트 — 이 기능 이전의 모든
    프로젝트가 그 상태이고, 그때는 감사 로그 폴백이 판정한다."""
    keys = await s3.list(APPROVALS_PREFIX)
    records: list[ApprovalRecord] = []
    # 키가 타임스탬프로 시작하므로 사전순 == 시간순이다(ISO 8601의 성질,
    # ProjectRegistry.list_ids가 created_at에 쓰는 것과 같은 규율).
    for key in sorted(keys):
        try:
            raw = await s3.get(key)
        except FileNotFoundError:
            continue  # list와 get 사이에 삭제됐다
        record = _parse(raw)
        if record is not None:
            records.append(record)
    return records
