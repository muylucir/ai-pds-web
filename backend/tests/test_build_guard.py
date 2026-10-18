# backend/tests/test_build_guard.py — the build agent's Bash gate.
#
# **Why this gate exists (the 2026-08-01 incident).** The build agent launched Playwright chromium for
# browser verification, that verification targeted port 3000, and the AI-PDS
# frontend died with SIGKILL (journalctl status=9/KILL, core dump with
# Unit=aipds-backend.service). The backend and frontend **run as the same user (`aipds`)
# as the build agent**, so nothing stopped it, and workshop participants' screens showed "연결이
# 끊겼다" (connection lost).
#
# The mitigations at the time were two, and **neither was code**:
#   1. Narrowing to `skills=["shadcn-design"]` — the cause was the CLI's bundled skill `run` ("Launch and drive
#      this project's app... browser-driven") being turned on along with `skills="all"`.
#      But as that comment in builder.py itself notes, this is
#      **a context filter, not a sandbox** — it only hides skills; Bash is untouched.
#   2. The "Processes and ports" section of `proto-config/CLAUDE.md` — **prose only.**
#
# So until now nothing actually blocked Bash. This module is that.
#
# It is the third of the same shape in the repo: test_discovery_guard.py recorded the same failure in Discovery (a single
# self-contained HTML file), and test_proto_session.py's
# test_first_prompt_forbids_writing_files_before_approval recorded it in the builder, and both first tried
# to block it with wording. The third starts with code.
#
# The verdict function is **language-neutral**: it returns only the fragment being refused, and the wording is
# owned in two versions by proto/prompts.py (that file header's convention — putting Korean here would
# leak into English projects).
from __future__ import annotations

import pytest

from aipds.proto.build_guard import bash_denial, fetch_denial, user_url_hosts


# ---- The normal build path must pass ----
#
# False positives are this gate's biggest risk. If builds get blocked, pressure builds to turn the gate off,
# and then what prevents incidents goes back to being prose (the same comment in discovery_guard).

def test_the_build_itself_is_allowed():
    """`npm run build` is the means of verification — if it is blocked, building is impossible."""
    assert bash_denial("npm run build") is None


def test_installing_dependencies_is_allowed():
    assert bash_denial("npm install") is None
    assert bash_denial("npm ci") is None


def test_ordinary_inspection_is_allowed():
    assert bash_denial("ls -la app") is None
    assert bash_denial("cat package.json") is None
    assert bash_denial("npm run lint") is None
    assert bash_denial("npx tsc --noEmit") is None


def test_a_command_mentioned_inside_quotes_is_not_a_command():
    """Quoted content is erased before the verdict.

    If normal commands that write conventions or leave logs with `echo` were blocked, that would be a false positive, and false positives
    shorten the gate's life (same reason as discovery_guard._QUOTED).
    """
    assert bash_denial('echo "never run npm run dev"') is None
    assert bash_denial("echo 'playwright is forbidden'") is None


def test_no_command_is_allowed():
    """With nothing to judge, it passes — this gate's failure direction must be 'pass'."""
    assert bash_denial(None) is None
    assert bash_denial("") is None
    assert bash_denial({"not": "a string"}) is None


# ---- Browser automation: the direct cause of the 2026-08-01 incident ----

def test_playwright_is_denied():
    assert "playwright" in bash_denial("npx playwright test")


def test_the_e2e_script_is_denied():
    """`playwright.config.ts` targets port 3000, so this reproduces the incident exactly."""
    assert bash_denial("npm run test:e2e") is not None


def test_puppeteer_is_denied():
    assert "puppeteer" in bash_denial("node scripts/shot.js --puppeteer")


def test_headless_chrome_is_denied():
    assert bash_denial("chrome-headless --screenshot") is not None
    assert bash_denial("chromium --headless https://localhost") is not None


# ---- dev and production servers: they keep holding a port after the turn ends ----

def test_npm_run_dev_is_denied():
    assert bash_denial("npm run dev") is not None


def test_npm_run_start_is_denied():
    """`npm run start` is hosting's job (proto/host.py). If the build agent starts it, it keeps
    holding the port, and cleaning that up led to the 2026-08-01 kill."""
    assert bash_denial("npm run start") is not None


def test_other_package_managers_are_denied_too():
    assert bash_denial("pnpm dev") is not None
    assert bash_denial("yarn start") is not None


def test_the_framework_binary_is_denied():
    """The form that bypasses `npm run` and calls the framework directly."""
    assert bash_denial("npx next dev") is not None
    assert bash_denial("npx next start -p 4123") is not None


