import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .events import Event
from .redaction import Config, Redactor


class SessionStore:
    def __init__(self, output: Path, config: Config):
        self.id = uuid4().hex
        self.path = output / f"session-{self.id}"
        self.path.mkdir(parents=True)
        # Recordings can contain proprietary content even after redaction.
        self.path.chmod(0o700)
        for name in ("network", "dom", "screenshots"):
            (self.path / "artifacts" / name).mkdir(parents=True)
        (self.path / "derived").mkdir()
        self.redactor = Redactor(config)
        self.started = time.monotonic()
        self.seq = 0
        self.counts = Counter()
        self.annotation_id = None
        self.closed = False
        self.stream = (self.path / "events.jsonl").open(
            "x", encoding="utf-8", buffering=1
        )
        self.manifest = {
            "schema_version": 1,
            "session_id": self.id,
            "started_at": datetime.now(UTC).isoformat(),
            "status": "recording",
            "browser": "chromium",
            "config": config.model_dump(),
        }
        self.write_json("manifest.json", self.manifest)
        self.write_json("annotations.json", [])

    def write_json(self, relative: str, value):
        target = self.path / relative
        temp = target.with_suffix(target.suffix + ".tmp")
        clean = self.redactor.clean(value)
        if relative == "manifest.json":
            clean["session_id"] = self.id
        temp.write_text(
            json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temp.replace(target)

    def artifact(self, kind: str, value) -> str:
        relative = f"artifacts/{kind}/{uuid4().hex}.json"
        self.write_json(relative, value)
        return relative

    def emit(
        self,
        type: str,
        payload: dict,
        *,
        source="playwright",
        page_id=None,
        frame_id=None,
    ):
        if self.closed:
            return None
        event = Event(
            seq=self.seq + 1,
            timestamp=datetime.now(UTC),
            elapsed_ms=(time.monotonic() - self.started) * 1000,
            session_id=self.id,
            page_id=page_id,
            frame_id=frame_id,
            annotation_id=self.annotation_id,
            source=source,
            type=type,
            payload=self.redactor.clean(payload),
        )
        self.stream.write(event.model_dump_json() + "\n")
        self.stream.flush()
        self.seq = event.seq
        self.counts[type] += 1
        return event

    def finish(self, reason: str):
        if self.closed:
            return
        self.emit("session.end", {"reason": reason}, source="recorder")
        self.manifest.update(
            status="complete",
            ended_at=datetime.now(UTC).isoformat(),
            event_count=self.seq,
            counts=dict(self.counts),
        )
        self.write_json("manifest.json", self.manifest)
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.stream.close()
        self.closed = True
