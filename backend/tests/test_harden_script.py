# backend/tests/test_harden_script.py — `sync` of infra/scripts/aipds-harden (applying the
# values the stack left in SSM).
#
# sync is run by a timer as root every 2 minutes, and on a new instance it runs inside user-data
# (set -e). So "what it does not change" is half the spec: if a value is missing or unreadable it
# does not turn things off, it does not revert a state the operator paused (hold), it does not
# restart if nothing changed, and under no circumstances does it end in failure.
#
# The script is copied into a temporary directory with its absolute paths (/etc,
# /usr/local/libexec) redirected inside it, and aws, systemctl, curl, and sleep are faked. It
# exercises the actual shell behavior of writing and reading real files — string checks cannot
# catch the kind of defect where one missing file under `set -e` halts boot (it actually
# happened once).
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "infra" / "scripts"
ARN = "arn:aws:iam::123456789012:role/AipdsAgentCredsStack-AgentRole-X"
ORIGIN = "https://d1example.cloudfront.net"
SECRET = "arn:aws:secretsmanager:ap-northeast-2:123456789012:secret:Preview-AbCdEf"

FAKE_AWS = """#!/bin/bash
# aws ssm get-parameter --region R --name N --query ... --output text
# aws s3api get-bucket-cors|put-bucket-cors --region R --bucket B [--cors-configuration JSON]
op=$2
while [ $# -gt 0 ]; do
  case "$1" in --name) name=$2 ;; --cors-configuration) cors=$2 ;; esac; shift
done
[ -e "$FAKE/aws-broken" ] && { echo "Could not connect to the endpoint URL" >&2; exit 255; }
if [ "$op" = get-bucket-cors ] || [ "$op" = put-bucket-cors ]; then
  [ -e "$FAKE/cors-denied" ] && { echo "An error occurred (AccessDenied)" >&2; exit 254; }
  if [ "$op" = put-bucket-cors ]; then printf '%s' "$cors" > "$FAKE/cors.json"; exit 0; fi
  [ -e "$FAKE/cors.json" ] && { cat "$FAKE/cors.json"; exit 0; }
  echo "An error occurred (NoSuchCORSConfiguration)" >&2; exit 254
fi
f="$FAKE/params$name"
if [ -e "$f" ]; then cat "$f"; else
  echo "An error occurred (ParameterNotFound) when calling the GetParameter operation:" >&2; exit 254
fi
"""
FAKE_SYSTEMCTL = """#!/bin/bash
echo "$*" >> "$FAKE/systemctl.log"
if [ "$1" = is-active ]; then [ -e "$FAKE/backend-active" ]; exit; fi
if [ "$1" = show ]; then [ -e "$FAKE/env-$2" ] && cat "$FAKE/env-$2"; exit 0; fi
exit 0
"""
FAKE_CURL = """#!/bin/bash
[ -e "$FAKE/imds-region" ] || exit 7
case "$*" in *api/token*) echo token ;; *placement/region*) cat "$FAKE/imds-region" ;; esac
"""


def _rewrite(text: str, root: Path) -> str:
    text = text.replace('if [ "$(id -u)" -ne 0 ]; then', "if false; then")
    for old in ("/etc/aipds-deploy.env", "/usr/local/libexec/aipds", "/etc/systemd/system",
                "/etc/aipds", "/etc/sudoers.d", "/etc/nginx"):
        text = text.replace(old, str(root) + old)
    return text


