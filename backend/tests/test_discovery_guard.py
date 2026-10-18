# backend/tests/test_discovery_guard.py — Discovery's write-scope gate.
#
# **Why this gate exists (the 2026-08-16 defect).** The Discovery agent went ahead and
# built a prototype in the workspace as `prototype/index.html`. Building and hosting are
# the job of the Prototypes tab, and Discovery's role ends at writing the spec
# (discovery-config/CLAUDE.md), but that rule was **prose only**, and what the prose
# forbade was build *commands*: npm install / npm run dev / subprocesses / port
# selection. A single self-contained HTML file needs none of those; the agent's own
# report ("API 키·패키지 설치·외부 통신이 모두 불필요합니다", i.e. no API key,
# package install, or external network needed) is itself proof that it satisfied
# every clause.
#
# Why there was no enforcement: Discovery runs with `bypassPermissions`
# (DEFAULT_PERMISSION_MODE in claude_driver.py). The SDK warns about this exact
# situation and even prescribes the fix (_get_can_use_tool_shadowed_warning in
# claude_agent_sdk/types.py): "can_use_tool will not be invoked ... To gate every tool
# call, use a PreToolUse hook instead." This module is the decision part of that hook.
#
# This is the second time the same failure has occurred in the repo:
# test_proto_session.py's test_first_prompt_forbids_writing_files_before_approval
# recorded the same shape in the builder, and the mitigation chosen then was also prompt
# wording. So this time we block it in code.
#
# The decision functions are language-neutral: they return only the denied target
# (path/command fragment), and agent/prompts.py owns the wording in two versions (the
# convention in that file's header).
from __future__ import annotations

import pytest

from aipds.agent.discovery_guard import (audit_overwrite_denial, bash_denial,
                                         web_owned_denial, write_denial)

WS = "/ws"


# ---- Write/Edit/MultiEdit: allow only inside aiplc-docs/ ----

def test_writing_under_aiplc_docs_is_allowed():
    assert write_denial("/ws/aiplc-docs/discovery/discovery-document.md", WS) is None
    assert write_denial("aiplc-docs/audit.md", WS) is None


def test_the_slugged_prototype_spec_is_allowed():
    """Confirms that this gate and the slug convention share the same boundary.

    The only path by which the Prototypes tab creates a card (_SPEC_RE in
    routes/prototypes.py) is inside aiplc-docs/, so it must pass; if the gate blocked
    this, slug artifacts could not be written at all and the problem would just be
    inverted.
    """
    path = "aiplc-docs/discovery/prototypes/maint-support/PROTOTYPE-maint-support.md"
    assert write_denial(path, WS) is None


def test_the_html_that_caused_this_gate_is_denied():
    """The actual artifact of this defect. Returns the relative path as the denied
    target."""
    assert write_denial("/ws/prototype/index.html", WS) == "prototype/index.html"
    assert write_denial("prototype/app.js", WS) == "prototype/app.js"


def test_any_path_outside_aiplc_docs_is_denied():
    # The extension is not considered; even a .md outside aiplc-docs/ is not an artifact.
    assert write_denial("notes.md", WS) == "notes.md"
    assert write_denial("src/main.py", WS) == "src/main.py"


def test_a_lookalike_prefix_does_not_slip_through():
    """A **different** directory whose name starts with `aiplc-docs` is outside. An
    implementation using prefix string comparison would let this pass."""
    assert write_denial("aiplc-docs-backup/x.md", WS) == "aiplc-docs-backup/x.md"


def test_workspace_escapes_are_denied():
    """An escape is outside the workspace, so it is of course denied. It is a path for
    which pathsafe.workspace_relative returns None, and then the raw input is reported
    as the denied target."""
    assert write_denial("/etc/passwd", WS) == "/etc/passwd"
    assert write_denial("../outside.md", WS) == "../outside.md"


def test_a_missing_path_is_not_denied():
    """A call without file_path is not this gate's concern; denying it with no basis for the
    decision would let one unrecognized tool shape block the turn."""
    assert write_denial("", WS) is None
    assert write_denial(None, WS) is None


