import asyncio
import json
import logging
import time
from fnmatch import fnmatchcase
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from uuid import uuid4

from playwright.async_api import Error
from playwright.async_api import TimeoutError as PlaywrightTimeout

from . import browser as browser_tools
from .config import Config
from .events import Annotation
from .storage import SessionStore

log = logging.getLogger(__name__)


class Recorder:
    def __init__(self, output=Path("sessions"), config=None):
        self.output = Path(output)
        self.config = config or Config()
        self.store = None
        self.playwright = self.browser = self.context = None
        self.pages, self.frames, self.requests = {}, {}, {}
        self.annotations = []
        self.tasks = set()
        self.finalizing_tasks = set()
        self.autosave = None
        self.stopping = False
        self.starting = False
        self._stop_lock = asyncio.Lock()

    @property
    def recording(self):
        return self.store is not None and not self.store.closed and not self.stopping

    def page_id(self, page):
        if page not in self.pages:
            self.pages[page] = f"page-{len(self.pages) + 1}"
        return self.pages[page]

    def frame_id(self, frame):
        if frame not in self.frames:
            self.frames[frame] = f"frame-{len(self.frames) + 1}"
        return self.frames[frame]

    def emit(
        self, type, payload, *, page=None, frame=None, source="recorder", **kwargs
    ):
        if self.store and not self.store.closed:
            return self.store.emit(
                type,
                payload,
                page_id=self.page_id(page) if page else None,
                frame_id=self.frame_id(frame) if frame else None,
                source=source,
                **kwargs,
            )

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)

        def done(task):
            self.tasks.discard(task)
            if not task.cancelled() and task.exception():
                self.emit(
                    "warning",
                    {
                        "operation": "optional capture",
                        "error": type(task.exception()).__name__,
                    },
                )

        task.add_done_callback(done)
        return task

    def schedule_stop(self, reason):
        if self.stopping or self.starting or self.finalizing_tasks:
            return
        task = asyncio.create_task(self.stop(reason))
        self.finalizing_tasks.add(task)

        def done(task):
            self.finalizing_tasks.discard(task)
            if not task.cancelled() and task.exception():
                log.error("Finalization failed (%s)", type(task.exception()).__name__)

        task.add_done_callback(done)

    async def _autosave(self):
        while self.recording:
            await asyncio.sleep(self.config.flush_interval_ms / 1000)
            await self.store.flush()

    async def start(self, url="about:blank", *, headless=False, executable_path=None):
        if self.recording or self.stopping or self.starting:
            raise RuntimeError("A session is already active")
        self.starting = True
        self.pages, self.frames, self.requests, self.annotations = {}, {}, {}, []
        self.store = SessionStore(self.output, self.config)
        self.emit("session.start", {})
        try:
            self.playwright, self.browser, self.context = await browser_tools.launch(
                self.config, headless=headless, executable_path=executable_path
            )
            self.browser.on(
                "disconnected", lambda: self.schedule_stop("browser closed")
            )
            self.context.on("page", self.attach_page)
            # No network callbacks at all when the allowlist is empty.
            if self.config.api_allowlist:
                self.context.on("request", self.on_request)
                self.context.on("requestfinished", self.on_finished)
                self.context.on("requestfailed", self.on_failed)
            await self.context.expose_binding("__recorderEmit", self.on_sensor)
            await self.context.add_init_script(
                script=browser_tools.sensor_script(self.config)
            )
            self.autosave = asyncio.create_task(self._autosave())
            self.autosave.add_done_callback(self._autosave_done)
            page = await self.context.new_page()
            await browser_tools.prepare_page(page)
            # Analytics and streaming requests must not hold Start hostage.
            try:
                await page.goto(url, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                self.emit(
                    "warning",
                    {
                        "operation": "initial navigation timed out; browser remains usable"
                    },
                    page=page,
                )
            return page
        except BaseException:
            await self.stop("startup failed")
            raise
        finally:
            self.starting = False

    def _autosave_done(self, task):
        if not task.cancelled() and task.exception():
            log.error(
                "Saving failed (%s); stopping capture", type(task.exception()).__name__
            )
            self.schedule_stop("storage failure")

    def attach_page(self, page):
        self.emit("page.open", {"url": page.url}, page=page, frame=page.main_frame)
        page.on("close", lambda: self.page_closed(page))
        if self.config.record_errors:
            page.on(
                "pageerror",
                lambda error: self.emit(
                    "error", {"message": str(error)[:2000]}, page=page
                ),
            )
        # HTML snapshots contain URL, title, frame, and state. No duplicate
        # navigation, console, WebSocket, scroll, or mousemove event streams.

    def page_closed(self, page):
        self.emit("page.close", {}, page=page)
        if self.context and not self.context.pages:
            self.schedule_stop("last page closed")

    def matches_api(self, request):
        if (
            request.resource_type not in {"fetch", "xhr"}
            or request.method not in self.config.api_methods
        ):
            return False
        parts = urlsplit(request.url)
        match_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        return any(
            fnmatchcase(match_url, pattern) for pattern in self.config.api_allowlist
        )

    def body_payload(self, body, content_type):
        if len(body) > self.config.max_body_bytes:
            return {
                "omitted": "size limit",
                "bytes": len(body),
                "limit": self.config.max_body_bytes,
            }
        try:
            if "json" in content_type:
                value = json.loads(body)
            elif "x-www-form-urlencoded" in content_type:
                value = dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
            elif content_type.startswith("text/plain"):
                value = body.decode("utf-8")
            elif not content_type:
                value = json.loads(body)  # Some JSON APIs omit Content-Type too.
            else:
                return {"omitted": "content type"}
            return {"data": value, "bytes": len(body)}
        except (ValueError, UnicodeError):
            return {"omitted": "invalid text or JSON"}

    def on_request(self, request):
        if not self.recording or not self.matches_api(request):
            return
        if len(self.requests) + len(self.tasks) >= self.config.max_pending_apis:
            self.store.dropped_apis += 1
            return
        payload = {
            "url": request.url,
            "method": request.method,
            "started_elapsed_ms": round(
                (time.monotonic() - self.store.started) * 1000, 1
            ),
        }
        if self.config.capture_headers:
            payload["request_headers"] = request.headers
        if self.config.max_body_bytes and request.method not in {"GET", "HEAD"}:
            headers = request.headers
            kind = headers.get("content-type", "").lower()
            if (
                "json" in kind
                or "x-www-form-urlencoded" in kind
                or kind.startswith("text/plain")
            ):
                body = request.post_data_buffer
                if body is not None:
                    payload["request_body"] = self.body_payload(body, kind)
            else:
                payload["request_body"] = {"omitted": "content type"}
        try:
            frame = request.frame
        except Error:
            frame = None
        self.requests[request] = (payload, frame, self.store.annotation_id)

    def on_finished(self, request):
        info = self.requests.pop(request, None)
        if info:
            self.spawn(self.capture_api(request, info))

    def on_failed(self, request):
        info = self.requests.pop(request, None)
        if info:
            payload, frame, annotation = info
            payload["failure"] = request.failure
            self.emit_api(payload, frame, annotation)

    def emit_api(self, payload, frame, annotation):
        self.emit(
            "api",
            payload,
            frame=frame,
            page=frame.page if frame else None,
            source="playwright",
            annotation_id=annotation,
        )

    async def capture_api(self, request, info):
        payload, frame, annotation = info
        try:
            async with asyncio.timeout(self.config.api_timeout_ms / 1000):
                response = await request.response()
                if response:
                    payload["status"] = response.status
                    payload["duration_ms"] = request.timing.get("responseEnd", -1)
                    headers = response.headers
                    if self.config.capture_headers:
                        payload["response_headers"] = headers
                    kind = headers.get("content-type", "").lower()
                    if not self.config.max_body_bytes:
                        payload["response_body"] = {"omitted": "disabled"}
                    elif (
                        kind
                        and "json" not in kind
                        and not kind.startswith("text/plain")
                    ):
                        payload["response_body"] = {
                            "omitted": "content type",
                            "content_type": kind,
                        }
                    elif response.status in {204, 304} or request.method == "HEAD":
                        payload["response_body"] = {
                            "omitted": "empty response",
                            "bytes": 0,
                        }
                    else:
                        # Content-Length can be absent (HTTP/2/chunked), compressed,
                        # or unavailable in Playwright's header view. Measure the
                        # actual decoded body; never reject JSON by that header.
                        payload["response_body"] = self.body_payload(
                            await response.body(), kind
                        )
        except (Error, TimeoutError):
            payload["response_body"] = {"omitted": "read failed or timed out"}
        self.emit_api(payload, frame, annotation)

    def on_sensor(self, source, data):
        if not self.recording and not self.stopping:
            return
        try:
            if data["type"] == "warning":
                self.emit(
                    "warning",
                    {
                        "operation": "HTML sensor",
                        "message": str(data["payload"]["message"])[:200],
                    },
                    page=source["page"],
                )
                return
            payload = data["payload"]
            if data["type"] != "html" or not isinstance(payload, dict):
                raise ValueError("Invalid HTML event")
            mode = payload.get("mode")
            changes = payload.get("changes", [])
            if (
                mode not in {"initial", "patch"}
                or not isinstance(changes, list)
                or len(changes) > self.config.max_changes_per_event
            ):
                raise ValueError("Invalid HTML changes")
            if mode == "initial" and (
                not isinstance(payload.get("html"), str)
                or len(payload["html"]) > self.config.max_html_chars
            ):
                raise ValueError("Oversized initial HTML")
            if mode == "patch" and "html" in payload:
                raise ValueError("Patches must not contain a full snapshot")
            for change in changes:
                if not isinstance(change, dict) or change.get("op") not in {
                    "add",
                    "context",
                    "remove",
                    "text",
                    "set_text",
                    "attribute",
                    "value",
                    "location",
                }:
                    raise ValueError("Invalid patch")
                if "html" in change and (
                    not isinstance(change["html"], str)
                    or len(change["html"]) > self.config.max_html_chars
                ):
                    raise ValueError("Oversized HTML fragment")
            if (
                not isinstance(payload.get("actions", []), list)
                or len(payload.get("actions", [])) > 20
            ):
                raise ValueError("Oversized action batch")
            frame = source["frame"]
            payload["parent_frame_id"] = (
                self.frame_id(frame.parent_frame) if frame.parent_frame else None
            )
            self.emit("html", payload, page=source["page"], frame=frame, source="dom")
        except (KeyError, ValueError, TypeError):
            self.emit("warning", {"operation": "invalid HTML sensor event skipped"})

    async def flush_html(self):
        for page in list(self.context.pages) if self.context else []:
            frames = page.frames if self.config.capture_frames else [page.main_frame]
            for frame in frames:
                try:
                    await asyncio.wait_for(
                        frame.evaluate("() => window.__recorderFlush?.()"), timeout=2
                    )
                except (Error, TimeoutError):
                    self.emit(
                        "warning",
                        {"operation": "HTML flush unavailable"},
                        page=page,
                        frame=frame,
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
        self.store.annotations.append(annotation.model_dump())
        self.emit("annotation.start", annotation.model_dump())
        return annotation

    async def end_annotation(self):
        if self.store and self.store.annotation_id:
            await self.flush_html()
            # Reserve room for the boundary even after a burst.
            await self.store.flush()
            event = self.emit("annotation.end", {"id": self.store.annotation_id})
            self.annotations[-1].end_seq = event.seq if event else self.store.seq
            self.store.annotations[-1] = self.annotations[-1].model_dump()
            self.store.annotation_id = None

    async def checkpoint(self):
        if not self.recording:
            raise RuntimeError("Start a session first")
        await self.flush_html()
        await self.store.flush()
        self.emit("checkpoint", {})

    async def stop(self, reason="user stopped"):
        async with self._stop_lock:
            if not self.store or self.store.closed:
                return
            self.stopping = True
            try:
                if self.store.annotation_id:
                    await self.end_annotation()
                else:
                    await self.flush_html()
                if self.context:
                    await self.context.close()
                # Never wait indefinitely on streaming API requests.
                for request, (payload, frame, annotation) in list(
                    self.requests.items()
                ):
                    payload["failure"] = "session ended before request completed"
                    self.emit_api(payload, frame, annotation)
                self.requests.clear()
                if self.tasks:
                    _, pending = await asyncio.wait(
                        self.tasks, timeout=self.config.api_timeout_ms / 1000 + 0.2
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
            except Error:
                pass  # Browser may have already disconnected.
            finally:
                try:
                    if self.autosave:
                        self.autosave.cancel()
                        await asyncio.gather(self.autosave, return_exceptions=True)
                    for task in self.tasks:
                        task.cancel()
                    if self.tasks:
                        await asyncio.gather(*self.tasks, return_exceptions=True)
                    try:
                        if self.browser:
                            await self.browser.close()
                    finally:
                        if self.playwright:
                            await self.playwright.stop()
                finally:
                    self.playwright = self.browser = self.context = None
                    try:
                        await self.store.finish(reason)
                    finally:
                        self.stopping = False
