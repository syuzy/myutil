import asyncio
import json

import pytest
from playwright.async_api import Error

from app.context_export import export_context
from app.recorder import Recorder
from app.redaction import Config


def events(path):
    return [
        json.loads(line) for line in (path / "events.jsonl").read_text().splitlines()
    ]


async def wait_for(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


async def test_payment_flow(tmp_path, demo_server, chromium_path):
    recorder = Recorder(
        tmp_path,
        Config(extra_headers=["X-Private"], redact_selectors=["#private", ".private"]),
    )
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        case = recorder.start_annotation(
            "Payment Case",
            goal="Pay for an order",
            expected_result="Payment accepted",
            tags=["checkout"],
        )
        await page.get_by_label("Full name").fill("Jane Doe")
        await page.get_by_label("Password").fill("TYPED_PASSWORD")
        await page.get_by_label("Card number").fill("4111111111111111")
        await page.get_by_label("CVC").fill("987")
        await page.get_by_label("Private note").fill("TYPED_PRIVATE")
        await (
            page.frame_locator("iframe")
            .get_by_role("button", name="Choose plan")
            .click()
        )
        await page.get_by_role("button", name="Pay now").click()
        await page.get_by_role("status").filter(has_text="Payment accepted").wait_for()
        await page.evaluate("fetch('/api/large').then(r => r.json())")
        await page.evaluate("fetch('/api/binary').then(r => r.text())")
        async with page.expect_popup() as popup_info:
            await page.get_by_role("link", name="Open receipt").click()
        popup = await popup_info.value
        await popup.wait_for_load_state()
        await popup.close()
        await recorder.checkpoint()
        recorder.on_sensor(
            {"page": page, "frame": page.main_frame},
            {"type": "dom", "payload": {"data": None}},
        )
        await wait_for(lambda: recorder.store.counts["network.body"] >= 2)
        await recorder.end_annotation()
    finally:
        await recorder.stop()
    path = recorder.store.path
    recorded = events(path)
    types = {e["type"] for e in recorded}
    assert {
        "page.created",
        "page.closed",
        "navigation",
        "network.redirect",
        "network.request",
        "network.response",
        "network.finished",
        "action.click",
        "action.input",
        "action.submit",
        "frame.attached",
        "page.popup",
        "console.error",
        "page.error",
        "annotation.start",
        "annotation.end",
        "checkpoint",
        "dom",
        "location",
    } <= types
    assert not [e for e in recorded if e["type"] == "sensor.error"]
    assert any(e["payload"]["rrweb_type"] == 2 for e in recorded if e["type"] == "dom")
    assert any(e["payload"]["rrweb_type"] == 3 for e in recorded if e["type"] == "dom")
    assert [e["seq"] for e in recorded] == list(range(1, len(recorded) + 1))
    clicks = [
        e
        for e in recorded
        if e["type"] == "action.click"
        and e["payload"]["locator"]["accessible_name"] == "Pay now"
    ]
    assert clicks[0]["annotation_id"] == case.id
    assert clicks[0]["payload"]["locator"]["role"] == "button"
    assert clicks[0]["payload"]["locator"]["test_id"] == "pay"
    frame_click = next(
        e
        for e in recorded
        if e["type"] == "action.click"
        and e["payload"]["locator"]["accessible_name"] == "Choose plan"
    )
    assert frame_click["frame_id"] != clicks[0]["frame_id"]
    assert frame_click["payload"]["locator"]["frame_path"]
    responses = [
        e
        for e in recorded
        if e["type"] == "network.response" and e["payload"]["url"].endswith("/api/pay")
    ]
    assert responses[0]["payload"]["status"] == 200
    assert any(
        e["payload"]["timing"]["responseEnd"] >= 0
        for e in recorded
        if e["type"] == "network.finished"
    )
    bodies = [e["payload"] for e in recorded if e["type"] == "network.body"]
    assert any("body_artifact" in b for b in bodies)
    context = json.loads((path / "derived/llm-context.json").read_text())
    annotation = context["annotations"][0]
    assert annotation["name"] == "Payment Case"
    assert any(
        item.get("locator_candidates", [{}])[0].get("by") == "role"
        for item in annotation["timeline"]
    )
    assert "Payment accepted" in json.dumps(annotation["final_state"])
    assert "Jane Doe" in json.dumps(annotation)
    assert (path / "derived/llm-context.json").stat().st_size < sum(
        p.stat().st_size for p in path.rglob("*.json*") if "derived" not in p.parts
    )
    all_data = "\n".join(p.read_text() for p in path.rglob("*.json*"))
    for value in [
        "INITIAL_PASSWORD",
        "TYPED_PASSWORD",
        "4111111111111111",
        '"987"',
        "PRIVATE_VALUE",
        "PRIVATE_TEXT",
        "TYPED_PRIVATE",
        "AUTH_SECRET",
        "HEADER_SECRET",
        "RESPONSE_SECRET",
        "COOKIE_SECRET",
        "BODY_SECRET",
        "BINARY_SECRET",
    ]:
        assert value not in all_data, f"Leaked {value}"
    before = (path / "derived/llm-context.json").read_bytes()
    export_context(path)
    assert (path / "derived/llm-context.json").read_bytes() == before


async def test_last_page_close_finalizes_and_restart(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(tmp_path)
    page = await recorder.start(
        demo_server, headless=True, executable_path=chromium_path
    )
    recorder.start_annotation("Auto close")
    first = recorder.store.path
    await page.close()
    await wait_for(lambda: recorder.store.closed and not recorder.stopping)
    assert (first / "derived/llm-context.json").exists()
    assert json.loads((first / "annotations.json").read_text())[0]["end_seq"]
    try:
        await recorder.start(demo_server, headless=True, executable_path=chromium_path)
        assert recorder.store.path != first
    finally:
        await recorder.stop()


async def test_failed_start_finalizes(tmp_path):
    recorder = Recorder(tmp_path)
    with pytest.raises(Error):
        await recorder.start(headless=True, executable_path="/not/a/browser")
    assert recorder.store.closed
    assert events(recorder.store.path)[-1]["payload"]["reason"] == "startup failed"


async def test_bad_annotation_and_partial_export(tmp_path, demo_server, chromium_path):
    recorder = Recorder(tmp_path)
    with pytest.raises(RuntimeError):
        recorder.start_annotation("before start")
    try:
        await recorder.start(demo_server, headless=True, executable_path=chromium_path)
        with pytest.raises(ValueError):
            recorder.start_annotation(" ")
        recorder.start_annotation("One")
        with pytest.raises(RuntimeError):
            recorder.start_annotation("Two")
    finally:
        await recorder.stop()
    with (recorder.store.path / "events.jsonl").open("a") as stream:
        stream.write("{broken")
    path = export_context(recorder.store.path)
    assert json.loads(path.read_text())["incomplete_lines_skipped"] == 1
