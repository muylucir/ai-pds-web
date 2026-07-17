from pathlib import Path
from pathfinder.sandbox.local import LocalSandbox
from sandbox_contract import run_sandbox_contract

async def test_local_sandbox_satisfies_contract(tmp_path: Path):
    sb = LocalSandbox(root=tmp_path)
    await sb.start()
    await run_sandbox_contract(sb)