@pytest.fixture
def host(tmp_path):
    root = tmp_path / "root"
    fake = tmp_path / "fake"
    bin_dir = fake / "bin"
    for d in (bin_dir, root / "usr/local/libexec/aipds", root / "etc/systemd/system",
              root / "etc/aipds", fake / "params/aipds"):
        d.mkdir(parents=True, exist_ok=True)
    harden = root / "usr/local/libexec/aipds/harden"
    harden.write_text(_rewrite((SCRIPTS / "aipds-harden").read_text(), root))
    preview = root / "usr/local/libexec/aipds/preview-configure"
    preview.write_text(_rewrite((SCRIPTS / "aipds-preview-configure").read_text(), root))
    for path, body in ((bin_dir / "aws", FAKE_AWS), (bin_dir / "systemctl", FAKE_SYSTEMCTL),
                       (bin_dir / "curl", FAKE_CURL), (bin_dir / "sleep", "#!/bin/sh\n")):
        path.write_text(body)
    for path in (harden, preview, *bin_dir.iterdir()):
        path.chmod(0o755)
    (fake / "imds-region").write_text("ap-northeast-2")
    (fake / "backend-active").touch()

    class Host:
        dropin = root / "etc/systemd/system/aipds-backend.service.d"
        front_dropin = root / "etc/systemd/system/aipds-frontend.service.d"
        conf = root / "etc/aipds"
        libexec = root / "usr/local/libexec/aipds"

        def param(self, name, value):
            (fake / "params" / name.lstrip("/")).write_text(value)

        fake_dir = fake

        def unit_env(self, unit, text):
            (fake / f"env-{unit}").write_text(text + "\n")

        def cors(self):
            path = fake / "cors.json"
            return json.loads(path.read_text()) if path.exists() else None

        def flag(self, name, on=True):
            (fake / name).touch() if on else (fake / name).unlink(missing_ok=True)

        def run(self, *args):
            env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "FAKE": str(fake)}
            proc = subprocess.run(["bash", str(harden), *args], env=env,
                                  capture_output=True, text=True, timeout=30)
            log = fake / "systemctl.log"
            calls = log.read_text().splitlines() if log.exists() else []
            log.unlink(missing_ok=True)
            return proc, calls

    return Host()


def _restarts(calls):
    return [c for c in calls if c == "restart aipds-backend"]


def test_nothing_to_apply_changes_nothing_and_does_not_fail(host):
    """The stack does not exist yet — right after a new instance first boots together with
    HostingStack."""
    proc, calls = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert not (host.dropin / "launcher.conf").exists()
    assert not (host.dropin / "preview.conf").exists()
    assert _restarts(calls) == []
    assert "no /aipds/agent-role-arn" in proc.stderr


def test_values_that_appear_later_are_applied_once(host):
    host.param("/aipds/agent-role-arn", ARN)
    host.param("/aipds/preview-origin", ORIGIN)
    host.param("/aipds/preview-secret-arn", SECRET)

    proc, calls = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert f"AIPDS_AGENT_ROLE_ARN={ARN}" in (host.dropin / "launcher.conf").read_text()
    assert "AIPDS_LAUNCHER=1" in (host.dropin / "launcher.conf").read_text()
    assert (host.conf / "launch.conf").read_text() == "imds_block=1\n"
    assert f"AIPDS_PREVIEW_ORIGIN={ORIGIN}" in (host.dropin / "preview.conf").read_text()
    assert _restarts(calls) == ["restart aipds-backend"]

    # The same value 2 minutes later — no restart (it would cut off in-progress turns and builds).
    proc, calls = host.run("sync")
    assert proc.returncode == 0 and "no change" in proc.stdout
    assert _restarts(calls) == []


def test_a_stopped_backend_is_not_started(host):
    """boot runs before the services start — it must not bring up a backend that is not
    running via restart."""
    host.flag("backend-active", False)
    host.param("/aipds/agent-role-arn", ARN)
    proc, calls = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert (host.dropin / "launcher.conf").exists()
    assert _restarts(calls) == []


def test_unreadable_parameters_do_not_turn_anything_off(host):
    host.param("/aipds/agent-role-arn", ARN)
    host.run("sync")
    before = (host.dropin / "launcher.conf").read_text()

    host.flag("aws-broken")
    proc, calls = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert "cannot read /aipds/agent-role-arn" in proc.stderr
    assert (host.dropin / "launcher.conf").read_text() == before
    assert (host.conf / "launch.conf").read_text() == "imds_block=1\n"
    assert _restarts(calls) == []