# ---- Write/Edit/MultiEdit: deny web-owned files inside aiplc-docs/ ----
# The survey aggregate is regenerated every time by "결과 취합" (Synthesize results). A Step
# 6 analysis the agent writes there disappears at the next synthesis
# (proto/layout.SURVEY_AGGREGATE).

def test_the_survey_aggregate_is_denied_for_both_layouts():
    for rel in ("aiplc-docs/discovery/prototype/survey-aggregate.md",
                "aiplc-docs/discovery/prototypes/maint/survey-aggregate.md"):
        assert web_owned_denial(rel, WS) == rel
        assert web_owned_denial(f"/ws/{rel}", WS) == rel


def test_the_change_history_is_denied_for_both_layouts():
    """The change history is appended by build sessions and read as input by the next
    build session (proto/layout.CHANGE_HISTORY). If Discovery edits it, the builder's
    trusted record of "what was built" changes; the reconciliation pass's conclusions go
    to spec-as-built.md."""
    for rel in ("aiplc-docs/discovery/prototype/change-history.md",
                "aiplc-docs/discovery/prototypes/maint/change-history.md"):
        assert web_owned_denial(rel, WS) == rel
        assert web_owned_denial(f"/ws/{rel}", WS) == rel


def test_the_reconciled_spec_is_allowed():
    """This is where the reconciliation pass writes. Blocking this too leaves nowhere to
    reflect the history."""
    rel = "aiplc-docs/discovery/prototype/spec-as-built.md"
    assert web_owned_denial(rel, WS) is None
    assert write_denial(rel, WS) is None


@pytest.mark.parametrize("language", ["ko", "en"])
def test_each_web_owned_file_names_where_the_work_goes_instead(language):
    """The denial wording offers an alternative; with the same wording the model retries
    the same path. Each file has a different destination: survey aggregate →
    validation-results.md, change history → spec-as-built.md."""
    from aipds.agent import prompts
    agg = prompts.web_owned_doc(language, "aiplc-docs/discovery/prototype/survey-aggregate.md")
    hist = prompts.web_owned_doc(language, "aiplc-docs/discovery/prototype/change-history.md")
    assert "validation-results.md" in agg and "spec-as-built.md" not in agg
    assert "spec-as-built.md" in hist and "validation-results.md" not in hist


def test_the_rules_results_file_is_allowed():
    """This is where the Step 6 synthesis goes. Blocking this too leaves the agent
    nowhere to write."""
    rel = "aiplc-docs/discovery/prototype/validation-results.md"
    assert web_owned_denial(rel, WS) is None
    assert write_denial(rel, WS) is None


def test_the_web_owned_name_outside_aiplc_docs_is_not_this_gates_call():
    """Outside aiplc-docs/ is already denied by write_denial; this decision does not
    step in."""
    assert web_owned_denial("prototype/survey-aggregate.md", WS) is None
    assert web_owned_denial("../survey-aggregate.md", WS) is None
    assert web_owned_denial(None, WS) is None


# ---- Write: do not wholesale overwrite an existing audit.md ----
# The web app first records user input verbatim (aipds/audit_log). Overwriting would
# erase that record.

def test_writing_over_an_existing_audit_log_is_denied_but_edit_is_not(tmp_path):
    ws = str(tmp_path)
    (tmp_path / "aiplc-docs").mkdir()
    (tmp_path / "aiplc-docs" / "audit.md").write_text("# AI-PLC Audit Log\n", encoding="utf-8")
    assert audit_overwrite_denial("Write", f"{ws}/aiplc-docs/audit.md", ws) == "aiplc-docs/audit.md"
    assert audit_overwrite_denial("Edit", f"{ws}/aiplc-docs/audit.md", ws) is None
    assert audit_overwrite_denial("Write", f"{ws}/aiplc-docs/aiplc-state.md", ws) is None


def test_creating_the_audit_log_when_it_does_not_exist_is_allowed(tmp_path):
    """On paths the web app does not record (e.g. resuming a pre-web session), the agent
    creating it first is not blocked."""
    ws = str(tmp_path)
    assert audit_overwrite_denial("Write", f"{ws}/aiplc-docs/audit.md", ws) is None


