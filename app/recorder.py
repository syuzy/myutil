import asyncio
import json
import logging
from pathlib import Path
from urllib.parse import parse_qsl
from uuid import uuid4

from playwright.async_api import Error

from . import browser as browser_tools
from .events import ActionPayload, Annotation
from .redaction import Config
from .storage import SessionStore

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, output: Path = Path("sessions"), config: Config | None = None):
        self.output = Path(output)
        self.config = config or Config()
        self.store = None
        self.playwright = self.browser = self.context = None
        self.annotations = []
        self.pages = {}
        self.frames = {}
        self.requests = {}
        self.tasks = set()
        self.finalizing_tasks = set()
        self.stopping = False
        self._stop_lock = asyncio.Lock()

    @property
    def recording(self):
        return self.store is not None and not self.store.closed and not self.stopping

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)

        def done(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception():
                # Never log exception text: external page data can contain secrets.
                self.emit(
                    "recorder.warning",
                    {
                        "operation": "optional telemetry",
                        "error": type(task.exception()).__name__,
                    },
                    source="recorder",
                )
                log.warning(
                    "Optional telemetry failed (%s)", type(task.exception()).__name__
                )

        task.add_done_callback(done)
        return task

    def schedule_stop(self, reason):
        task = asyncio.create_task(self.stop(reason))
        self.finalizing_tasks.add(task)

        def done(task):
            self.finalizing_tasks.discard(task)
            if not task.cancelled() and task.exception():
                log.error(
                    "Session finalization failed (%s)", type(task.exception()).__name__
                )

        task.add_done_callback(done)

    def page_id(self, page):
        if page not in self.pages:
            self.pages[page] = f"page-{len(self.pages) + 1}"
        return self.pages[page]

    def frame_id(self, frame):
        if frame not in self.frames:
            self.frames[frame] = f"frame-{len(self.frames) + 1}"
        return self.frames[frame]

    def emit(self, type, payload, *, page=None, frame=None, source="playwright"):
        if self.store and not self.store.closed:
            return self.store.emit(
                type,
                payload,
                source=source,
                page_id=self.page_id(page) if page else None,
                frame_id=self.frame_id(frame) if frame else None,
            )

    async def start(self, url="about:blank", *, headless=False, executable_path=None):
        if self.recording or self.stopping:
            raise RuntimeError("A session is already active")
        self.pages, self.frames, self.requests, self.annotations = {}, {}, {}, []
        self.store = SessionStore(self.output, self.config)
        self.emit("session.start", {}, source="recorder")
        try:
            self.playwright, self.browser, self.context = await browser_tools.launch(
                headless=headless, executable_path=executable_path
            )
            self.browser.on(
                "disconnected",
                lambda: (
                    self.schedule_stop("browser closed") if not self.stopping else None
                ),
            )
            self.context.on("page", self.attach_page)
            self.context.on("request", self.on_request)
            self.context.on("response", self.on_response)
            self.context.on("requestfinished", self.on_finished)
            self.context.on("requestfailed", self.on_failed)
            await self.context.expose_binding("__recorderEmit", self.on_sensor)
            await self.context.add_init_script(
                script=browser_tools.sensor_script(self.config)
            )
            page = await self.context.new_page()
            await page.goto(url)
            return page
        except BaseException:
            await self.stop("startup failed")
            raise

    def attach_page(self, page):
        self.emit("page.created", {"url": page.url}, page=page, frame=page.main_frame)
        page.on("close", lambda: self.page_closed(page))
        page.on("crash", lambda: self.emit("page.crashed", {}, page=page))
        page.on(
            "framenavigated",
            lambda f: self.emit(
                "navigation",
                {"url": f.url, "name": f.name, "is_main_frame": f == page.main_frame},
                page=page,
                frame=f,
            ),
        )
        page.on(
            "frameattached",
            lambda f: self.emit(
                "frame.attached",
                {
                    "parent_frame_id": self.frame_id(f.parent_frame)
                    if f.parent_frame
                    else None
                },
                page=page,
                frame=f,
            ),
        )
        page.on(
            "framedetached",
            lambda f: self.emit("frame.detached", {}, page=page, frame=f),
        )
        page.on(
            "popup",
            lambda p: self.emit(
                "page.popup", {"opener_page_id": self.page_id(page)}, page=p
            ),
        )
        page.on(
            "console",
            lambda m: (
                self.emit(
                    "console.error", {"text": m.text, "location": m.location}, page=page
                )
                if m.type == "error"
                else None
            ),
        )
        page.on(
            "pageerror",
            lambda e: self.emit("page.error", {"message": str(e)}, page=page),
        )
        page.on("websocket", lambda ws: self.on_websocket(page, ws))

    def page_closed(self, page):
        self.emit("page.closed", {}, page=page)
        if not self.stopping and self.context and not self.context.pages:
            self.schedule_stop("last page closed")

    def on_websocket(self, page, ws):
        ws_id = uuid4().hex
        self.emit("websocket.open", {"id": ws_id, "url": ws.url}, page=page)
        ws.on("close", lambda: self.emit("websocket.close", {"id": ws_id}, page=page))
        ws.on(
            "socketerror",
            lambda e: self.emit(
                "websocket.error", {"id": ws_id, "error": str(e)}, page=page
            ),
        )

    def request_context(self, request):
        try:
            return {"frame": request.frame, "page": request.frame.page}
        except Error:
            return {}  # Service-worker requests do not have a frame.

    def request_id(self, request):
        if request not in self.requests:
            self.requests[request] = f"request-{len(self.requests) + 1}"
        return self.requests[request]

    def body_payload(self, body: bytes, content_type: str):
        if len(body) > self.config.max_body_bytes:
            return {"body_omitted": "size limit", "body_bytes": len(body)}
        if "json" in content_type:
            try:
                value = json.loads(body)
            except (ValueError, UnicodeError):
                return {"body_omitted": "invalid JSON"}
        elif "x-www-form-urlencoded" in content_type:
            value = dict(
                parse_qsl(
                    body.decode("utf-8", errors="replace"), keep_blank_values=True
                )
            )
        elif content_type.startswith("text/plain"):
            value = body.decode("utf-8", errors="replace")
        else:
            return {"body_omitted": "resource type"}
        if len(body) > self.config.inline_body_bytes:
            return {
                "body_artifact": self.store.artifact("network", {"body": value}),
                "body_bytes": len(body),
            }
        return {"body": value, "body_bytes": len(body)}

    def on_request(self, request):
        payload = {
            "request_id": self.request_id(request),
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "headers": request.headers,
        }
        if request.redirected_from:
            payload["redirected_from"] = self.request_id(request.redirected_from)
            self.emit(
                "network.redirect",
                {
                    "from_request_id": payload["redirected_from"],
                    "to_request_id": payload["request_id"],
                    "url": request.url,
                },
                **self.request_context(request),
            )
        if request.post_data_buffer:
            payload.update(
                self.body_payload(
                    request.post_data_buffer, request.headers.get("content-type", "")
                )
            )
        self.emit("network.request", payload, **self.request_context(request))

    def on_response(self, response):
        self.emit(
            "network.response",
            {
                "request_id": self.request_id(response.request),
                "url": response.url,
                "status": response.status,
                "headers": response.headers,
            },
            **self.request_context(response.request),
        )

    def on_failed(self, request):
        self.emit(
            "network.failed",
            {
                "request_id": self.request_id(request),
                "failure": request.failure,
                "timing": request.timing,
            },
            **self.request_context(request),
        )

    def on_finished(self, request):
        self.emit(
            "network.finished",
            {"request_id": self.request_id(request), "timing": request.timing},
            **self.request_context(request),
        )
        self.spawn(self.capture_body(request))

    async def capture_body(self, request):
        response = await request.response()
        if response is None or request.resource_type not in {"fetch", "xhr"}:
            return
        content_type = response.headers.get("content-type", "").lower()
        if not ("json" in content_type or content_type.startswith("text/plain")):
            return
        # Only bounded bodies are retrieved; unknown/chunked bodies stay metadata-only.
        size = response.headers.get("content-length", "")
        if not size.isdigit() or int(size) > self.config.max_body_bytes:
            self.emit(
                "network.body",
                {
                    "request_id": self.request_id(request),
                    "body_omitted": "unknown or oversized length",
                },
                **self.request_context(request),
            )
            return
        try:
            payload = self.body_payload(await response.body(), content_type)
        except Error:
            payload = {"body_omitted": "read failed"}
        self.emit(
            "network.body",
            {"request_id": self.request_id(request), **payload},
            **self.request_context(request),
        )

    def on_sensor(self, source, data):
        if not self.recording:
            return
        try:
            if not isinstance(data, dict) or data.get("type") not in {
                "dom",
                "action.click",
                "action.input",
                "action.change",
                "action.submit",
                "sensor.error",
                "location",
            }:
                raise ValueError("Invalid sensor event")
            payload = data.get("payload")
            if not isinstance(payload, dict):
                raise TypeError("Invalid sensor payload")
            if data["type"].startswith("action."):
                payload = ActionPayload.model_validate(payload).model_dump(
                    exclude_none=True
                )
            if data["type"] == "dom":
                mutation = payload.get("data")
                if not isinstance(payload.get("type"), int) or not isinstance(
                    mutation, dict
                ):
                    raise TypeError("Invalid rrweb event")
                for key in ("adds", "removes", "texts", "attributes"):
                    entries = mutation.get(key, [])
                    if not isinstance(entries, list) or not all(
                        isinstance(e, dict) for e in entries
                    ):
                        raise TypeError("Invalid rrweb mutation")
                summary = {
                    "rrweb_type": payload.get("type"),
                    "source": payload.get("data", {}).get("source"),
                }
                summary["changes"] = {
                    k: len(mutation.get(k, []))
                    for k in ("adds", "removes", "texts", "attributes")
                }
                summary["artifact"] = self.store.artifact("dom", payload)
                payload = summary
            self.emit(
                data["type"],
                payload,
                page=source["page"],
                frame=source["frame"],
                source="rrweb" if data["type"] == "dom" else "sensor",
            )
        except (TypeError, ValueError, KeyError, AttributeError):
            self.emit(
                "recorder.warning",
                {"operation": "malformed sensor event skipped"},
                source="recorder",
            )

    def save_annotations(self):
        self.store.write_json(
            "annotations.json", [a.model_dump() for a in self.annotations]
        )

    def start_annotation(self, name, *, goal=None, expected_result=None, tags=None):
        if not self.recording:
            raise RuntimeError("Start a session first")
        if self.store.annotation_id:
            raise RuntimeError("End the current annotation first")
        if not name.strip():
            raise ValueError("Enter an annotation name")
        annotation = Annotation(
            id=uuid4().hex,
            name=name.strip(),
            goal=goal,
            expected_result=expected_result,
            tags=tags or [],
            start_seq=self.store.seq + 1,
        )
        self.store.annotation_id = annotation.id
        self.annotations.append(annotation)
        self.emit("annotation.start", annotation.model_dump(), source="gui")
        self.save_annotations()
        return annotation

    async def observed_state(self):
        for page in list(self.context.pages) if self.context else []:
            if page.is_closed():
                continue
            try:
                state = await page.evaluate(
                    "() => window.__recorderState ? window.__recorderState() : {url: location.href, title: document.title}"
                )
                self.emit(
                    "page.state",
                    state,
                    page=page,
                    frame=page.main_frame,
                    source="sensor",
                )
            except Error:
                self.emit(
                    "recorder.warning",
                    {"operation": "page state unavailable"},
                    page=page,
                    source="recorder",
                )

    async def end_annotation(self):
        if not self.store or not self.store.annotation_id:
            return
        await self.observed_state()
        annotation = self.annotations[-1]
        event = self.emit("annotation.end", {"id": annotation.id}, source="gui")
        annotation.end_seq = event.seq
        self.save_annotations()
        self.store.annotation_id = None

    async def checkpoint(self):
        if not self.recording:
            raise RuntimeError("Start a session first")
        self.emit("checkpoint", {}, source="gui")
        await self.observed_state()
        for page in list(self.context.pages):
            for frame in page.frames:
                try:
                    await frame.evaluate("() => window.__recorderSnapshot?.()")
                except Error:
                    self.emit(
                        "recorder.warning",
                        {"operation": "checkpoint snapshot unavailable"},
                        page=page,
                        frame=frame,
                        source="recorder",
                    )

    async def stop(self, reason="user stopped"):
        async with self._stop_lock:
            if not self.store or self.store.closed:
                return
            self.stopping = True
            try:
                await self.end_annotation()
                await self.observed_state()
                if self.context:
                    try:
                        await self.context.close()
                    except Error:
                        pass
                pending = [t for t in self.tasks if t is not asyncio.current_task()]
                if pending:
                    _, unfinished = await asyncio.wait(pending, timeout=5)
                    for task in unfinished:
                        task.cancel()
                    await asyncio.gather(*unfinished, return_exceptions=True)
                if self.browser:
                    await self.browser.close()
            finally:
                try:
                    if self.playwright:
                        await self.playwright.stop()
                finally:
                    self.playwright = self.browser = self.context = None
                    try:
                        self.store.finish(reason)
                        from .context_export import export_context

                        export_context(self.store.path)
                    finally:
                        self.stopping = False