def test_no_region_changes_nothing(host):
    host.flag("imds-region", False)
    host.param("/aipds/agent-role-arn", ARN)
    proc, _ = host.run("sync")
    assert proc.returncode == 0 and "no region" in proc.stderr
    assert not (host.dropin / "launcher.conf").exists()


def test_a_held_instance_is_left_alone(host):
    """`disable` and `imds allow` are the operator's choice — if the timer reverted them 2
    minutes later, rollback would not work."""
    host.param("/aipds/agent-role-arn", ARN)
    host.conf.mkdir(parents=True, exist_ok=True)
    (host.conf / "hold").write_text("disable at 2026-09-24T00:00:00Z\n")
    proc, calls = host.run("sync")
    assert proc.returncode == 0 and "held" in proc.stdout
    assert not (host.dropin / "launcher.conf").exists()
    assert _restarts(calls) == []


def test_disable_holds_and_enable_releases(host):
    host.param("/aipds/agent-role-arn", ARN)
    (host.libexec / "launch").write_text("#!/bin/sh\n")  # enable checks whether install finished
    (host.libexec / "launch").chmod(0o755)

    proc, _ = host.run("disable")
    assert proc.returncode == 0, proc.stderr
    assert (host.conf / "hold").read_text().startswith("disable at ")
    assert host.run("sync")[0].stdout.strip().endswith("releases it)")

    proc, _ = host.run("enable", ARN)
    assert proc.returncode == 0, proc.stderr
    assert not (host.conf / "hold").exists()


def test_a_rejected_preview_value_does_not_fail_the_run(host):
    """boot is inside user-data (set -e) — ending in failure here halts boot and the whole app
    is a 502."""
    host.param("/aipds/agent-role-arn", ARN)
    host.param("/aipds/preview-origin", "http://not-cloudfront.example.com")
    host.param("/aipds/preview-secret-arn", SECRET)
    proc, _ = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert "preview parameters rejected" in proc.stderr
    assert (host.dropin / "launcher.conf").exists()
    assert not (host.dropin / "preview.conf").exists()


# ---- Bucket CORS (direct browser upload for import, infra/lib/aipds-upload-cors-stack.ts) ----

APP = "https://d2example.cloudfront.net"
DRILL_RULE = {"AllowedHeaders": ["content-type"], "AllowedMethods": ["PUT"],
              "AllowedOrigins": ["http://localhost:3000"], "ExposeHeaders": ["ETag"],
              "MaxAgeSeconds": 3000}


@pytest.fixture
def cors_host(host):
    host.unit_env("aipds-backend", "HOME=/opt/aipds AIPDS_S3_BUCKET=artifacts-bkt AWS_REGION=ap-northeast-2")
    host.unit_env("aipds-frontend", f"NODE_ENV=production APP_BASE_URL={APP}/")
    return host


def test_the_app_origin_is_added_to_the_drill_rule(cors_host):
    """DrillStack deployed without AIPDS_UPLOAD_ORIGINS (localhost only) — this is what the
    first deploy of a new environment looks like."""
    (cors_host.fake_dir / "cors.json").write_text(json.dumps({"CORSRules": [DRILL_RULE]}))
    proc, calls = cors_host.run("sync")
    assert proc.returncode == 0, proc.stderr
    rules = cors_host.cors()["CORSRules"]
    assert rules == [{**DRILL_RULE, "AllowedOrigins": ["http://localhost:3000", APP]}]
    assert f"added {APP}" in proc.stdout
    # CORS is not a backend setting — no restart.
    assert _restarts(calls) == []

    before = cors_host.cors()
    proc, _ = cors_host.run("sync")
    assert "added" not in proc.stdout and cors_host.cors() == before


