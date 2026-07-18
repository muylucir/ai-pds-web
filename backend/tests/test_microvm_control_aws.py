import datetime

import boto3
import pytest
from botocore.stub import Stubber, ANY
from pathfinder.sandbox.microvm_control import BootSpec
from pathfinder.sandbox.microvm_control_aws import (
    LambdaMicroVMController, mint_harness_token, _map_status,
)

REGION = "ap-northeast-1"


def _client():
    return boto3.client("lambda-microvms", region_name=REGION)


def _vm_resp(state, microvm_id="vm-1", endpoint="https://vm-1.microvm.aws"):
    """Build a full GetMicrovmResponse/RunMicrovmResponse body. The real
    service model (lambda-microvms 2025-09-09) requires microvmId, state,
    endpoint, imageArn, imageVersion, maximumDurationInSeconds, startedAt --
    NOT the nested {"microvm": {"status": ...}} shape this plan originally
    guessed. Corrected here per the Stubber's ParamValidationError."""
    return {
        "microvmId": microvm_id,
        "state": state,
        "endpoint": endpoint,
        "imageArn": "arn:aws:lambda-microvms:ap-northeast-1:123456789012:image/img-1",
        "imageVersion": "1",
        "maximumDurationInSeconds": 28800,
        "startedAt": datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc),
    }


def test_map_status_table():
    assert _map_status("PENDING") == "booting"
    assert _map_status("STARTING") == "booting"
    assert _map_status("RUNNING") == "ready"
    assert _map_status("SUSPENDED") == "suspended"
    assert _map_status("TERMINATED") == "expired"
    assert _map_status("EXPIRED") == "expired"
    assert _map_status("WAT") == "stopped"


def test_boto3_floor_ships_lambda_microvms_model():
    """Executable floor check for the pyproject.toml boto3 pin. Confirmed by
    binary search that botocore==1.43.34 lacks the lambda-microvms service
    model entirely (boto3.client(...) raises UnknownServiceError) while
    1.43.35 has it -- this is the exact first version, not a guess. This test
    fails loudly (UnknownServiceError, not silently) in any environment whose
    resolved boto3/botocore predates that floor, catching a `>=` pin that
    resolves to a too-old install before the Stubber tests below even run."""
    assert "lambda-microvms" in boto3.session.Session().get_available_services()
    client = boto3.client("lambda-microvms", region_name=REGION)
    assert client.meta.service_model.service_id == "Lambda Microvms"


ROLE_ARN = "arn:aws:iam::123456789012:role/microvm-exec-role"


async def test_boot_sends_spec_params_and_polls_until_running():
    client = _client()
    stub = Stubber(client)
    spec = BootSpec(image_id="img-arn", exec_role_arn=ROLE_ARN,
                    anthropic_model="global.anthropic.claude-sonnet-5")
    # The REAL run/get-microvm return a BARE host with no scheme (confirmed
    # against a live boot: "<id>.lambda-microvm.<region>.on.aws"). The
    # controller must prepend https:// so HarnessClient's "{base_url}/message"
    # is a usable URL.
    bare = "vm-1.lambda-microvm.ap-northeast-1.on.aws"
    stub.add_response(
        "run_microvm",
        _vm_resp("PENDING", endpoint=bare),
        {"imageIdentifier": "img-arn", "executionRoleArn": ROLE_ARN,
         "idlePolicy": {"maxIdleDurationSeconds": 300,
                        "suspendedDurationSeconds": 1800,
                        "autoResumeEnabled": True}},
    )
    stub.add_response("get_microvm", _vm_resp("PENDING", endpoint=bare),
                      {"microvmIdentifier": "vm-1"})
    stub.add_response("get_microvm", _vm_resp("RUNNING", endpoint=bare),
                      {"microvmIdentifier": "vm-1"})
    ctrl = LambdaMicroVMController(region=REGION, client=client, poll_interval_seconds=0)
    with stub:
        handle = await ctrl.boot("proj-1", spec)
    assert handle.vm_id == "vm-1"
    assert handle.base_url == f"https://{bare}"  # scheme prepended
    assert handle.status == "ready"
    stub.assert_no_pending_responses()


