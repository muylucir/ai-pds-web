# 승인 레코드의 영속 계약.
#
# **왜 이 스토어가 생겼는가.** 승인 여부를 `audit.md`의 산문에서 정규식으로
# 복원하고 있었다. 사용자가 버튼을 누른 사실은 그 순간 확실히 알려져 있는데
# 그것을 버리고, 에이전트가 자연어로 옮겨 적은 것을 되찾는 구조다. 표현이
# 조금 달라지면 결정이 사라진다.
#
# 실측(pilot1의 audit.md 41건): 승인 게이트 5건 중 2건만 인식됐다.
#   idx=13 "승인"  → 인식
#   idx=17 "A"     → 실패 (객관식으로 답한 승인)
#   idx=33 "진행"  → 실패
#   idx=37 "승인"  → 인식
#   idx=41 "동의"  → 실패 (**최종 승인**)
# 그리고 idx=40의 정상 진행 서술("...Written to Living Document...")에 'update'가
# 들어 있어 isDocumentChange가 idx=37의 승인마저 무효화했다. 결과가 화면의
# "기록된 승인 이력이 없습니다"다.
#
# 같은 증상을 세 번 고쳤는데(ca8c508 파서, 68e143f 표시조건, e18d681 언어)
# 전부 "에이전트의 출력을 어떻게 읽을까"였다. 우리가 통제하지 못하는 값을
# 판정 근거로 쓰는 한 다음 표현에서 또 깨진다. 그래서 근거를 우리가 쓰는
# 값으로 바꾼다.
import json

import pytest

from aipds.approval_store import (
    APPROVALS_PREFIX, ApprovalRecord, load_approvals, save_approval,
)
from tests.fakes.in_memory_s3 import FakeS3Store


@pytest.mark.asyncio
async def test_saved_approval_is_readable_back():
    s3 = FakeS3Store()
    await save_approval(s3, document="aiplc-docs/discovery/discovery-document.md",
                        doc_hash="abc123", approved_at="2026-08-10T03:00:00Z")

    records = await load_approvals(s3)
    assert len(records) == 1
    assert records[0].document == "aiplc-docs/discovery/discovery-document.md"
    assert records[0].doc_hash == "abc123"
    assert records[0].approved_at == "2026-08-10T03:00:00Z"


@pytest.mark.asyncio
async def test_records_live_under_the_project_prefix():
    # S3Store가 projects/{pid}/를 붙이므로 키는 그 아래 상대 경로다
    # (pending_store와 같은 규율).
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="h",
                        approved_at="2026-08-10T03:00:00Z")
    assert all(k.startswith(APPROVALS_PREFIX) for k in s3.blobs)


@pytest.mark.asyncio
async def test_approvals_accumulate_and_come_back_in_time_order():
    # 승인은 이력이다 — 마지막 것으로 덮어쓰지 않는다. 수정 요청 후 재승인이
    # 일어나면 그 순서가 "승인 게이트 이력" 패널의 내용이 된다.
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="h1",
                        approved_at="2026-08-10T01:00:00Z")
    await save_approval(s3, document="d.md", doc_hash="h2",
                        approved_at="2026-08-10T02:00:00Z")

    records = await load_approvals(s3)
    assert [r.doc_hash for r in records] == ["h1", "h2"]


@pytest.mark.asyncio
async def test_two_approvals_in_the_same_second_do_not_overwrite_each_other():
    # 키가 타임스탬프뿐이면 같은 초에 두 번 저장할 때 하나가 사라진다. 초 단위
    # 충돌은 재승인 연타로 실제로 일어날 수 있고, 잃는 것이 감사 기록이다.
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="h1",
                        approved_at="2026-08-10T01:00:00Z")
    await save_approval(s3, document="d.md", doc_hash="h2",
                        approved_at="2026-08-10T01:00:00Z")
    assert len(await load_approvals(s3)) == 2


@pytest.mark.asyncio
async def test_no_records_is_an_empty_list_not_an_error():
    # 이 기능 이전의 모든 프로젝트가 이 상태다 — 500이 아니라 "레코드 없음"
    # 이어야 하고, 그때는 감사 로그 폴백이 판정한다.
    assert await load_approvals(FakeS3Store()) == []


@pytest.mark.asyncio
async def test_a_corrupt_record_is_skipped_not_fatal():
    # 한 건이 깨졌다고 나머지 승인 이력을 통째로 잃으면 안 된다. pending_store가
    # 손상 페이로드에 None을 돌려주는 것과 같은 판단이다.
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="good",
                        approved_at="2026-08-10T01:00:00Z")
    s3.blobs[f"{APPROVALS_PREFIX}2026-08-10T02-00-00Z-zzzz.json"] = "{not json"

    records = await load_approvals(s3)
    assert [r.doc_hash for r in records] == ["good"]


@pytest.mark.asyncio
async def test_a_record_missing_required_fields_is_skipped():
    # 필드 누락은 나중에 해시 비교에서 터진다 — 읽는 자리에서 걸러낸다.
    s3 = FakeS3Store()
    s3.blobs[f"{APPROVALS_PREFIX}2026-08-10T01-00-00Z-aaaa.json"] = json.dumps(
        {"document": "d.md"})  # doc_hash/approved_at 없음
    assert await load_approvals(s3) == []


@pytest.mark.asyncio
async def test_record_is_json_so_the_audit_trail_stays_human_readable():
    # audit.md를 대체하지 않는다 — 사람이 읽는 감사 추적은 계속 에이전트가
    # 쓴다. 이 레코드는 기계 판정용이지만, 조사할 때 열어볼 수 있어야 한다.
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="h",
                        approved_at="2026-08-10T01:00:00Z")
    raw = next(iter(s3.blobs.values()))
    assert json.loads(raw)["doc_hash"] == "h"


def test_record_exposes_the_fields_the_gate_needs():
    # 게이트 판정에 필요한 것: 어느 문서를, 어떤 내용일 때 승인했는가.
    # doc_hash가 있어야 "승인 후 문서가 바뀌면 다시 미승인"을 추측이 아니라
    # 사실로 판정할 수 있다.
    r = ApprovalRecord(document="d.md", doc_hash="h",
                       approved_at="2026-08-10T01:00:00Z")
    assert (r.document, r.doc_hash, r.approved_at) == (
        "d.md", "h", "2026-08-10T01:00:00Z")
