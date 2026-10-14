# Usage tutorial

## 1. Install on macOS

Use Python 3.12 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/). Both Apple Silicon and Intel Macs use the same commands on macOS releases supported by the installed Playwright version.

```bash
git clone https://github.com/syuzy/myutil.git
cd myutil
uv sync --locked
uv run playwright install chromium
uv run browser-ui-recorder --url https://example.com
```

Playwright downloads the matching Chromium build for your machine. This recorder does not use Linux-only browser paths, signals, or X11 libraries. On macOS, launch from Terminal in your normal logged-in desktop session. Mobile mode is Chromium emulation; it is not Safari, an iOS simulator, or a connected device.

Without uv:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m playwright install chromium
browser-ui-recorder --url https://example.com
```

On Linux, use the same uv commands. If Chromium needs system libraries, run `uv run playwright install --with-deps chromium`. A display is required for manual browsing. Headless mode is for automated/debug sessions, not manual use.

## 2. Record your first case

1. Start the command above. Chromium opens a clean, isolated browser context.
2. In the terminal, enter `annotate Payment Case`.
3. Use Chromium normally. Fill fields, press buttons, and wait for the resulting UI.
4. Enter `end` to end the annotation. You can start another annotation in the same session.
5. Enter `stop`. The terminal prints the path to the **single JSON file**.
6. Give that file to your LLM with your test-generation instructions. Nothing is uploaded automatically.

| Terminal command | Effect |
| --- | --- |
| `annotate NAME` | Start an annotation covering all captured pages. End the current annotation first. |
| `end` | Flush changed HTML, then end the annotation. |
| `checkpoint` | Capture current HTML if it changed and flush pending records. It does not duplicate unchanged HTML. |
| `status` | Print counts of captured HTML states, APIs, and other session events. |
| `stop` / `quit` / `exit` | Finalize the JSON and close the recorder's Chromium instance. |

Closing the last browser tab also finalizes recording. Ctrl-C or EOF in the terminal attempts a clean shutdown. Another session creates another JSON file. Recordings are ignored by Git.

## 3. Allowlist only useful APIs

By default, **no network requests are recorded**. Add full URL patterns for your application APIs:

```bash
uv run browser-ui-recorder --url https://shop.example.com \
  --api 'https://shop.example.com/api/orders/*' \
  --api 'https://payments.example.com/v1/checkout'
```

Quote patterns so the shell does not expand `*`. Matching uses case-sensitive shell-style globs against `scheme://host[:port]/path`, excluding query strings and fragments. `*` can span slashes. `/orders/*` does not include `/orders` itself; add both if needed. Use a narrow host/path rather than a pattern that matches your entire site.

Only `fetch`/XHR calls matching both the URL allowlist and `api_methods` are eligible. Analytics, images, scripts, documents, fonts, sockets, and unmatched API calls are omitted. One selected API produces one combined result containing method, URL, status/failure, start time, duration, and eligible request/response bodies. It retains the annotation active when the request began, even if its response arrives later.

This is passive observation, not a proxy or Playwright route interceptor: it does not modify, block, or replay website traffic and does not disable browser caching. The allowlist controls **recording**, not browser Internet access.

JSON, URL-encoded request bodies, and plain text are included only within `max_body_bytes`. Responses must be JSON/plain text with a known acceptable Content-Length. Unknown/chunked, binary, large, or unreadable bodies have an explicit omission reason. No body is written to another file. Playwright buffers a selected body before the second size check; a compressed response can expand beyond its advertised length. Set `max_body_bytes: 0` for status/timing only.

## 4. Mobile browsing

```bash
# Pixel 7: mobile viewport, touch, device scale, and user agent
uv run browser-ui-recorder --mobile --url https://example.com

# Select a named device profile
uv run browser-ui-recorder --device 'iPhone 13' --url https://example.com

# Inspect installed Playwright mobile profiles
uv run browser-ui-recorder --list-devices

# Override a mobile profile in configuration/environment
uv run browser-ui-recorder --desktop --url https://example.com
```

The browser engine remains Chromium for every profile, including iPhone profiles. This checks responsive/touch behavior, not Safari compatibility. The JSON records the selected device so an LLM can reproduce the emulation. Desktop uses the configurable viewport; named devices use their own profile dimensions.

## 5. Optional small control window

```bash
uv sync --locked --extra gui
uv run browser-ui-recorder --gui --url https://example.com
```

Or `python -m pip install -e '.[gui]'` without uv. The optional dependency is **PySide6-Essentials**, not the full Qt Addons/WebEngine distribution. Base installation needs only Playwright and Pydantic plus their dependencies.

Select Desktop/Pixel 7/iPhone 13, enter API URL patterns separated by commas, and start a session. A custom `--device` profile is also available in the selector. The window has annotations, optional goal/expected-result/tags, checkpoint, stop, and compact counters. Device and API changes apply before the next session starts. All other settings come from your configuration file. The GUI runs on the main thread, as required on macOS.

## 6. Configuration file

