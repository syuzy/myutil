import json
from itertools import pairwise

from app.config import Config
from app.events import Event
from app.storage import SessionStore, repair_recording


async def test_single_json_live_flush_order_and_bounded_buffer(tmp_path):
    store = SessionStore(tmp_path, Config(max_pending_events=10))
    assert json.loads(store.path.read_text())["events"] == []
    store.annotation_id = "case-1"
    for i in range(15):
        store.emit("html", {"html": f"<p>{i}</p>"})
    assert len(store.pending) == 10 and store.dropped_events == 5
    await store.flush()
    first = json.loads(store.path.read_text())
    assert len(first["events"]) == 10 and first["status"] == "recording"
    store.emit("checkpoint", {})
    await store.finish("test")
    await store.finish("again")
    data = json.loads(store.path.read_text())
    assert list(tmp_path.iterdir()) == [store.path]
    assert data["session_id"] == store.id
    assert data["status"] == "complete"
    events = [Event.model_validate(e) for e in data["events"]]
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert all(a.elapsed_ms <= b.elapsed_ms for a, b in pairwise(events))
    assert all(e.timestamp.utcoffset().total_seconds() == 0 for e in events)
    assert data["dropped_events"] == 5


async def test_redaction_and_crash_repair(tmp_path):
    store = SessionStore(tmp_path, Config(extra_headers=["X-Customer-Key"]))
    store.emit(
        "api",
        {
            "url": "https://user:PASS@example.com/pay?token=TOKEN_SECRET",
            "headers": {
                "Authorization": "Bearer AUTH_SECRET",
                "Cookie": "COOKIE_SECRET",
                "X-Customer-Key": "EXTRA_SECRET",
            },
            "body": {
                "password": "PW_SECRET",
                "card_number": "4111111111111111",
                "cvc": "987",
                "name": "Jane",
            },
        },
    )
    await store.flush()
    raw = store.path.read_text()
    for value in [
        "TOKEN_SECRET",
        "AUTH_SECRET",
        "COOKIE_SECRET",
        "EXTRA_SECRET",
        "PW_SECRET",
        "4111111111111111",
        "987",
        "user:PASS",
    ]:
        assert value not in raw
    assert "Jane" in raw
    # Simulate a kill during the next event write, preserving the complete prefix.
    store.stream.seek(store._tail)
    store.stream.write(b',\n{"seq": 2, "payload": "incom')
    store.stream.truncate()
    store.stream.close()
    recovered = repair_recording(store.path)
    data = json.loads(recovered.read_text())
    assert data["status"] == "recovered"
    assert len(data["events"]) == 1
    assert store.path.read_text().endswith("incom")


async def test_idle_flush_does_not_write(tmp_path, monkeypatch):
    store = SessionStore(tmp_path, Config())
    writes = []
    original = store._write

    def track(batch, footer):
        writes.append(len(batch))
        original(batch, footer)

    monkeypatch.setattr(store, "_write", track)
    await store.flush()
    await store.flush()
    assert writes == []
    store.emit("html", {"html": "<p>Changed</p>"})
    await store.flush()
    await store.flush()
    assert writes == [1]
    await store.finish("test")


def test_card_redaction_preserves_generated_ids():
    from app.redaction import Redactor

    redactor = Redactor(Config())
    identifier = "36230068050340d7bb3945c684f1037a"
    assert redactor.clean({"id": identifier})["id"] == identifier
    assert redactor.text("Card: 4111 1111 1111 1111") == "Card: [REDACTED]"
    assert redactor.text("4111111111111111") == "[REDACTED]"
