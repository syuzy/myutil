import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from playwright.async_api import Error

from app.recorder import Recorder
from app.redaction import Config
from app.storage import SessionStore


async def test_body_failure_and_unknown_size_do_not_stop_recording(tmp_path):
    recorder = Recorder(tmp_path)
    recorder.store = SessionStore(tmp_path, Config())
    recorder.request_context = lambda request: {}
    response = SimpleNamespace(
        headers={"content-type": "application/json", "content-length": "20"},
        body=AsyncMock(side_effect=Error("secret content must not be logged")),
    )
    request = SimpleNamespace(
        resource_type="fetch", response=AsyncMock(return_value=response)
    )
    recorder.request_id = lambda request: "request-1"
    await recorder.capture_body(request)
    assert recorder.recording
    event = json.loads(
        (recorder.store.path / "events.jsonl").read_text().splitlines()[-1]
    )
    assert event["payload"]["body_omitted"] == "read failed"
    response.headers.pop("content-length")
    await recorder.capture_body(request)
    assert response.body.await_count == 1
    recorder.on_sensor({}, {"type": "unexpected", "payload": {}})
    recorder.on_sensor({}, {"type": "dom", "payload": {"data": None}})
    assert recorder.store.counts["recorder.warning"] == 2
    await recorder.stop()
    assert recorder.store.closed


async def test_browser_disconnect_finalizes(tmp_path, demo_server, chromium_path):
    import asyncio

    recorder = Recorder(tmp_path)
    await recorder.start(demo_server, headless=True, executable_path=chromium_path)
    recorder.start_annotation("Disconnect")
    await recorder.browser.close()
    async with asyncio.timeout(10):
        while not recorder.store.closed or recorder.stopping:
            await asyncio.sleep(0.02)
    assert (
        json.loads((recorder.store.path / "manifest.json").read_text())["status"]
        == "complete"
    )
    assert (recorder.store.path / "derived/llm-context.json").exists()