def test_wrapping_the_server_in_setsid_is_still_denied():
    """Exactly the workaround recipe `proto-config/CLAUDE.md` used to teach — this test is the
    grounds for deleting that section."""
    assert bash_denial("setsid npm run start > /tmp/smoke.log 2>&1 &") is not None


# ---- Process termination: do not kill what I did not start ----

def test_pkill_is_denied():
    assert "pkill" in bash_denial("pkill -f node")


def test_killall_is_denied():
    assert "killall" in bash_denial("killall node")


def test_fuser_kill_is_denied():
    assert bash_denial("fuser -k 3000/tcp") is not None


def test_killing_a_pid_from_a_substitution_is_denied():
    """`kill -9 $(lsof -ti:3000)` — the shape of the cleanup command at the time of the incident.

    A bare `kill <pid>` is not blocked: starting servers is itself blocked, so there is no own process
    to kill, and blocking broadly increases false positives.
    """
    assert bash_denial("kill -9 $(lsof -ti:3000)") is not None


def test_killing_a_pid_you_hold_is_allowed():
    assert bash_denial("kill 12345") is None


# ---- Ports 3000 and 8000: AI-PDS itself ----
#
# The range hosting assigns is range(4000, 8000) (_scan_port in proto/host.py),
# so 3000 and 8000 are never assigned to any prototype — blocking them causes no conflict.

def test_probing_the_frontend_port_is_denied():
    assert bash_denial("lsof -ti:3000") is not None


def test_probing_the_backend_port_is_denied():
    assert bash_denial("curl -s http://localhost:8000/health") is not None


def test_binding_the_frontend_port_is_denied():
    assert bash_denial("PORT=3000 npm run build") is not None


def test_a_hosted_prototype_port_is_allowed():
    """Ports inside the assigned range (4000-7999) are normal — only 3000 and 8000 are blocked."""
    assert bash_denial("curl -s http://localhost:4123/") is None
    assert bash_denial("lsof -ti:7999") is None


def test_a_number_that_merely_contains_the_port_is_allowed():
    """Catching `13000` or `80000` would be a false positive."""
    assert bash_denial("curl -s http://localhost:13000/") is None
    assert bash_denial("echo 80000 > /tmp/n") is None


# ---- CLI settings files ----

def test_touching_cli_settings_from_bash_is_denied():
    assert bash_denial("echo x > .claude/settings.json") == ".claude/settings.json"
    assert bash_denial("cp planted.json .mcp.json") == ".mcp.json"


def test_ordinary_json_files_are_allowed():
    assert bash_denial("cat package.json tsconfig.json") is None
    assert bash_denial("cat app/settings.json") is None


# ---- WebFetch: the sites of URLs the user wrote directly in the chat are opened ----



def test_hosts_come_from_urls_the_user_wrote():
    text = ("https://store.example.com/ranks 이게 베스트 목록이야. "
            "(http://Shop.Example.org/a?b=1) 도 봐줘")
    assert user_url_hosts(text) == {"store.example.com", "shop.example.org"}


def test_text_without_a_scheme_is_not_a_url():
    """Catching fragments without a scheme too would turn file paths and version strings into hosts."""
    assert user_url_hosts("store.example.com/ranks 와 v1.2.3, src/app.tsx") == set()
    assert user_url_hosts(None) == set()


@pytest.mark.parametrize("url", [
    "http://localhost:8000/projects",
    "http://api.localhost/",
    "http://printer.local/",
    "http://metadata.google.internal/",
    "http://127.0.0.1:3000/",
    "http://10.0.0.5/",
    "http://169.254.169.254/latest/meta-data/",
    "http://[::1]:8000/",
    "http://127.1/",            # WebFetch's URL parser reads abbreviated IPv4 as 127.0.0.1
    "http://2130706433/",
    "http://0x7f.1/",
])
def test_internal_addresses_are_never_opened_by_a_user_url(url):
    """Even if the user wrote it, it must not become a way to open the inside of this server (the backend, other projects'
    preview ports)."""
    assert user_url_hosts(url) == set()


def test_a_user_host_opens_exactly_that_host():
    """Opening subdomains too would make one line of `https://co.kr` into all of `*.co.kr`."""
    hosts = user_url_hosts("https://store.example.com/ranks")
    assert fetch_denial("https://store.example.com/robots.txt", hosts) is None
    assert fetch_denial("https://evil.store.example.com/", hosts) == "evil.store.example.com"
    assert fetch_denial("https://example.com/", hosts) == "example.com"
    assert fetch_denial("https://collector.example.net/?d=x", hosts) == "collector.example.net"


def test_without_user_hosts_only_the_docs_open():
    assert fetch_denial("https://ui.shadcn.com/blocks") is None
    assert fetch_denial("https://store.example.com/") == "store.example.com"
