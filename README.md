# Browser UI Recorder

A local Python 3.12+ tool for manually recording Chromium flows and preparing evidence for Playwright test generation. A small PySide6 window controls recording and annotations. No account, database, web server, or LLM connection is required.

## Install and run

With [uv](https://docs.astral.sh/uv/):

```bash
uv sync --locked --extra test
uv run playwright install chromium
uv run browser-ui-recorder
```

On Linux, Chromium and Qt need a graphical desktop and their system libraries. Playwright can install its browser libraries with `uv run playwright install --with-deps chromium`. For an existing Chromium installation:

```bash
uv run browser-ui-recorder --chromium /usr/bin/chromium --url https://example.com
```

Without uv, create a Python 3.12+ virtual environment, install `pip install -e '.[test]'`, run `playwright install chromium`, then `browser-ui-recorder`.

In this cloud checkout, dependencies are installed in `.venv` and `/usr/bin/chromium` is available. Run `.venv/bin/browser-ui-recorder --chromium /usr/bin/chromium` from `/workspace/myutil` in a graphical session. The cloud shell has no display; automated validation uses headless Chromium and Qt's offscreen backend.

## Record a flow

1. Enter a starting URL and click **Start Session**. A fresh Chromium window opens.
2. Enter an annotation name such as **Payment Case**. Optionally add a goal, expected result, and comma-separated tags, then click **Start Annotation**.
3. Use the browser normally, including frames and additional tabs. Click **Checkpoint** to capture a fresh DOM snapshot and observed page state.
4. Click **End Annotation**, then **Stop Session**. Closing the last tab, browser, or control window also finalizes the session when possible.
5. Inspect the displayed session directory. Give `derived/llm-context.json` to your test-generation workflow. The recorder does not send it anywhere or run generated tests.

Multiple sequential annotations are supported; annotations cannot overlap. Browsing outside annotations is retained as unannotated activity. The annotation covers all pages in the session. Starting another session creates a new directory and isolated browser context.

```text
sessions/session-<id>/
  manifest.json
  events.jsonl
  annotations.json
  artifacts/
    network/
    dom/
    screenshots/
  derived/llm-context.json
```

`events.jsonl` is the source of truth. Every event has a session-wide increasing `seq`, UTC timestamp, elapsed monotonic milliseconds, session ID, nullable page/frame/annotation IDs, source, type, and structured payload. Sequence is the observation order at the recorder; concurrent browser sources and response-body reads can arrive later than the actions that caused them. Network events share a `request_id`, and redirects link request IDs.

The writer flushes each event and fsyncs on finalization. Artifacts, annotations, and the manifest use atomic file replacement. After a process crash, complete JSONL lines remain usable; the manifest can remain `recording`. Rebuild context without opening the GUI:

```bash
uv run browser-ui-recorder --export sessions/session-<id>
```

The exporter skips a partial final JSONL line, preserves sequence numbers, and reports skipped lines. It does not relabel an interrupted recording as complete.

## Captured evidence

- Page creation/closure, navigation, history changes, redirects, popups, and frame lifecycle.
- Click, input, change, and submit actions with role/name, labels, test IDs, text, ID/name, frame path, visibility/enabled state, and CSS fallback.
- Requests, responses, status, timing, failure, and bounded text/JSON API bodies. WebSocket open/close/errors are metadata only.
- Console errors and uncaught page errors.
- rrweb initial snapshots and incremental DOM changes, stored as artifacts. The bundled, pinned `@rrweb/record` runs locally in each frame before page scripts; no runtime CDN access is required.
- Annotation boundaries, checkpoints, and bounded observed page state at annotation/session end.

The export groups ordered evidence by annotation. It includes semantic locator candidates, relevant document/API traffic, URL changes, errors, compact DOM mutation summaries, and final observed text/controls. Full rrweb snapshots and resource headers stay out of the export. Consecutive input events for the same target are collapsed when no intervening evidence would be lost.

## Redaction and limits

Create a JSON configuration file and pass `--config recorder-config.json`:

```json
{
  "extra_headers": ["X-Internal-Key"],
  "redact_selectors": ["#private-note", ".customer-secret"],
  "inline_body_bytes": 4096,
  "max_body_bytes": 1000000
}
```

Sensitive key names, credentials in URL userinfo/query parameters, password/token/cookie headers and fields, card-number patterns, CVV/CVC assignments, JWTs, and base64 blobs are redacted before file writes. Configured selectors are blocked in rrweb and masked in semantic actions and page-state capture. rrweb masks all input values; semantic events retain only values from non-sensitive controls. Inline scripts/styles and event-handler attributes are excluded or masked. Screenshots are intentionally not taken, to avoid a separate pixel-redaction problem.

Images, video, audio, fonts, SVG resources, JS/CSS bundles, multipart uploads, and HTML response bodies stay metadata-only. Only JSON and plain-text fetch/XHR responses with a known Content-Length within the limit are read. Larger allowed bodies are external artifacts; unknown-length, chunked, oversized, or unreadable bodies keep metadata and an omission reason. This bounds normal reads, though Playwright buffers a selected body in memory and the size is checked again after reading. Plain-text and URL redaction is heuristic; use additional selectors for app-specific sensitive content and synthetic accounts for payment flows. Arbitrary secrets in unlabelled free text cannot be reliably inferred.

Locator names approximate common accessible-name rules; verify uniqueness and semantics when generating tests. Cross-origin frame paths may include a placeholder; frame IDs, parent relationships, and frame navigation URLs supply the remaining context. Closed shadow DOM, canvas, screenshots, downloads, and WebSocket message contents are outside V1. Heavy pages still produce substantial DOM artifacts. A page can interfere with injected sensors; malformed events become recorder warnings, not a failed session.

CDP is not needed for the current workflow: Playwright exposes the required network timing/body and frame APIs. Checkpoints use rrweb snapshots rather than duplicating DOM capture.

## Development and validation

```bash
uv sync --locked --extra test
uv run pytest -q
```

Tests use the installed `chromium` executable when available, otherwise Playwright's bundled browser. Override it with `RECORDER_CHROMIUM=/path/to/chromium`. The GUI test uses `QT_QPA_PLATFORM=offscreen`; a desktop is not needed for automated checks.

The local HTTP fixture exists only in tests. It exercises a payment-like flow, API requests, dynamic DOM, redirects, nested frames, popups, errors, redaction, annotations, checkpoints, and context export. Other tests cover failed body reads, browser closure, restart, startup failure, partial-log recovery, and the Qt controls.

Code is under `app/`: event models, storage, redaction, browser launch, recorder orchestration, context export, GUI, and a small JavaScript sensor. `app/js/rrweb.NOTICE` and `rrweb.LICENSE` identify the bundled MIT-licensed dependency and verified npm tarball integrity.
