import json
from itertools import pairwise

from app.events import Event
from app.redaction import REDACTED, Config, Redactor
from app.storage import SessionStore


def test_append_order_and_metadata(tmp_path):
    store = SessionStore(tmp_path, Config())
    store.annotation_id = "case-1"
    for n in range(20):
        store.emit("action.click", {"n": n}, page_id="p", frame_id="f")
    events = [
        Event.model_validate_json(line)
        for line in (store.path / "events.jsonl").read_text().splitlines()
    ]
    assert [e.seq for e in events] == list(range(1, 21))
    assert all(e.timestamp.utcoffset().total_seconds() == 0 for e in events)
    assert all(e.annotation_id == "case-1" for e in events)
    assert all(a.elapsed_ms <= b.elapsed_ms for a, b in pairwise(events))
    store.finish("test")
    store.finish("idempotent")
    manifest = json.loads((store.path / "manifest.json").read_text())
    assert manifest["session_id"] == store.id
    assert manifest["event_count"] == 21


def test_redact_nested_headers_urls_and_body(tmp_path):
    store = SessionStore(tmp_path, Config(extra_headers=["X-Customer-Key"]))
    store.emit(
        "network.request",
        {
            "url": "https://user:pass@example.com/pay?access_token=tokSECRET&name=Jane",
            "headers": {
                "Authorization": "Bearer AUTHSECRET",
                "Cookie": "SESSIONSECRET",
                "Set-Cookie": "NEWSECRET",
                "X-Customer-Key": "EXTRASECRET",
            },
            "body": {
                "password": "PWSECRET",
                "nested": {"refreshToken": "REFRESHSECRET"},
                "card_number": "4111 1111 1111 1111",
                "cvv": "123",
                "name": "Jane",
            },
            "text": "Authorization: Bearer ABCDEFG and CVV=987 tokenless 4111111111111111",
        },
    )
    artifact = store.artifact(
        "network", {"body": {"apiKey": "APISECRET", "name": "Jane"}}
    )
    store.finish("test")
    data = "\n".join(p.read_text() for p in store.path.rglob("*.json*"))
    for secret in [
        "tokSECRET",
        "AUTHSECRET",
        "SESSIONSECRET",
        "NEWSECRET",
        "EXTRASECRET",
        "PWSECRET",
        "REFRESHSECRET",
        "4111",
        "APISECRET",
        "user:pass",
        "987",
    ]:
        assert secret not in data
    assert "Jane" in data
    assert json.loads((store.path / artifact).read_text())["body"]["apiKey"] == REDACTED


def test_binary_noise():
    redactor = Redactor(Config())
    assert "AAAA" not in redactor.text("data:image/png;base64," + "AAAA" * 100)
    assert "eyJ" not in redactor.text("eyJabc.defghi.jklmno")
