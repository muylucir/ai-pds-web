# backend/tests/test_harden_script.py — infra/scripts/aipds-harden의 `sync`(스택이 SSM에 남긴 값 반영).
#
# sync는 타이머가 root로 2분마다 돌리고, 새 인스턴스에서는 user-data(set -e) 안에서 돈다. 그래서
# "무엇을 바꾸지 않는가"가 사양의 절반이다: 값이 없거나 못 읽으면 끄지 않고, 운영자가 멈춰 둔
# 상태(hold)를 되돌리지 않고, 바뀐 것이 없으면 재시작하지 않고, 어떤 경우에도 실패로 끝나지 않는다.
#
# 스크립트를 임시 디렉터리로 복사하면서 절대 경로(/etc, /usr/local/libexec)를 그 안으로 돌리고,
# aws·systemctl·curl·sleep을 가짜로 둔다. 실제 파일을 쓰고 읽는 셸 동작 자체를 본다 — 문자열 검사로는
# `set -e` 아래에서 없는 파일 하나가 부팅을 멈추는 종류의 결함을 못 잡는다(실제로 한 번 그랬다).
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
    """스택이 아직 없다 — 새 인스턴스가 HostingStack과 함께 먼저 부팅한 직후."""
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

    # 2분 뒤 같은 값 — 재시작하지 않는다(진행 중인 턴과 빌드가 끊긴다).
    proc, calls = host.run("sync")
    assert proc.returncode == 0 and "no change" in proc.stdout
    assert _restarts(calls) == []


def test_a_stopped_backend_is_not_started(host):
    """boot는 서비스 기동 전에 돈다 — 떠 있지 않은 백엔드를 restart로 먼저 띄우면 안 된다."""
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
    """`disable`·`imds allow`는 운영자의 선택이다 — 2분 뒤 타이머가 되돌리면 롤백이 안 된다."""
    host.param("/aipds/agent-role-arn", ARN)
    host.conf.mkdir(parents=True, exist_ok=True)
    (host.conf / "hold").write_text("disable at 2026-09-24T00:00:00Z\n")
    proc, calls = host.run("sync")
    assert proc.returncode == 0 and "held" in proc.stdout
    assert not (host.dropin / "launcher.conf").exists()
    assert _restarts(calls) == []


def test_disable_holds_and_enable_releases(host):
    host.param("/aipds/agent-role-arn", ARN)
    (host.libexec / "launch").write_text("#!/bin/sh\n")  # enable은 install이 끝났는지 본다
    (host.libexec / "launch").chmod(0o755)

    proc, _ = host.run("disable")
    assert proc.returncode == 0, proc.stderr
    assert (host.conf / "hold").read_text().startswith("disable at ")
    assert host.run("sync")[0].stdout.strip().endswith("releases it)")

    proc, _ = host.run("enable", ARN)
    assert proc.returncode == 0, proc.stderr
    assert not (host.conf / "hold").exists()


def test_a_rejected_preview_value_does_not_fail_the_run(host):
    """boot는 user-data(set -e) 안이다 — 여기서 실패로 끝나면 부팅이 멈추고 앱 전체가 502다."""
    host.param("/aipds/agent-role-arn", ARN)
    host.param("/aipds/preview-origin", "http://not-cloudfront.example.com")
    host.param("/aipds/preview-secret-arn", SECRET)
    proc, _ = host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert "preview parameters rejected" in proc.stderr
    assert (host.dropin / "launcher.conf").exists()
    assert not (host.dropin / "preview.conf").exists()


# ---- 버킷 CORS(프로젝트 가져오기의 브라우저 직접 업로드, infra/lib/aipds-upload-cors-stack.ts) ----

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
    """DrillStack을 AIPDS_UPLOAD_ORIGINS 없이 배포한 상태(localhost만) — 새 환경의 첫 배포가 이렇다."""
    (cors_host.fake_dir / "cors.json").write_text(json.dumps({"CORSRules": [DRILL_RULE]}))
    proc, calls = cors_host.run("sync")
    assert proc.returncode == 0, proc.stderr
    rules = cors_host.cors()["CORSRules"]
    assert rules == [{**DRILL_RULE, "AllowedOrigins": ["http://localhost:3000", APP]}]
    assert f"added {APP}" in proc.stdout
    # CORS는 백엔드 설정이 아니다 — 재시작하지 않는다.
    assert _restarts(calls) == []

    before = cors_host.cors()
    proc, _ = cors_host.run("sync")
    assert "added" not in proc.stdout and cors_host.cors() == before


def test_a_bucket_without_cors_gets_the_drill_shaped_rule(cors_host):
    proc, _ = cors_host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert cors_host.cors() == {"CORSRules": [{**DRILL_RULE, "AllowedOrigins": [APP]}]}


def test_without_the_permission_nothing_fails(cors_host):
    """AipdsUploadCorsStack 전 — 경고만 하고, 나머지 동기화(래퍼·프리뷰)는 그대로 한다."""
    cors_host.flag("cors-denied")
    cors_host.param("/aipds/agent-role-arn", ARN)
    proc, _ = cors_host.run("sync")
    assert proc.returncode == 0, proc.stderr
    assert "AipdsUploadCorsStack" in proc.stderr
    assert (cors_host.dropin / "launcher.conf").exists()


def test_a_held_instance_still_keeps_uploads_working(cors_host):
    """hold는 샌드박스 롤백이다 — 가져오기 업로드와는 무관하다."""
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