def test_endpoint_url_prepends_https_only_when_missing():
    f = LambdaMicroVMController._endpoint_url
    assert f("x.lambda-microvm.ap-northeast-1.on.aws") == "https://x.lambda-microvm.ap-northeast-1.on.aws"
    assert f("https://already.example") == "https://already.example"
    assert f("http://plain.example") == "http://plain.example"


async def test_boot_times_out_raises_runtimeerror():
    client = _client()
    stub = Stubber(client)
    spec = BootSpec(image_id="img", exec_role_arn=ROLE_ARN)
    stub.add_response("run_microvm", _vm_resp("PENDING", microvm_id="vm-2", endpoint="https://x"),
                      {"imageIdentifier": "img", "executionRoleArn": ROLE_ARN,
                       "idlePolicy": ANY})
    # Every poll returns PENDING; with a 0s timeout the first check fails fast.
    stub.add_response("get_microvm", _vm_resp("PENDING", microvm_id="vm-2", endpoint="https://x"),
                      {"microvmIdentifier": "vm-2"})
    ctrl = LambdaMicroVMController(region=REGION, client=client,
                                   boot_timeout_seconds=0, poll_interval_seconds=0)
    with stub:
        with pytest.raises(RuntimeError, match="did not reach RUNNING"):
            await ctrl.boot("proj-1", spec)


async def test_status_maps_get_microvm():
    client = _client()
    stub = Stubber(client)
    stub.add_response("get_microvm", _vm_resp("SUSPENDED", microvm_id="vm-9", endpoint="u"),
                      {"microvmIdentifier": "vm-9"})
    ctrl = LambdaMicroVMController(region=REGION, client=client)
    from pathfinder.sandbox.microvm_control import VMHandle
    with stub:
        assert await ctrl.status(VMHandle(vm_id="vm-9", base_url="u", status="ready")) == "suspended"


async def test_suspend_and_stop_call_api():
    client = _client()
    stub = Stubber(client)
    stub.add_response("suspend_microvm", {}, {"microvmIdentifier": "vm-3"})
    stub.add_response("terminate_microvm", {}, {"microvmIdentifier": "vm-3"})
    ctrl = LambdaMicroVMController(region=REGION, client=client)
    from pathfinder.sandbox.microvm_control import VMHandle
    h = VMHandle(vm_id="vm-3", base_url="u", status="ready")
    with stub:
        await ctrl.suspend(h)
        await ctrl.stop(h)
    stub.assert_no_pending_responses()


async def test_resume_polls_until_running():
    client = _client()
    stub = Stubber(client)
    # ResumeMicrovmResponse has NO members in the real service model (it
    # cannot carry "endpoint") -- corrected from the plan's guess. The new
    # endpoint is only obtainable via the subsequent get_microvm poll.
    stub.add_response("resume_microvm", {}, {"microvmIdentifier": "vm-4"})
    stub.add_response("get_microvm", _vm_resp("RUNNING", microvm_id="vm-4", endpoint="https://vm-4.new"),
                      {"microvmIdentifier": "vm-4"})
    ctrl = LambdaMicroVMController(region=REGION, client=client, poll_interval_seconds=0)
    from pathfinder.sandbox.microvm_control import VMHandle
    with stub:
        h = await ctrl.resume(VMHandle(vm_id="vm-4", base_url="old", status="suspended"))
    assert h.status == "ready" and h.base_url == "https://vm-4.new"


async def test_mint_harness_token_returns_proxy_auth_header():
    client = _client()
    stub = Stubber(client)
    # CreateMicrovmAuthTokenResponse.authToken is a map (TokenParts), not a
    # bare "token" string -- corrected from the plan's guess. Per the service
    # docs: "Use the value at key 'X-aws-proxy-auth'" from that map.
    stub.add_response("create_microvm_auth_token", {"authToken": {"X-aws-proxy-auth": "jwe-abc"}},
                       {"microvmIdentifier": "vm-5", "expirationInMinutes": 30,
                        "allowedPorts": [{"port": 8080}]})
    with stub:
        hdr = mint_harness_token("vm-5", region=REGION, client=client)
    assert hdr == {"X-aws-proxy-auth": "jwe-abc"}
