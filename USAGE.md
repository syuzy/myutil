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
| `end` | Flush copied DOM changes, then end the annotation. |
| `checkpoint` | Drain observed DOM changes and flush pending records. It does not redump the page. |
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

JSON, URL-encoded request bodies, and plain text are included only within `max_body_bytes`. JSON/plain-text responses are read even when Content-Length is missing, chunked, or compressed. When Content-Type is missing, the body must parse as JSON. The limit applies to **actual decoded bytes before redaction**, not the header or compressed transfer size. Included bodies have `data` and `bytes`; a real size violation has `omitted: "size limit"`, `bytes`, and `limit`. Disabled capture, unsupported types, empty responses, parse failures, and read failures have distinct reasons. No body is written to another file. Playwright buffers a completed selected body before the size check; recording size, concurrency, and read time are bounded, but this is not a streaming memory limit. Set `max_body_bytes: 0` for status/timing only.

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

DOM changes are copied **when the MutationObserver callback runs**, including nodes already removed by the time the callback arrives. Text/attribute old values recover same-task transitions. Transport batching does not delay serialization or discard an add followed by a remove.

| Setting | Default | Meaning |
| --- | --- | --- |
| `root_selector` | `"body"` | Capture the first matching subtree. Prefer your checkout/form container on a huge site. A missing root waits until it appears. |
| `ignore_selectors` | `[]` | Exclude tickers, ads, clocks, or other noisy subtrees. Scripts/styles/SVG/canvas/templates/metadata are always omitted. |
| `dom_flush_interval_ms` | `50` | Transport copied changes in short batches (10–1000 ms). This does not postpone capture. |
| `max_changes_per_event` | `200` | Maximum delta operations in one event (10–1000). |
| `max_pending_changes` | `1000` | Maximum queued browser-side operations (50–5000); a separate character cap bounds the queue too. |
| `max_mutation_records` | `5000` | Maximum source records inspected per observer callback (100–50000). |
| `max_transition_history` | `16` | Retain this many intermediate text/attribute/value states before retaining only the latest one (2–100). Additional omitted transitions are counted by `coalesced`. |
| `max_html_chars` | `80000` | Maximum initial HTML/individual fragment characters and per-callback added HTML budget (500–500000). Scalar patch values are capped at 4000 characters and flagged if truncated. |
| `max_dom_nodes` | `3000` | Maximum nodes visited for an initial baseline or additions in one callback (20–20000). |
| `dom_budget_ms` | `12` | Traversal/callback time budget (2–100 ms). A single DOM operation can exceed it; this is a best-effort bound. |
| `watched_attributes` | See below | Attributes whose changes create deltas. Child/text changes are always observed. |
| `capture_frames` | `false` | Opt into iframe HTML capture. Each frame has its own sensor, IDs, and budget. |
| `record_actions` | `true` | Attach bounded click/input/change/submit context to the next delta batch. Input properties are observed even when action metadata is disabled. |
| `record_errors` | `false` | Opt into uncaught page errors. Console and WebSocket telemetry remain disabled. |

Default watched attributes:

```json
["class", "style", "hidden", "open", "disabled", "checked", "selected", "value", "href",
 "aria-hidden", "aria-expanded", "aria-checked", "aria-selected", "aria-disabled"]
```

Class changes capture class-based loading/visibility states. Style is reduced to `display`, `visibility`, and `content-visibility`; animated transforms, positions, opacity, and other geometry are ignored. Exclude animation-only subtrees with `ignore_selectors`, or remove `class` from `watched_attributes` if appropriate for your application. Pure CSS hover/animation without watched DOM changes, layout, scroll, images, canvas, and closed shadow DOM are not captured.

The legacy `debounce_ms`, `min_snapshot_interval_ms`, and `max_wait_ms` settings remain accepted for existing config files but **no longer delay or control DOM capture**. Use `dom_flush_interval_ms` for transport frequency. Checkpoints and annotation/session end drain pending changes instead of serializing the entire page again.

