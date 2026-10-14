"""One growing JSON document, with bounded buffers and batched off-thread I/O."""

import asyncio
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .config import Config
from .events import Event
from .redaction import Redactor

_ACTIVE = object()
MARKER = b'"events": [\n'


class SessionStore:
    def __init__(self, output: Path, config: Config):
        output.mkdir(parents=True, exist_ok=True)
        self.id = uuid4().hex
        self.path = output / f"session-{self.id}.json"
        self.redactor = Redactor(config)
        self.started = time.monotonic()
        self.seq = 0
        self.counts = Counter()
        self.annotation_id = None
        self.annotations = []
        self.closed = False
        self.status = "recording"
        self.reason = None
        self.dropped_events = 0
        self.dropped_apis = 0
        self.pending = []
        self.max_pending = config.max_pending_events
        self._lock = asyncio.Lock()
        self._written = 0
        self.header = {
            "schema_version": 3,
            "session_id": self.id,
            "started_at": datetime.now(UTC).isoformat(),
            "browser": {
                "engine": "chromium",
                "device": config.device,
                "mobile_emulation": config.device is not None,
            },
            "config": self.redactor.clean(config.model_dump()),
            "guidance": "One minified HTML baseline per document, then ordered add/remove/text/attribute/value patches with associated actions and allowlisted API results. Loading states may be transient. Export-only node IDs are not application locators. See AGENT_GUIDE.md. Inspect truncation, coalescing, and dropped counters. Device mode emulates Chromium, not Safari or a real phone.",
        }
        prefix = (
            json.dumps(self.header, ensure_ascii=False).encode()[:-1] + b",\n" + MARKER
        )
        # A private file, not a directory of telemetry and artifacts.
        self.stream = os.fdopen(
            os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600), "w+b"
        )
        self.stream.write(prefix)
        self._tail = self.stream.tell()
        self._last_footer = self._footer()
        self.stream.write(self._last_footer)
        self.stream.flush()

    def _footer(self):
        metadata = {
            "status": self.status,
            "reason": self.reason,
            "annotations": self.redactor.clean(self.annotations),
            "counts": dict(self.counts),
            "dropped_events": self.dropped_events,
            "dropped_apis": self.dropped_apis,
        }
        return b"\n],\n" + json.dumps(metadata, ensure_ascii=False).encode()[1:] + b"\n"

    def emit(
        self,
        type,
        payload,
        *,
        source="recorder",
        page_id=None,
        frame_id=None,
        annotation_id=_ACTIVE,
    ):
        if self.closed:
            return None
        if len(self.pending) >= self.max_pending:
            self.dropped_events += 1
            return None
        event = Event(
            seq=self.seq + 1,
            timestamp=datetime.now(UTC),
            elapsed_ms=(time.monotonic() - self.started) * 1000,
            session_id=self.id,
            page_id=page_id,
            frame_id=frame_id,
            annotation_id=self.annotation_id
            if annotation_id is _ACTIVE
            else annotation_id,
            source=source,
            type=type,
            payload=self.redactor.clean(payload),
        )
        self.pending.append(event.model_dump_json().encode())
        self.seq = event.seq
        self.counts[type] += 1
        return event

    def _write(self, batch, footer):
        self.stream.seek(self._tail)
        for value in batch:
            if self._written:
                self.stream.write(b",\n")
            self.stream.write(value)
            self._written += 1
        self._tail = self.stream.tell()
        self.stream.write(footer)
        self.stream.truncate()
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self._last_footer = footer

    async def flush(self):
        async with self._lock:
            if self.closed:
                return
            footer = self._footer()
            if not self.pending and footer == self._last_footer:
                return
            batch, self.pending = self.pending, []
            # Only new events and a small metadata footer are written. The entire
            # recording is never loaded or rewritten during normal capture.
            write = asyncio.create_task(asyncio.to_thread(self._write, batch, footer))
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                await write  # Do not close a file while its writer is still running.
                raise

    async def finish(self, reason):
        if self.closed:
            return
        await self.flush()
        self.emit("session.end", {"reason": reason})
        self.status = "complete"
        self.reason = reason
        await self.flush()
        self.stream.close()
        self.closed = True


def repair_recording(path: Path):
    """Recover complete events if a crash interrupted an in-place batch write."""
    data = path.read_bytes()
    if MARKER not in data:
        raise ValueError("Not a supported recording")
    head, tail = data.split(MARKER, 1)
    header = json.loads(head.rstrip().removesuffix(b",") + b"}")
    decoder = json.JSONDecoder()
    text = tail.decode("utf-8", errors="replace")
    events, position = [], 0
    while position < len(text):
        while position < len(text) and text[position] in " \r\n\t,":
            position += 1
        try:
            event, end = decoder.raw_decode(text, position)
            Event.model_validate(event)
        except (ValueError, TypeError):
            break
        events.append(event)
        position = end
    annotations = []
    for event in events:
        if event["type"] == "annotation.start":
            annotations.append(dict(event["payload"]))
        elif event["type"] == "annotation.end" and annotations:
            annotations[-1]["end_seq"] = event["seq"]
    header.update(
        events=events,
        annotations=annotations,
        status="recovered",
        reason="Recovered complete events; buffered or partial data may be missing",
        counts=dict(Counter(e["type"] for e in events)),
    )
    target = path.with_name(path.stem + ".recovered.json")
    with target.open("x", encoding="utf-8") as stream:
        target.chmod(0o600)
        json.dump(header, stream, ensure_ascii=False, indent=2)
    return target
