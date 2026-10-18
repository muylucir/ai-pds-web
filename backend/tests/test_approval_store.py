# Persistence contract for approval records.
#
# **Why this store exists.** Approval status was being reconstructed by regex from the
# prose in `audit.md`. The fact that the user pressed the button is known with certainty
# at that moment, yet the structure discarded it and tried to recover what the agent
# transcribed in natural language. If the wording changes slightly, the decision is lost.
#
# Observed (41 entries in ws-m's audit.md): only 2 of 5 approval gates were recognized.
#   idx=13 "승인"  → recognized
#   idx=17 "A"     → failed (approval given as a multiple-choice answer)
#   idx=33 "진행"  → failed
#   idx=37 "승인"  → recognized
#   idx=41 "동의"  → failed (**final approval**)
# And the normal-progress narration at idx=40 ("...Written to Living Document...") contained
# 'update', so isDocumentChange invalidated even the approval at idx=37. The result was the
# on-screen message "기록된 승인 이력이 없습니다" (no recorded approval history).
#
# The same symptom was fixed three times (ca8c508 parser, 68e143f display condition,
# e18d681 language), and every fix was about "how do we read the agent's output". As long as
# a value we do not control is the basis for the decision, it will break again on the next
# phrasing. So we switch the basis to a value we write ourselves.
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
    # S3Store prepends projects/{pid}/, so keys are relative paths beneath it
    # (the same discipline as pending_store).
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="h",
                        approved_at="2026-08-10T03:00:00Z")
    assert all(k.startswith(APPROVALS_PREFIX) for k in s3.blobs)


@pytest.mark.asyncio
async def test_approvals_accumulate_and_come_back_in_time_order():
    # Approvals are history — never overwritten by the latest one. When re-approval
    # happens after a change request, that order becomes the content of the "approval gate history" panel.
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="h1",
                        approved_at="2026-08-10T01:00:00Z")
    await save_approval(s3, document="d.md", doc_hash="h2",
                        approved_at="2026-08-10T02:00:00Z")

    records = await load_approvals(s3)
    assert [r.doc_hash for r in records] == ["h1", "h2"]


@pytest.mark.asyncio
async def test_two_approvals_in_the_same_second_do_not_overwrite_each_other():
    # If the key were only a timestamp, saving twice in the same second would lose one.
    # Same-second collisions can really happen with repeated re-approval clicks, and what is lost is the audit record.
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="h1",
                        approved_at="2026-08-10T01:00:00Z")
    await save_approval(s3, document="d.md", doc_hash="h2",
                        approved_at="2026-08-10T01:00:00Z")
    assert len(await load_approvals(s3)) == 2


@pytest.mark.asyncio
async def test_no_records_is_an_empty_list_not_an_error():
    # Every project from before this feature is in this state — it must be "no records",
    # not a 500, and in that case the audit log fallback decides.
    assert await load_approvals(FakeS3Store()) == []


@pytest.mark.asyncio
async def test_a_corrupt_record_is_skipped_not_fatal():
    # One corrupt record must not cost the rest of the approval history. This is the same
    # judgment as pending_store returning None for a damaged payload.
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="good",
                        approved_at="2026-08-10T01:00:00Z")
    s3.blobs[f"{APPROVALS_PREFIX}2026-08-10T02-00-00Z-zzzz.json"] = "{not json"

    records = await load_approvals(s3)
    assert [r.doc_hash for r in records] == ["good"]


@pytest.mark.asyncio
async def test_a_record_missing_required_fields_is_skipped():
    # Missing fields would blow up later in hash comparison — filter them out at the read site.
    s3 = FakeS3Store()
    s3.blobs[f"{APPROVALS_PREFIX}2026-08-10T01-00-00Z-aaaa.json"] = json.dumps(
        {"document": "d.md"})  # no doc_hash/approved_at
    assert await load_approvals(s3) == []


@pytest.mark.asyncio
async def test_record_is_json_so_the_audit_trail_stays_human_readable():
    # This does not replace audit.md — the agent keeps writing the human-readable audit
    # trail. This record is for machine decisions, but it must be openable during an investigation.
    s3 = FakeS3Store()
    await save_approval(s3, document="d.md", doc_hash="h",
                        approved_at="2026-08-10T01:00:00Z")
    raw = next(iter(s3.blobs.values()))
    assert json.loads(raw)["doc_hash"] == "h"


def test_record_exposes_the_fields_the_gate_needs():
    # What the gate decision needs: which document was approved, with what content.
    # doc_hash is what lets "if the document changes after approval, it becomes unapproved
    # again" be decided as fact rather than guesswork.
    r = ApprovalRecord(document="d.md", doc_hash="h",
                       approved_at="2026-08-10T01:00:00Z")
    assert (r.document, r.doc_hash, r.approved_at) == (
        "d.md", "h", "2026-08-10T01:00:00Z")
