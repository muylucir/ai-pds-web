# backend/tests/test_doc_references.py — parsers/doc_references.missing_references
#
# 예문은 2026-10-06 드릴 버킷의 실제 discovery-document.md 문장 모양을 따른다(ko 4종,
# 템플릿 경로 1종). 라벨 문구는 프로젝트마다 다르게 번역되므로 경로 모양만 단정한다.
from __future__ import annotations

from aipds.parsers.doc_references import missing_references

DOCS = [
    "aiplc-docs/aiplc-state.md",
    "aiplc-docs/discovery/discovery-document.md",
    "aiplc-docs/discovery/prototype/prototype-spec.md",
    "aiplc-docs/discovery/prototype/build-instructions.md",
    "aiplc-docs/discovery/prototype/validation-results.md",
]


def test_a_cited_results_file_that_is_gone_is_reported():
    """novadesk-2의 모양: Part 2가 근거 파일을 가리키는데 파일이 없다."""
    text = "## 검증 결과\n전체 결과: `prototype/validation-results.md`\n- **응답자**: 5명\n"
    paths = [p for p in DOCS if not p.endswith("validation-results.md")]
    assert missing_references(text, paths) == ["prototype/validation-results.md"]


def test_a_reference_rooted_at_discovery_is_resolved_the_same_way():
    """test1111의 모양: 기준이 `discovery/`다. 있으면 통과, 없으면 그대로 보고."""
    text = "상세: `discovery/prototype/validation-results.md`\n"
    assert missing_references(text, DOCS) == []
    paths = [p for p in DOCS if not p.endswith("validation-results.md")]
    assert missing_references(text, paths) == ["discovery/prototype/validation-results.md"]


def test_a_korean_particle_glued_to_the_path_does_not_hide_it():
    """`\\b`로 끝을 자르면 `.md에`에서 놓친다 — 파이썬의 `\\w`는 한글을 포함한다."""
    text = ("전체 명세는 `prototype/prototype-spec.md`에 있습니다. "
            "근거는 iteration-log.md에 있다.")
    assert missing_references(text, DOCS) == ["iteration-log.md"]


def test_a_bare_file_name_resolves_against_any_directory():
    text = "기준: `build-instructions.md` 와 aiplc-docs/aiplc-state.md"
    assert missing_references(text, DOCS) == []


def test_a_name_matches_whole_segments_only():
    """`spec.md`가 `prototype-spec.md`의 꼬리 문자열이라고 있는 것이 되면 안 된다."""
    assert missing_references("`spec.md` 참조", DOCS) == ["spec.md"]


def test_a_change_history_said_to_be_absent_is_not_a_broken_link():
    """tobacco의 문장. 이 파일은 없는 것이 "변경 없음"이라는 뜻이다."""
    text = "승인 이후 변경 이력(`change-history.md`)이 없으므로 승인된 명세가 그대로 기준이다."
    assert missing_references(text, DOCS) == []


def test_template_paths_and_globs_are_not_references():
    """test2222의 모양: 부속 문서 목록이 슬러그 템플릿으로 적혀 있다."""
    text = ("- `discovery/prototypes/{slug}/PROTOTYPE-{slug}.md`\n"
            "- `discovery/prototypes/{slug}/design-context.md`\n"
            "- `aiplc-docs/discovery/prototypes/*/PROTOTYPE-*.md`\n")
    assert missing_references(text, DOCS) == []


def test_urls_and_absolute_paths_are_not_references():
    text = ("원본: https://github.com/aws-samples/sample-ai-plc/blob/main/README.md\n"
            "작업 디렉터리 /opt/aipds/workspaces/x/notes.md")
    assert missing_references(text, DOCS) == []


def test_each_missing_reference_is_reported_once_in_order():
    text = "`b.md`, `a.md`, 다시 `b.md`"
    assert missing_references(text, DOCS) == ["b.md", "a.md"]
