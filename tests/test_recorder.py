import asyncio
import json
import time

import pytest
from playwright.async_api import Error

from app.config import Config
from app.recorder import Recorder


async def wait_for(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


def settings(**kwargs):
    return Config(
        debounce_ms=80,
        min_snapshot_interval_ms=200,
        max_wait_ms=500,
        dom_budget_ms=50,
        **kwargs,
    )


async def test_payment_one_file_allowlist_and_redaction(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(
        tmp_path,
        settings(
            api_allowlist=[demo_server + "/api/pay"],
            capture_headers=True,
            capture_frames=True,
            extra_headers=["X-Private"],
            redact_selectors=["#private", ".private"],
        ),
    )
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await recorder.checkpoint()
        case = recorder.start_annotation(
            "Payment Case", goal="Pay for an order", expected_result="Payment accepted"
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
        await page.evaluate("fetch('/api/large').then(r=>r.json())")
        await page.evaluate("fetch('/api/binary').then(r=>r.text())")
        await wait_for(lambda: recorder.store.counts["api"] == 1)
        await recorder.end_annotation()
    finally:
        await recorder.stop()
    data = json.loads(recorder.store.path.read_text())
    assert list(tmp_path.iterdir()) == [recorder.store.path]
    assert data["annotations"][0]["id"] == case.id
    assert data["annotations"][0]["end_seq"]
    snapshots = [e for e in data["events"] if e["type"] == "html"]
    assert any("Payment accepted" in e["payload"]["html"] for e in snapshots)
    assert any(
        "Selected" in e["payload"]["html"] and e["payload"]["parent_frame_id"]
        for e in snapshots
    )
    assert any(
        a["locator"].get("name") == "Pay now" and a["locator"].get("role") == "button"
        for e in snapshots
        for a in e["payload"]["actions"]
    )
    api = next(e for e in data["events"] if e["type"] == "api")
    assert api["annotation_id"] == case.id
    assert api["payload"]["status"] == 200
    assert api["payload"]["response_body"]["data"]["result"] == "Payment accepted"
    raw = recorder.store.path.read_text()
    for secret in [
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
        assert secret not in raw, secret
    assert "rrweb" not in raw.lower()
    assert set(data["counts"]) <= {
        "session.start",
        "session.end",
        "annotation.start",
        "annotation.end",
        "checkpoint",
        "html",
        "page.open",
        "page.close",
        "api",
    }


async def test_noise_and_mutation_storm_are_bounded(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(
        tmp_path,
        Config(
            min_snapshot_interval_ms=400,
            debounce_ms=100,
            max_wait_ms=600,
            ignore_selectors=["#ticker"],
            dom_budget_ms=50,
        ),
    )
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await page.evaluate(
            "document.body.insertAdjacentHTML('beforeend','<div id=ticker>0</div>')"
        )
        await recorder.checkpoint()
        baseline = recorder.store.counts["html"]
        await page.evaluate("""async () => {
          for (let i=0;i<100;i++) {
            document.body.style.opacity = String(.9 + (i%2)/10);
            document.body.className = 'animation-' + i;
            document.querySelector('#ticker').textContent = String(i);
            document.dispatchEvent(new MouseEvent('mousemove', {bubbles:true}));
            document.dispatchEvent(new Event('scroll'));
          }
          await Promise.all(Array.from({length:20},()=>fetch('/api/large')));
        }""")
        await asyncio.sleep(0.7)
        assert recorder.store.counts["html"] == baseline
        assert recorder.store.counts["api"] == 0
        assert not recorder.requests and not recorder.tasks
        # Many mutations in each frame, for longer than max_wait: the UI remains
        # responsive and capture progresses without one event per mutation.
        start = time.monotonic()
        ticks = await page.evaluate("""() => new Promise(resolve => {
          let ticks=0; const timer=setInterval(() => {
            for (let n=0;n<200;n++) document.querySelector('#result').textContent=String(ticks*200+n);
            if (++ticks===100) { clearInterval(timer); resolve(ticks); }
          }, 10);
        })""")
        assert ticks == 100 and time.monotonic() - start < 8
        await recorder.checkpoint()
        assert 1 <= recorder.store.counts["html"] - baseline <= 6
        count = recorder.store.counts["html"]
        await recorder.checkpoint()
        assert (
            recorder.store.counts["html"] == count
        )  # Explicit checkpoint deduplicates too.
    finally:
        await recorder.stop()
    result = json.loads(recorder.store.path.read_text())
    assert len(result["events"]) < 20
    assert recorder.store.path.stat().st_size < 80_000


async def test_large_dom_budget_root_scope_and_mobile(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(
        tmp_path,
        settings(
            root_selector="#result",
            max_dom_nodes=80,
            max_html_chars=2000,
            device="Pixel 7",
        ),
    )
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        assert await page.evaluate("navigator.maxTouchPoints") > 0
        assert page.viewport_size["width"] < 500
        await page.evaluate(
            "document.querySelector('#result').innerHTML = '<button>Buy</button>'.repeat(10000)"
        )
        await recorder.checkpoint()
    finally:
        await recorder.stop()
    result = json.loads(recorder.store.path.read_text())
    snapshots = [e["payload"] for e in result["events"] if e["type"] == "html"]
    assert snapshots[-1]["truncated"]
    assert len(snapshots[-1]["html"]) <= 2000
    assert snapshots[-1]["nodes"] <= 81
    assert "Checkout" not in snapshots[-1]["html"]
    assert result["browser"]["device"] == "Pixel 7"
    assert result["browser"]["mobile_emulation"]


async def test_last_page_close_finalizes_and_restart(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(tmp_path, settings())
    page = await recorder.start(
        demo_server, headless=True, executable_path=chromium_path
    )
    recorder.start_annotation("Auto close")
    first = recorder.store.path
    await page.close()
    await wait_for(lambda: recorder.store.closed and not recorder.stopping)
    result = json.loads(first.read_text())
    assert result["status"] == "complete"
    assert result["annotations"][0]["end_seq"]
    try:
        await recorder.start(demo_server, headless=True, executable_path=chromium_path)
        assert recorder.store.path != first
    finally:
        await recorder.stop()


async def test_failed_start(tmp_path):
    recorder = Recorder(tmp_path)
    with pytest.raises(Error):
        await recorder.start(headless=True, executable_path="/not/a/browser")
    assert json.loads(recorder.store.path.read_text())["status"] == "complete"


async def test_body_bounds_and_api_capacity(tmp_path, demo_server, chromium_path):
    recorder = Recorder(
        tmp_path,
        settings(
            api_allowlist=[demo_server + "/api/*"],
            max_body_bytes=200,
            max_pending_apis=2,
        ),
    )
    release = asyncio.Event()

    async def slow(route):
        await release.wait()
        await route.fulfill(
            content_type="application/json",
            body='{"ok":true}',
            headers={"Content-Length": "11"},
        )

    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await page.evaluate("fetch('/api/large').then(r=>r.json())")
        await wait_for(lambda: recorder.store.counts["api"] == 1)
        await page.route("**/api/slow*", slow)
        pending = asyncio.create_task(
            page.evaluate(
                "Promise.all(Array.from({length:20},(_,i)=>fetch('/api/slow'+i)))"
            )
        )
        await wait_for(lambda: recorder.store.dropped_apis >= 18)
        assert len(recorder.requests) + len(recorder.tasks) <= 2
        release.set()
        await pending
        await wait_for(lambda: not recorder.requests and not recorder.tasks)
    finally:
        release.set()
        await recorder.stop()
    data = json.loads(recorder.store.path.read_text())
    api = [e["payload"] for e in data["events"] if e["type"] == "api"]
    assert len(api) == 3
    assert api[0]["response_body"]["omitted"]
    assert "Large result" not in recorder.store.path.read_text()


async def test_deferred_root_and_no_action_mode(tmp_path, demo_server, chromium_path):
    recorder = Recorder(tmp_path, settings(root_selector="#late", record_actions=False))
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await page.evaluate(
            "document.body.insertAdjacentHTML('beforeend','<section id=late><input aria-label=Late></section>')"
        )
        await wait_for(lambda: recorder.store.counts["html"] > 0)
        await page.get_by_label("Late", exact=True).fill("Current value")
        await recorder.checkpoint()
    finally:
        await recorder.stop()
    snapshots = [
        e["payload"]
        for e in json.loads(recorder.store.path.read_text())["events"]
        if e["type"] == "html"
    ]
    assert "Current value" in snapshots[-1]["html"]
    assert all(not s["actions"] for s in snapshots)


async def test_start_does_not_wait_for_slow_resources(
    tmp_path, chromium_path, monkeypatch
):
    from app import browser as browser_tools

    recorder = Recorder(tmp_path, settings())
    release = asyncio.Event()
    original_launch = browser_tools.launch

    async def launch(*args, **kwargs):
        playwright, browser, context = await original_launch(*args, **kwargs)

        async def route_handler(route):
            if route.request.url.endswith("/slow.png"):
                await release.wait()
                await route.fulfill(status=204)
            else:
                await route.fulfill(
                    content_type="text/html",
                    body='<h1>Usable</h1><img src="/slow.png">',
                )

        await context.route("https://example.com/**", route_handler)
        return playwright, browser, context

    monkeypatch.setattr(browser_tools, "launch", launch)
    try:
        page = await asyncio.wait_for(
            recorder.start(
                "https://example.com/", headless=True, executable_path=chromium_path
            ),
            timeout=3,
        )
        assert await page.get_by_role("heading").inner_text() == "Usable"
        await recorder.checkpoint()
    finally:
        release.set()
        await recorder.stop()
