# backend/pathfinder/sandbox/microvm_control_aws.py
from __future__ import annotations
from pathfinder.sandbox.microvm_control import MicroVMController, BootSpec, VMHandle, VMStatus

class LambdaMicroVMController(MicroVMController):
    """AWS Lambda MicroVMs control-plane binding (ap-northeast-1).

    Maps onto the confirmed Lambda MicroVMs API (GA 2026-06-22):
      boot    -> lambda-microvms run-microvm --image-identifier <spec.image_id>
                 --execution-role-arn <spec.exec_role_arn> --idle-policy <spec.idle_policy()>
                 ; response gives microvmId + endpoint (the harness base_url)
      resume  -> lambda-microvms resume-microvm --microvm-identifier <id>
      suspend -> lambda-microvms suspend-microvm --microvm-identifier <id>
      stop    -> lambda-microvms terminate-microvm --microvm-identifier <id>
      status  -> lambda-microvms get-microvm --microvm-identifier <id> (RUNNING/SUSPENDED/...)
    The concrete calls (via aioboto3/boto3-in-executor) + JWE auth-token wiring
    for the harness endpoint are completed and verified in Task 7 against real
    AWS — they cannot run in CI without credentials. Until then these raise
    NotImplementedError; unit tests inject FakeMicroVMController via
    app.microvm_controller_factory instead.
    """

    def __init__(self, region: str = "ap-northeast-1"):
        self.region = region

    async def boot(self, project_id: str, spec: BootSpec) -> VMHandle:
        raise NotImplementedError("Task 7: bind to lambda-microvms run-microvm")

    async def resume(self, handle: VMHandle) -> VMHandle:
        raise NotImplementedError("Task 7: bind to lambda-microvms resume-microvm")

    async def suspend(self, handle: VMHandle) -> None:
        raise NotImplementedError("Task 7: bind to lambda-microvms suspend-microvm")

    async def stop(self, handle: VMHandle) -> None:
        raise NotImplementedError("Task 7: bind to lambda-microvms terminate-microvm")

    async def status(self, handle: VMHandle) -> VMStatus:
        raise NotImplementedError("Task 7: bind to lambda-microvms get-microvm")
