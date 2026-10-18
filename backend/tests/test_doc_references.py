# backend/tests/test_doc_references.py — parsers/doc_references.missing_references
#
# The examples follow the shape of real discovery-document.md sentences from the 2026-10-06
# drill bucket (4 ko variants, 1 template path). Label wording is translated differently
# per project, so only the path shape is asserted.
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
    """Shape from ws-l: Part 2 points to a source file, but the file does not exist."""
    text = "## 검증 결과\n전체 결과: `prototype/validation-results.md`\n- **응답자**: 5명\n"
    paths = [p for p in DOCS if not p.endswith("validation-results.md")]
    assert missing_references(text, paths) == ["prototype/validation-results.md"]


def test_a_reference_rooted_at_discovery_is_resolved_the_same_way():
    """Shape from test1111: the base is `discovery/`. Passes if present, reported as is if missing."""
    text = "상세: `discovery/prototype/validation-results.md`\n"
    assert missing_references(text, DOCS) == []
    paths = [p for p in DOCS if not p.endswith("validation-results.md")]
    assert missing_references(text, paths) == ["discovery/prototype/validation-results.md"]


def test_a_korean_particle_glued_to_the_path_does_not_hide_it():
    """Cutting the end with `\\b` misses `.md에`: Python's `\\w` includes Hangul."""
    text = ("전체 명세는 `prototype/prototype-spec.md`에 있습니다. "
            "근거는 iteration-log.md에 있다.")
    assert missing_references(text, DOCS) == ["iteration-log.md"]


def test_a_bare_file_name_resolves_against_any_directory():
    text = "기준: `build-instructions.md` 와 aiplc-docs/aiplc-state.md"
    assert missing_references(text, DOCS) == []


def test_a_name_matches_whole_segments_only():
    """`spec.md` must not count as present just because it is a suffix of `prototype-spec.md`."""
    assert missing_references("`spec.md` 참조", DOCS) == ["spec.md"]


def test_a_change_history_said_to_be_absent_is_not_a_broken_link():
    """Sentence from ws-b. For this file, being absent means "no changes"."""
    text = "승인 이후 변경 이력(`change-history.md`)이 없으므로 승인된 명세가 그대로 기준이다."
    assert missing_references(text, DOCS) == []


def test_template_paths_and_globs_are_not_references():
    """Shape from ws-n: the list of supporting documents is written as a slug template."""
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
