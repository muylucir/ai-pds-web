import json, pytest, httpx
from app import build_app
from tests.fake_driver import FakeDriver


@pytest.fixture
def client(tmp_path):
    driver = FakeDriver()
    app = build_app(driver, str(tmp_path))
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t"), driver, tmp_path


@pytest.mark.asyncio
async def test_message_streams_events(client):
    c, driver, _ = client
    async with c.stream("POST", "/message", json={"text": "build it"}) as r:
        assert r.status_code == 200
        body = "".join([chunk async for chunk in r.aiter_text()])
    assert '"kind":"message"' in body and '"kind":"done"' in body


@pytest.mark.asyncio
async def test_interrupt_returns_202_and_calls_driver(client):
    c, driver, _ = client
    r = await c.post("/interrupt")
    assert r.status_code == 202
    assert driver.interrupts == 1


@pytest.mark.asyncio
async def test_answers_forwards_to_driver(client):
    c, driver, _ = client
    r = await c.post("/answers", json={"interrupt_id": "i1", "answers": {"Q?": "A"}})
    assert r.status_code == 204
    assert driver.answers_calls == [("i1", {"Q?": "A"})]


@pytest.mark.asyncio
async def test_answers_missing_key_400(client):
    c, _, _ = client
    r = await c.post("/answers", json={"answers": {}})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_file_roundtrip_and_escape_rejected(client):
    c, _, ws = client
    r = await c.put("/files/prototype/a.txt", content=b"hello")
    assert r.status_code == 204
    r = await c.get("/files/prototype/a.txt")
    assert r.text == "hello"
    # httpx normalizes literal "../" dot-segments client-side before the
    # request goes out (RFC 3986), so a raw client (curl, or the drill
    # scripts, which talk to the harness directly and bypass
    # MicroVMSandbox's reject_unsafe) is simulated with percent-encoded
    # dots, which travel over the wire untouched and land in
    # request.path_params["path"] as a literal "..".
    r = await c.get("/files/%2e%2e/etc/passwd")
    assert r.status_code == 400