At most 20 user actions accompany a batch; overflow is counted in `dropped_actions`. Full-page navigation can destroy a buffer before delivery. `truncated`, `dropped_changes`, or `coalesced` indicate incomplete detail. Do not infer omitted content was absent. Later edits below a truncated baseline can include local `context` fragments, which identify existing nodes without redumping the root. Portals/modals outside the configured root are excluded.

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

Version **3** stores one minified baseline per document/root instance and subsequent deltas. Metadata, annotations, actions, device settings, and allowlisted API bodies remain in the same file. There is no separate export step.

```json
{
  "schema_version": 3,
  "events": [
    {"type": "html", "payload": {
      "mode": "initial", "document_id": "doc-a", "root_id": "n1",
      "html": "<main data-recorder-node=\"n1\"></main>", "changes": []
    }},
    {"type": "html", "payload": {
      "mode": "patch", "document_id": "doc-a", "changes": [
        {"op": "add", "node_id": "n2", "parent_id": "n1", "html": "<div data-recorder-node=\"n2\">Loading</div>"},
        {"op": "remove", "node_id": "n2", "parent_id": "n1"}
      ]
    }}
  ]
}
```

The abbreviated example omits event timestamps, IDs, config, annotation metadata, counts, and completion status. Each real event retains those shared fields. Only an `initial` event has top-level HTML; a `patch` contains only changes. A replaced root establishes a new baseline. Export-only element IDs and text markers (`<!--r:n3-->`) identify delta targets; they do not exist in the real browser DOM.

Read **[AGENT_GUIDE.md](AGENT_GUIDE.md)** for a short, framework- and language-agnostic explanation of each operation and how to infer automation tests. It covers transient loading states, intermediate histories, semantic locators, API correlation, and completeness flags.

## 8. Performance tuning and recovery

For a busy checkout:

```json
{
  "root_selector": "#checkout",
  "ignore_selectors": [".live-chat", ".ticker", "[data-ad-slot]"],
  "api_allowlist": ["https://shop.example.com/api/checkout*"],
  "dom_flush_interval_ms": 100,
  "max_transition_history": 16,
  "max_html_chars": 40000,
  "capture_frames": false
}
```

Check `truncated`, `dropped_events`, `dropped_apis`, and HTML batch `dropped_changes`/`dropped_actions` before trusting completeness. Scope capture before increasing limits. The test suite exercises 20,000 DOM mutations, ignored animation/scroll events, and capped concurrent APIs; those are regression checks, not a guarantee for every production site.

The JSON is valid after each completed batch flush. To avoid repeatedly rewriting a growing file, the writer appends events in place and rewrites only the small trailing metadata section. A reader can see an incomplete tail during a write; read after a flush or after stopping. A crash can lose buffered events or interrupt the tail. Recover complete records without changing the original:

```bash
uv run browser-ui-recorder --repair sessions/session-abc.json
```

This creates `session-abc.recovered.json` with `status: "recovered"`. It cannot restore events that never reached disk. Recovery loads the file in memory; normal recording does not.

## Migration from 0.1

Version 0.2 deliberately removes rrweb, artifact directories, JSONL, the derived export, and unconditional network/console/WebSocket recording. Existing v1 folders are left untouched; the repair command is not a v1 converter. `--export` and `inline_body_bytes` no longer exist. The launcher defaults to terminal controls; add `--gui` and install the `gui` extra for the window. Mobile and API configuration are new. All new sessions use the single-file v2 format.

## Migration from 0.2

Version 0.3 changes `schema_version` to 3. HTML now has `mode`, `document_id`, and `changes`; only initial baselines contain top-level `html`. Consumers must understand deltas using AGENT_GUIDE.md. Legacy snapshot timing parameters remain accepted but have no capture effect. New defaults watch class and visibility-related style changes. API bodies no longer require Content-Length; limits use actual decoded bytes. The repair command handles both v2 and v3 single-file recordings.
