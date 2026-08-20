# backend/pathfinder/routes/approval.py — 문서 승인 게이트.
#
# 왜 별도 라우트인가(POST /message로 "승인"을 보내는 것으로 충분하지 않은
# 이유): 그 경로는 승인의 유일한 기록이 **에이전트가 쓰는 audit.md**였다.
# 판정이 에이전트의 산문에 달려 있으면 표현이 흔들릴 때 결정이 사라진다 —
# 실측으로 승인 게이트 5건 중 3건이 인식되지 않았다(approval_store.py 헤더).
#
# 이 라우트는 사용자가 누른 사실을 **먼저 구조화된 레코드로 남기고**, 그 다음
# 에이전트 턴을 돌린다. 순서가 계약이다: 턴이 실패해도 승인은 남는다.
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

import aipds.app as app_module
from aipds.approval_store import load_approvals, save_approval
from aipds.routes.deps import ensure_workspace

router = APIRouter()
_log = logging.getLogger(__name__)

#: 승인 대상. 게이트는 discovery-document.md에만 붙는다(리뷰 화면의
#: isDiscoveryDocument) — 다른 파일에는 승인이라는 개념이 없다.
_DOC_PATH = "aiplc-docs/discovery/discovery-document.md"


def _hash(text: str) -> str:
    """승인 시점 문서의 지문.

    이것이 무효화 판정을 추측에서 사실로 바꾼다. 종전에는 감사 로그의 산문에서
    `수정|update|갱신`을 찾아 "문서가 바뀌었다"를 추정했는데, 정상 진행 서술이
    그 단어를 흔히 포함해 승인이 멋대로 무효화됐다(실측: pilot1 idx=40의
    "Written to Living Document"가 idx=37의 승인을 지웠다).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@router.post("/projects/{pid}/approve")
async def approve_document(pid: str):
    """문서를 승인한다. 레코드를 먼저 쓰고, 그 다음 에이전트 턴을 돌린다."""
    ws = await ensure_workspace(pid)
    try:
        text = await ws.runner.read_file(_DOC_PATH)
    except (FileNotFoundError, ValueError):
        # 빈 해시로 레코드를 쓰면 무효화 판정이 영구히 무의미해진다(무엇과
        # 비교해도 같지 않다). 게이트는 문서를 보고 있을 때만 뜨므로 정상
        # 경로로는 오지 않지만, 조용히 통과시키면 그 사실을 아무도 모른다.
        raise HTTPException(status_code=409,
                            detail="there is no discovery document to approve")

    s3 = app_module.s3_store_factory(pid)
    await save_approval(s3, document=_DOC_PATH, doc_hash=_hash(text),
                        approved_at=datetime.now(timezone.utc).isoformat())

    # 턴은 레코드 **뒤**에 돈다. 에이전트가 다음 단계로 진행하고 audit.md에
    # 사람이 읽는 기록을 남기는 것이 여전히 필요하지만(상류 룰의 요구),
    # 그 성공 여부가 승인의 존재를 좌우해서는 안 된다.
    #
    # 승인 텍스트는 프로젝트 언어를 따른다 — 이 턴은 트랜스크립트에 사용자
    # 발화로 남고 에이전트가 그 언어로 대화하고 있다(frontend의
    # approvalMarker.ts가 같은 판단을 기록해 뒀다).
    language = app_module.project_language(pid)
    turn_text = "Approved" if language == "en" else "승인"
    try:
        async for _ in ws.runner.send_message(turn_text):
            pass
    except Exception:
        # 승인은 이미 기록됐다. 턴 실패는 사용자가 다시 시도할 수 있는 일이고,
        # 여기서 500을 내면 "승인이 안 됐다"고 오해하게 만든다.
        _log.exception("approval turn failed after the record was saved")

    return {"approved": True}


@router.get("/projects/{pid}/approvals")
async def list_approvals(pid: str):
    """승인 이력 + **현재 문서 해시**.

    현재 해시를 여기서 함께 주는 이유: 프론트가 스스로 계산하면 해시 알고리즘이
    두 곳에 생기고, 둘이 어긋나는 순간 승인이 영구히 인식되지 않는다. 그 실패는
    조용하다 — 게이트가 안 열릴 뿐이라 원인을 찾기 어렵다. 해시의 정의는 승인을
    쓰는 쪽이 소유한다.

    이력이 없으면 빈 목록이다 — 이 기능 이전의 모든 프로젝트가 그 상태이고,
    그때는 프론트가 감사 로그 폴백으로 판정한다.
    """
    ws = await ensure_workspace(pid)
    records = await load_approvals(app_module.s3_store_factory(pid))
    try:
        current = _hash(await ws.runner.read_file(_DOC_PATH))
    except (FileNotFoundError, ValueError):
        # 문서가 없으면 비교할 것이 없다. 빈 문자열이 아니라 null이어야 한다 —
        # 프론트가 "해시가 있다"고 오해하면 승인 여부를 잘못 판정한다.
        current = None
    return {
        "approvals": [
            {"document": r.document, "doc_hash": r.doc_hash,
             "approved_at": r.approved_at}
            for r in records
        ],
        "current_doc_hash": current,
    }