Copy [examples/recorder-config.json](examples/recorder-config.json), replace the example API host with yours, then run:

```bash
uv run browser-ui-recorder --config recorder-config.json --url https://your-site.example
```

Defaults intentionally capture less. Unknown settings are rejected to catch typos. Configuration precedence is **CLI flags > RECORDER_* environment variables > JSON config > defaults**. Repeatable `--api` flags replace, rather than extend, the configured allowlist. `--desktop` overrides a configured or environment-provided mobile device.

### HTML and performance settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `root_selector` | `"body"` | Capture only this CSS-selected subtree. Prefer your checkout/form container on a huge site. Only the first match is used; a missing root waits until it appears. |
| `ignore_selectors` | `[]` | Exclude tickers, ads, clocks, or other noisy subtrees. Scripts/styles/SVG/canvas/templates/metadata are always omitted. |
| `debounce_ms` | `400` | Wait briefly for related changes to settle. |
| `min_snapshot_interval_ms` | `1500` | Minimum spacing between automatic snapshots per document/frame. |
| `max_wait_ms` | `3000` | Flush sustained change bursts without waiting forever for quiet. Must be at least both intervals above. |
| `max_html_chars` | `80000` | Maximum characters in each sanitized HTML snapshot (500–500000). |
| `max_dom_nodes` | `3000` | Maximum nodes visited per snapshot (20–20000). |
| `dom_budget_ms` | `12` | Traversal time budget (2–100 ms). A single DOM operation can exceed the budget; this is a best-effort bound, not a hard browser scheduling guarantee. |
| `watched_attributes` | See below | Attribute changes that schedule a snapshot. Child/text changes are always observed. |
| `capture_frames` | `false` | Opt into iframe HTML capture. This adds a sensor and per-frame budget to each frame, including third-party frames. |
| `record_actions` | `true` | Attach bounded click/input/change/submit context to the next changed snapshot. Inputs are coalesced; no standalone keystroke stream. |
| `record_errors` | `false` | Opt into uncaught page errors. Console and WebSocket telemetry remain disabled. |

Default watched attributes:

```json
["hidden", "open", "disabled", "checked", "selected", "value", "href",
 "aria-hidden", "aria-expanded", "aria-checked", "aria-selected", "aria-disabled"]
```

Style/class animation is ignored by default. If your UI relies on class-only state changes, add `"class"` to `watched_attributes`; if necessary add `"style"` too. The configured attributes are also included in the sanitized HTML. This trades additional capture work for evidence of visual state. Pure CSS hover/animation, layout, scroll, images, canvas, and closed shadow DOM are not captured. URL changes can produce a snapshot even when the sanitized HTML is identical, so navigation context is preserved.

Actions accompany changed HTML/form state, not every click. A click with no observed state change creates no record by itself. At most 20 actions are held between snapshots; excess actions are counted in `dropped_actions`. Full-page navigation can destroy an action buffer before it reaches Python; the before/after HTML and URLs remain the primary evidence. Snapshot `truncated: true` means a size/node/depth/time limit was reached; do not infer that omitted page content was absent. Large or busy pages benefit most from a narrower `root_selector`. Portals/modals outside that root will be excluded.

### API, storage, redaction, and browser settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `api_allowlist` | `[]` | Absolute HTTP(S) URL globs. Empty disables network listeners entirely. |
| `api_methods` | `["GET","POST","PUT","PATCH","DELETE"]` | Allowed methods; normalized to uppercase. |
| `max_body_bytes` | `16384` | Per-body read limit (0–1000000); 0 omits bodies. |
| `capture_headers` | `false` | Include redacted request/response headers for selected APIs. |
| `max_pending_apis` | `8` | Bound simultaneous selected requests/body reads (1–64). Excess requests increment `dropped_apis`. Long-running requests occupy slots until completion or session end. |
| `api_timeout_ms` | `3000` | Timeout for reading a completed API response (100–30000). |
| `extra_headers` | `[]` | Additional header names to redact, case-insensitively. |
| `redact_selectors` | `[]` | CSS selectors to mask in HTML, actions, and field values. Invalid selectors disable the HTML sensor with a warning; they are never silently ignored. |
| `flush_interval_ms` | `1000` | Batch disk writes at this interval (100–30000). Writes run outside the browser/UI event loop. |
| `max_pending_events` | `100` | Bounded in-memory event buffer (10–1000). Overflow increments `dropped_events`; recording does not accumulate an unbounded backlog. |
| `device` | `null` | Desktop, or a named Playwright mobile profile. |
| `viewport_width` | `1280` | Desktop viewport width (320–3840). |
| `viewport_height` | `800` | Desktop viewport height (320–3840). |
| `navigation_timeout_ms` | `30000` | Startup navigation timeout (1000–120000). A timeout warns but leaves Chromium usable. Startup waits for DOMContentLoaded, not every resource or network idle. |