# ---- Bash: deny builds, servers, and redirection outside the workspace ----

def test_package_managers_are_denied_outright():
    """Discovery writes markdown; there is no job that needs a package manager. The
    reason we block the invocation itself rather than enumerating subcommands is that
    the lesson of this defect is that an enumeration invites the items it misses."""
    for cmd in ("npm install", "npm run dev", "npm ci", "npx create-next-app x",
                "pnpm build", "yarn dev", "bun install"):
        assert bash_denial(cmd) is not None, cmd


def test_dev_servers_are_denied():
    for cmd in ("python3 -m http.server 8000",
                "cd prototype && python3 -m http.server 8000",
                "python -m SimpleHTTPServer",
                "uvicorn app:app", "gunicorn app:app", "flask run",
                "npx serve .", "php -S localhost:8000", "vite"):
        assert bash_denial(cmd) is not None, cmd


def test_redirection_outside_aiplc_docs_is_denied():
    """Blocking Write is not enough: one line of Bash bypasses the gate, so the same
    boundary is checked here too."""
    assert bash_denial("echo '<html>' > prototype/index.html") is not None
    assert bash_denial("cat <<EOF >> app.js") is not None
    assert bash_denial("echo x | tee prototype/style.css") is not None


def test_redirection_into_aiplc_docs_is_allowed():
    """There is no reason to block writing an artifact through the shell as such."""
    assert bash_denial("echo '# note' > aiplc-docs/audit.md") is None
    assert bash_denial("echo x >> aiplc-docs/discovery/notes.md") is None


def test_fd_redirections_are_not_mistaken_for_files():
    """`2>&1` is fd duplication, not a file. Reading it as a file would block every common
    idiom, which in turn creates pressure to turn the gate off."""
    assert bash_denial("grep -r foo aiplc-docs/ 2>&1") is None
    assert bash_denial("ls -la 2>/dev/null") is None
    assert bash_denial("find . -name '*.md' 2>/dev/null | head") is None


def test_ordinary_read_only_commands_are_allowed():
    """The gate is a denylist; it must not block the normal paths Discovery uses to
    explore the rules (if it were an allowlist, all of these would be blocked)."""
    for cmd in ("ls aiplc-docs", "cat aiplc-docs/audit.md", "wc -l x.md",
                "grep -n Question aiplc-docs/a-questions.md",
                "find aiplc-docs -name '*-questions.md'", "pwd"):
        assert bash_denial(cmd) is None, cmd


def test_an_empty_command_is_not_denied():
    assert bash_denial("") is None
    assert bash_denial(None) is None


def test_the_denial_names_what_was_refused():
    """The denial reason goes back to the model, so it must point out what was caught;
    otherwise the model retries the same command in a different form and gets stuck in a
    loop."""
    assert "npm" in bash_denial("npm run dev")
    assert "prototype/index.html" in bash_denial("echo x > prototype/index.html")


# ---- CLI settings files and notebooks ----

def test_bash_touching_cli_settings_is_denied():
    """Redirection is blocked by bash_denial, but cp, sed -i, and interpreters are not
    inspected; settings files have fixed names, so they are blocked by name."""
    from aipds.agent.discovery_guard import settings_denial
    assert settings_denial("cp /tmp/x .claude/settings.json") == ".claude/settings.json"
    assert settings_denial("sed -i s/a/b/ .mcp.json") == ".mcp.json"
    assert settings_denial("grep -rn Stage aiplc-docs/") is None


def test_notebook_edit_is_judged_like_a_write():
    from aipds.agent.discovery_guard import tool_write_path
    assert tool_write_path("NotebookEdit", {"notebook_path": "/ws/x.ipynb"}) == "/ws/x.ipynb"
    assert write_denial(tool_write_path("NotebookEdit", {"notebook_path": "/ws/x.ipynb"}),
                        WS) == "x.ipynb"
    assert tool_write_path("Read", {"file_path": "/ws/a"}) is None
