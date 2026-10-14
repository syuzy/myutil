import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from playwright.async_api import Error

from app.config import Config
from app.recorder import Recorder
from app.storage import SessionStore


async def test_body_failure_and_unknown_size(tmp_path):
    recorder = Recorder(tmp_path)
    recorder.store = SessionStore(tmp_path, Config())
    response = SimpleNamespace(
        status=200,
        headers={"content-type": "application/json", "content-length": "20"},
        body=AsyncMock(side_effect=Error("secret text must not be logged")),
    )
    request = SimpleNamespace(
        response=AsyncMock(return_value=response), timing={"responseEnd": 12}
    )
    await recorder.capture_api(
        request, ({"url": "https://example.com/api"}, None, "original-annotation")
    )
    response.headers.pop("content-length")
    await recorder.capture_api(
        request, ({"url": "https://example.com/api"}, None, None)
    )
    assert response.body.await_count == 1
    recorder.on_sensor({}, {"type": "unexpected"})
    await recorder.stop()
    data = json.loads(recorder.store.path.read_text())
    api = [e for e in data["events"] if e["type"] == "api"]
    assert api[0]["annotation_id"] == "original-annotation"
    assert api[0]["payload"]["response_body"]["omitted"] == "read failed or timed out"
    assert "secret text" not in recorder.store.path.read_text()


def test_allowlist_matches_only_api_path_and_method():
    recorder = Recorder(
        config=Config(
            api_allowlist=["https://example.com/api/orders/*"], api_methods=["post"]
        )
    )

    def request(url, resource_type="fetch", method="POST"):
        return SimpleNamespace(url=url, resource_type=resource_type, method=method)

    assert recorder.matches_api(
        request("https://example.com/api/orders/1?token=secret")
    )
    assert not recorder.matches_api(
        request("https://analytics.example.com/api/orders/1")
    )
    assert not recorder.matches_api(
        request("https://example.com/api/orders/1", method="GET")
    )
    assert not recorder.matches_api(
        request("https://example.com/api/orders/1", resource_type="image")
    )
    assert not recorder.matches_api(request("https://example.com/analytics"))
