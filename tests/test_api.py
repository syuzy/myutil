import asyncio
import json

from app.config import Config
from app.recorder import Recorder


async def test_chunked_compressed_and_headerless_json_are_captured(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(
        tmp_path, Config(api_allowlist=[demo_server + "/api/*"], max_body_bytes=200)
    )
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        for endpoint in ["chunked", "gzip", "gzip-large", "no-type"]:
            await page.evaluate(
                "(path)=>fetch(path).then(r=>r.json())", "/api/" + endpoint
            )
        async with asyncio.timeout(5):
            while recorder.store.counts["api"] < 4:
                await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
    recorded = json.loads(recorder.store.path.read_text())
    results = {
        e["payload"]["url"].rsplit("/", 1)[-1]: e["payload"]["response_body"]
        for e in recorded["events"]
        if e["type"] == "api"
    }
    assert results["chunked"]["data"]["result"] == "Chunked ready"
    assert results["gzip"]["data"]["result"] == "Compressed ready"
    assert results["no-type"]["data"]["result"] == "Chunked ready"
    assert results["gzip-large"]["omitted"] == "size limit"
    assert results["gzip-large"]["bytes"] > results["gzip-large"]["limit"] == 200
    assert results["chunked"]["bytes"] == len(
        json.dumps({"result": "Chunked ready", "token": "CHUNK_SECRET"}).encode()
    )
    assert "CHUNK_SECRET" not in recorder.store.path.read_text()


async def test_request_json_does_not_require_content_length(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(tmp_path, Config(api_allowlist=[demo_server + "/api/pay"]))
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await page.evaluate(
            """()=>fetch('/api/pay',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Jane',password:'REQUEST_SECRET'})}).then(r=>r.json())"""
        )
        async with asyncio.timeout(5):
            while recorder.store.counts["api"] < 1:
                await asyncio.sleep(0.02)
    finally:
        await recorder.stop()
    api = next(
        e["payload"]
        for e in json.loads(recorder.store.path.read_text())["events"]
        if e["type"] == "api"
    )
    assert api["request_body"]["data"]["name"] == "Jane"
    assert api["request_body"]["data"]["password"] == "[REDACTED]"
    assert api["request_body"]["bytes"] > 0
    assert "REQUEST_SECRET" not in recorder.store.path.read_text()
