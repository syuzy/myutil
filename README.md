# Lightweight Browser UI Recorder

Record meaningful UI state changes and selected APIs into **one JSON file** for an LLM to infer a Playwright test.

```bash
uv sync --locked
uv run playwright install chromium
uv run browser-ui-recorder --url https://example.com
```

Works with Python 3.12+ on macOS and Linux. Chromium is installed by Playwright; no OS-specific browser path is required. Terminal controls are the default. Qt is an optional extra.

```bash
# Phone emulation, scoped HTML, and only the API you care about
uv run browser-ui-recorder --mobile --url https://shop.example.com \
  --root main --api 'https://shop.example.com/api/orders/*'
```

Commands: `annotate Payment Case`, `end`, `checkpoint`, `status`, `stop`.

Each session produces `sessions/session-<id>.json` containing HTML states, associated semantic actions, annotations, URLs, device settings, and allowlisted API results. No separate artifacts or export step.

- A small MutationObserver batches HTML changes. Default minimum interval: **1.5 seconds**; unchanged snapshots are discarded.
- No rrweb, mousemove/scroll telemetry, console stream, WebSocket stream, or per-resource logs.
- No APIs are captured unless explicitly allowlisted. Traffic is observed without rerouting or blocking it.
- HTML traversal, payload sizes, pending API reads, event buffers, and writes are bounded. HTML truncation and dropped-event counters are explicit.
- Passwords, token fields, payment fields, and configured private selectors are masked before writing.

Optional control window:

```bash
uv sync --locked --extra gui
uv run browser-ui-recorder --gui
```

Read **[USAGE.md](USAGE.md)** for the macOS tutorial, all settings and environment variables, mobile profiles, performance tuning, JSON format, and recovery.

Development:

```bash
uv sync --locked --all-extras
uv run playwright install chromium
uv run pytest -q
```

Tests cover a payment flow, API filters, redaction, mobile emulation, mutation storms, large DOM budgets, single-file persistence/recovery, terminal controls, and the optional Qt window. GitHub Actions runs the suite on macOS and Linux. Local validation in this development environment uses Linux Chromium; macOS execution is covered by the CI job when it runs.
