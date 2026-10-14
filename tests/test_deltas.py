import json

from app.config import Config
from app.recorder import Recorder


async def test_same_task_loading_add_modify_remove_is_retained(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(tmp_path, Config(dom_budget_ms=50))
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await recorder.checkpoint()
        recorder.start_annotation("Fast loading")
        await page.evaluate("""() => {
          const overlay=document.createElement('div');
          overlay.id='loading-overlay'; overlay.setAttribute('role','status');
          overlay.innerHTML='  Loading   order  ';
          document.body.append(overlay);
          overlay.firstChild.data='Order ready';
          overlay.className='loaded';
          overlay.remove();
          document.querySelector('#result').textContent='Payment ready';
        }""")
        await recorder.checkpoint()
        await recorder.end_annotation()
    finally:
        await recorder.stop()
    data = json.loads(recorder.store.path.read_text())
    events = [e for e in data["events"] if e["type"] == "html"]
    assert len([e for e in events if e["payload"]["mode"] == "initial"]) == 1
    changes = [op for e in events for op in e["payload"]["changes"]]
    added = next(
        op
        for op in changes
        if op["op"] == "add" and "loading-overlay" in op.get("html", "")
    )
    assert "  Loading" not in added["html"]
    assert "Loading order" in added["html"]
    removed = next(
        op
        for op in changes
        if op["op"] == "remove" and op["node_id"] == added["node_id"]
    )
    text = next(
        op for op in changes if op["op"] == "text" and op["value"] == "Order ready"
    )
    assert added["change_id"] < text["change_id"] < removed["change_id"]
    assert text["previous"].strip() == "Loading order"
    assert all(
        "html" not in e["payload"] for e in events if e["payload"]["mode"] == "patch"
    )
    assert all("Checkout" not in op.get("html", "") for op in changes)
    assert not data["counts"].get("warning")


async def test_fast_low_latency_api_skeleton_replacement(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(
        tmp_path, Config(api_allowlist=[demo_server + "/api/pay"], dom_budget_ms=50)
    )
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await recorder.checkpoint()
        await page.evaluate("""async () => {
          const result=document.querySelector('#result');
          result.innerHTML='<div data-testid="skeleton">Loading payment</div>';
          const reply=await fetch('/api/pay',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
          const data=await reply.json();
          result.innerHTML='<button>Confirm '+data.result+'</button>';
        }""")
        await recorder.checkpoint()
    finally:
        await recorder.stop()
    events = json.loads(recorder.store.path.read_text())["events"]
    ops = [op for e in events if e["type"] == "html" for op in e["payload"]["changes"]]
    skeleton = next(op for op in ops if "skeleton" in op.get("html", ""))
    assert any(
        op["op"] == "remove" and op["node_id"] == skeleton["node_id"] for op in ops
    )
    assert any("Confirm Payment accepted" in op.get("html", "") for op in ops)
    assert (
        next(e for e in events if e["type"] == "api")["payload"]["response_body"][
            "data"
        ]["result"]
        == "Payment accepted"
    )


async def test_text_and_visibility_histories_no_full_redumps(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(tmp_path, Config(dom_budget_ms=50))
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await recorder.checkpoint()
        await page.evaluate("""() => {
          const el=document.querySelector('#result');
          el.textContent='Loading'; el.textContent='Ready'; el.textContent='Done';
          el.className='skeleton'; el.className='ready'; el.className='done';
          el.style.display='none'; el.style.display='block';
        }""")
        await recorder.checkpoint()
    finally:
        await recorder.stop()
    data = json.loads(recorder.store.path.read_text())
    ops = [
        op
        for e in data["events"]
        if e["type"] == "html"
        for op in e["payload"]["changes"]
    ]
    text = next(op for op in ops if op["op"] == "set_text" and op["value"] == "Done")
    assert [x["value"] for x in text["intermediate"]] == ["Loading", "Ready"]
    classes = next(
        op for op in ops if op["op"] == "attribute" and op["name"] == "class"
    )
    assert [x["value"] for x in classes["intermediate"]] == ["skeleton", "ready"]
    assert classes["value"] == "done"
    style = next(op for op in ops if op["op"] == "attribute" and op["name"] == "style")
    assert style["intermediate"][0]["value"] == "display: none"
    assert style["value"] == "display: block"


async def test_mutation_below_truncated_baseline_keeps_local_context(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(tmp_path, Config(max_dom_nodes=20, dom_budget_ms=50))
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await recorder.checkpoint()
        await page.evaluate(
            "document.body.insertAdjacentHTML('beforeend','<section>'+ '<span>Item</span>'.repeat(100) + '<div id=bottom>Original</div></section>')"
        )
        await recorder.checkpoint()
        await page.evaluate(
            "document.querySelector('#bottom').textContent='Bottom result'"
        )
        await recorder.checkpoint()
    finally:
        await recorder.stop()
    data = json.loads(recorder.store.path.read_text())
    ops = [
        op
        for e in data["events"]
        if e["type"] == "html"
        for op in e["payload"]["changes"]
    ]
    assert any(op["op"] == "context" and 'id="bottom"' in op["html"] for op in ops)
    assert any(op["op"] == "set_text" and op["value"] == "Bottom result" for op in ops)
    assert not data["counts"].get("warning")


async def test_checkboxes_and_sensitive_textarea_mutations(
    tmp_path, demo_server, chromium_path
):
    recorder = Recorder(tmp_path, Config(dom_budget_ms=50))
    try:
        page = await recorder.start(
            demo_server, headless=True, executable_path=chromium_path
        )
        await recorder.checkpoint()
        await page.evaluate(
            "document.body.insertAdjacentHTML('beforeend','<label>Agree<input type=checkbox id=agree></label><textarea name=password>INITIAL_TEXT_SECRET</textarea>')"
        )
        await recorder.checkpoint()
        await page.get_by_label("Agree").check()
        await page.evaluate(
            "document.querySelector('#customer').setAttribute('value','Prefilled')"
        )
        await page.evaluate(
            "document.querySelector('textarea').textContent='EDITED_TEXT_SECRET'"
        )
        await recorder.checkpoint()
    finally:
        await recorder.stop()
    raw = recorder.store.path.read_text()
    assert "INITIAL_TEXT_SECRET" not in raw and "EDITED_TEXT_SECRET" not in raw
    ops = [
        op
        for e in json.loads(raw)["events"]
        if e["type"] == "html"
        for op in e["payload"]["changes"]
    ]
    assert any(op["op"] == "value" and op.get("checked") is True for op in ops)
    assert any(op["op"] == "value" and op.get("value") == "Prefilled" for op in ops)