def test_a_bucket_without_cors_gets_the_drill_shaped_rule(cors_host):
    proc, _ = cors_host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert cors_host.cors() == {"CORSRules": [{**DRILL_RULE, "AllowedOrigins": [APP]}]}


def test_without_the_permission_nothing_fails(cors_host):
    """Before AipdsUploadCorsStack — only warns, and the rest of the sync (wrapper, preview)
    proceeds as is."""
    cors_host.flag("cors-denied")
    cors_host.param("/aipds/agent-role-arn", ARN)
    proc, _ = cors_host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert "AipdsUploadCorsStack" in proc.stderr
    assert (cors_host.dropin / "launcher.conf").exists()


def test_a_held_instance_still_keeps_uploads_working(cors_host):
    """hold is the sandbox rollback — it has nothing to do with import uploads."""
    cors_host.conf.mkdir(parents=True, exist_ok=True)
    (cors_host.conf / "hold").write_text("disable at 2026-09-24T00:00:00Z\n")
    proc, _ = cors_host.run("sync")
    assert proc.returncode == 0 and "held" in proc.stdout
    assert APP in cors_host.cors()["CORSRules"][0]["AllowedOrigins"]


def test_a_non_https_app_origin_is_not_written(cors_host):
    cors_host.unit_env("aipds-frontend", "APP_BASE_URL=http://example.com")
    proc, _ = cors_host.run("sync")
    assert proc.returncode == 0 and "not https" in proc.stderr
    assert cors_host.cors() is None


# ---- App origin (the frontend's APP_BASE_URL, HostingStack's /aipds/app-origin) ----


def _frontend_restarts(calls):
    return [c for c in calls if c == "restart aipds-frontend"]


def test_the_app_origin_that_appears_later_is_applied_once(host):
    """A new instance boots before its distribution exists — the first syncs find nothing, and
    the value shows up minutes later."""
    proc, calls = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert not (host.front_dropin / "app-origin.conf").exists()

    host.param("/aipds/app-origin", APP)
    proc, calls = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert (host.front_dropin / "app-origin.conf").read_text() == (
        f"[Service]\nEnvironment=APP_BASE_URL={APP}\n")
    assert _frontend_restarts(calls) == ["restart aipds-frontend"]
    # Not a backend setting — in-flight turns and builds are left alone.
    assert _restarts(calls) == []

    proc, calls = host.run("sync")
    assert proc.returncode == 0 and _frontend_restarts(calls) == []


def test_a_stopped_frontend_is_not_started(host):
    host.flag("backend-active", on=False)
    host.param("/aipds/app-origin", APP)
    proc, calls = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert (host.front_dropin / "app-origin.conf").exists()
    assert _frontend_restarts(calls) == []


@pytest.mark.parametrize("value", [
    "http://d2example.cloudfront.net",
    "https://d2example.cloudfront.net/",
    "https://d2example.cloudfront.net\nEnvironment=LD_PRELOAD=/tmp/evil.so",
])
def test_anything_but_one_https_origin_is_not_written(host, value):
    """The value lands in a unit file the frontend runs with — a second line would be a second
    setting."""
    host.param("/aipds/app-origin", value)
    proc, calls = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert "not an https origin" in proc.stderr
    assert not (host.front_dropin / "app-origin.conf").exists()
    assert _frontend_restarts(calls) == []


def test_a_held_instance_still_gets_its_app_origin(host):
    """hold is the sandbox rollback — login has nothing to do with it."""
    host.conf.mkdir(parents=True, exist_ok=True)
    (host.conf / "hold").write_text("disable at 2026-09-24T00:00:00Z\n")
    host.param("/aipds/app-origin", APP)
    proc, _ = host.run("sync")
    assert proc.returncode == 0 and "held" in proc.stdout
    assert APP in (host.front_dropin / "app-origin.conf").read_text()
