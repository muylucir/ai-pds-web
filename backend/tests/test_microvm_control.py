# backend/tests/test_microvm_control.py
from pathfinder.sandbox.microvm_control import BootSpec, FakeMicroVMController

def test_bootspec_env_has_bedrock_flag_and_region():
    spec = BootSpec(region="ap-northeast-1")
    env = spec.env()
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["AWS_REGION"] == "ap-northeast-1"

def test_bootspec_omits_model_until_injected():
    # anthropic_model comes from env at deploy; confirmed id is
    # "global.anthropic.claude-sonnet-5" (ap-northeast-1, ACTIVE 2026-07-17).
    assert "ANTHROPIC_MODEL" not in BootSpec().env()
    assert BootSpec(anthropic_model="global.anthropic.claude-sonnet-5").env()["ANTHROPIC_MODEL"] \
        == "global.anthropic.claude-sonnet-5"

def test_bootspec_idle_policy_matches_run_microvm_api():
    # Exact shape run-microvm --idle-policy expects (Lambda MicroVMs API).
    p = BootSpec(max_idle_seconds=300, suspended_duration_seconds=1800, auto_resume=True).idle_policy()
    assert p == {"maxIdleDurationSeconds": 300, "suspendedDurationSeconds": 1800, "autoResumeEnabled": True}

def test_bootspec_env_has_no_credential_material():
    # IAM-role auth only — no static keys of any shape may appear in the env.
    env = BootSpec(anthropic_model="global.anthropic.claude-sonnet-5").env()
    joined = " ".join(f"{k}={v}" for k, v in env.items())
    for marker in ("AKIA", "sk-", "bedrock-api-key-", "AWS_BEARER_TOKEN", "AWS_SECRET"):
        assert marker not in joined

async def test_fake_controller_boot_ready():
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    handle = await ctrl.boot("proj-1", BootSpec())
    assert handle.status == "ready"
    assert handle.base_url == "http://fake-vm"
    assert ctrl.boot_calls == 1

async def test_fake_controller_suspend_resume_stop():
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    handle = await ctrl.boot("proj-1", BootSpec())
    await ctrl.suspend(handle)
    assert await ctrl.status(handle) == "suspended"
    handle = await ctrl.resume(handle)
    assert handle.status == "ready"
    await ctrl.stop(handle)
    assert await ctrl.status(handle) == "stopped"

# backend/tests/test_microvm_control.py
async def test_simulate_auto_suspend_reports_suspended():
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    handle = await ctrl.boot("p1", BootSpec())
    ctrl.simulate_auto_suspend(handle)
    assert await ctrl.status(handle) == "suspended"   # even though handle.status == "ready"
    assert handle.status == "ready"                   # the cached copy is stale by design

async def test_simulate_expiry_reports_expired():
    ctrl = FakeMicroVMController(base_url="http://fake-vm")
    handle = await ctrl.boot("p1", BootSpec())
    ctrl.simulate_expiry(handle)
    assert await ctrl.status(handle) == "expired"