Passwords, payment fields, secret key names, auth/cookie headers, sensitive URL parameters, recognizable token/card strings, and configured selectors are masked before disk writes. HTML omits scripts, event handlers, arbitrary data attributes, and resource bodies. Redaction remains heuristic for secrets in unlabelled prose; use synthetic test accounts and add selectors for app-specific sensitive regions. Session files are created with private owner permissions.

### CLI flags and environment variables

| CLI | Environment equivalent | Notes |
| --- | --- | --- |
| `--config PATH` | `RECORDER_CONFIG` | JSON configuration path. |
| `--output DIR` | `RECORDER_OUTPUT` | Destination directory; default `sessions`. |
| `--chromium PATH` | `RECORDER_CHROMIUM` | Optional Chrome/Chromium executable. Normally omit this to use Playwright's supported browser. |
| `--device NAME` | `RECORDER_DEVICE` | Named device profile. |
| `--mobile` / `--desktop` | — | Pixel 7 shortcut / desktop override. |
| `--url URL` | — | Starting URL; default `about:blank`. |
| `--api URL_GLOB` | — | Repeat for each allowed API pattern. |
| `--root SELECTOR` | — | Override the HTML capture root. |
| `--gui` | — | Use optional Qt controls. |
| `--headless` | — | No visible browser; useful for automation. |
| `--list-devices` | — | List available mobile profiles and exit. |
| `--repair FILE` | — | Recover complete events from a damaged v2 JSON recording. |

macOS environment-variable example:

```bash
export RECORDER_CONFIG="$PWD/recorder-config.json"
export RECORDER_OUTPUT="$PWD/sessions"
export RECORDER_DEVICE='Pixel 7'
uv run browser-ui-recorder --url https://example.com
```

Optional system Chrome on macOS:

```bash
uv run browser-ui-recorder \
  --chromium '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  --url https://example.com
```

System browser versions can differ from Playwright's tested build. Prefer the installed Playwright Chromium unless you need a particular system browser.

## 7. Read the single JSON file

The document is shaped like:

```json
{
  "schema_version": 2,
  "session_id": "...",
  "started_at": "...",
  "browser": {"engine": "chromium", "device": "Pixel 7", "mobile_emulation": true},
  "config": {},
  "guidance": "...",
  "events": [
    {
      "seq": 4, "timestamp": "...", "elapsed_ms": 1200,
      "session_id": "...", "page_id": "page-1", "frame_id": "frame-1",
      "annotation_id": "...", "source": "dom", "type": "html",
      "payload": {"url": "https://example.com/checkout", "title": "Checkout",
                  "html": "<main>...</main>", "actions": [], "truncated": false}
    }
  ],
  "status": "complete",
  "annotations": [],
  "counts": {},
  "dropped_events": 0,
  "dropped_apis": 0
}
```

HTML is stored directly, not as external file references. Actions contain semantic locator data. API results contain their eligible bodies directly. Annotation spans use event sequence numbers. A frame ID identifies a Playwright frame and persists across its navigations; `parent_frame_id` identifies nested frames when enabled. `seq` is observation order, not causal network order; selected API events include their request start time and original annotation.

For generation, tell the LLM to read the header/device settings, use the last HTML state before an annotation as its initial state, then follow that annotation's ordered HTML/actions/API events. The last HTML state for each page/frame is the final observed state. Ask for semantic role/label/test-ID locators, verify locator uniqueness, and replace `[REDACTED]` with test fixtures. No extra export command is needed.

## 8. Performance tuning and recovery

For a busy checkout:

```json
{
  "root_selector": "#checkout",
  "ignore_selectors": [".live-chat", ".ticker", "[data-ad-slot]"],
  "api_allowlist": ["https://shop.example.com/api/checkout*"],
  "min_snapshot_interval_ms": 2000,
  "max_wait_ms": 4000,
  "max_html_chars": 40000,
  "capture_frames": false
}
```

Check `truncated`, `dropped_events`, `dropped_apis`, and snapshot `dropped_actions` before trusting completeness. Scope capture before increasing limits. The test suite exercises 20,000 DOM mutations, ignored animation/scroll events, and capped concurrent APIs; those are regression checks, not a guarantee for every production site.

The JSON is valid after each completed batch flush. To avoid repeatedly rewriting a growing file, the writer appends events in place and rewrites only the small trailing metadata section. A reader can see an incomplete tail during a write; read after a flush or after stopping. A crash can lose buffered events or interrupt the tail. Recover complete records without changing the original:

```bash
uv run browser-ui-recorder --repair sessions/session-abc.json
```

This creates `session-abc.recovered.json` with `status: "recovered"`. It cannot restore events that never reached disk. Recovery loads the file in memory; normal recording does not.

## Migration from 0.1

Version 0.2 deliberately removes rrweb, artifact directories, JSONL, the derived export, and unconditional network/console/WebSocket recording. Existing v1 folders are left untouched; the v2 repair command is not a v1 converter. `--export` and `inline_body_bytes` no longer exist. The launcher defaults to terminal controls; add `--gui` and install the `gui` extra for the window. Mobile and API configuration are new. All new sessions use the single-file v2 format.
