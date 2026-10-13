"""Deterministic, bounded context; raw JSONL remains the source of truth."""

import json
from pathlib import Path

from .events import Event


def locator_candidates(locator):
    candidates = []
    if locator.get("role") and locator.get("accessible_name"):
        candidates.append(
            {
                "by": "role",
                "role": locator["role"],
                "name": locator["accessible_name"],
                "exact": True,
            }
        )
    if locator.get("label"):
        candidates.append({"by": "label", "value": locator["label"], "exact": True})
    if locator.get("test_id"):
        candidates.append({"by": "test_id", "value": locator["test_id"]})
    if locator.get("text") and locator["text"] != "[REDACTED]":
        candidates.append({"by": "text", "value": locator["text"], "exact": True})
    if locator.get("css"):
        candidates.append({"by": "css", "value": locator["css"]})
    return candidates


def read_artifact(session, relative):
    target = (session / relative).resolve()
    if not target.is_relative_to(session.resolve() / "artifacts"):
        return {}
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def dom_summary(session, payload):
    if payload.get("rrweb_type") != 3 or payload.get("source") not in (0, 5):
        return None
    data = read_artifact(session, payload.get("artifact", "")).get("data", {})
    if not isinstance(data, dict):
        return None
    result = {"changes": payload.get("changes"), "texts": [], "elements": []}

    def visit(node, depth=0):
        if not isinstance(node, dict) or depth > 30:
            return
        content = node.get("textContent", "")
        if len(result["texts"]) < 12 and isinstance(content, str) and content.strip():
            result["texts"].append(content[:300])
        if len(result["elements"]) < 12 and node.get("tagName"):
            attrs = node.get("attributes", {})
            if not isinstance(attrs, dict):
                attrs = {}
            result["elements"].append(
                {
                    "tag": node["tagName"],
                    "attributes": {
                        k: v
                        for k, v in attrs.items()
                        if k in {"role", "aria-label", "data-testid", "id", "class"}
                    },
                }
            )
        children = node.get("childNodes", [])
        if isinstance(children, list):
            for child in children[:100]:
                visit(child, depth + 1)

    for key in ("adds", "texts", "attributes"):
        if not isinstance(data.get(key, []), list) or not all(
            isinstance(e, dict) for e in data.get(key, [])
        ):
            return None
    for entry in data.get("adds", [])[:30]:
        visit(entry.get("node", {}))
    result["texts"].extend(
        str(entry.get("value", ""))[:300] for entry in data.get("texts", [])[:12]
    )
    result["attributes"] = data.get("attributes", [])[:12]
    if data.get("source") == 5:
        result["input_state"] = {
            "id": data.get("id"),
            "text": data.get("text"),
            "isChecked": data.get("isChecked"),
        }
    return result


def export_context(session: Path):
    session = Path(session)
    annotations = json.loads((session / "annotations.json").read_text(encoding="utf-8"))
    groups = {
        a["id"]: {**a, "timeline": [], "final_state": {}, "initial_urls": {}}
        for a in annotations
    }
    unannotated = {
        "name": "Unannotated activity",
        "timeline": [],
        "final_state": {},
        "initial_urls": {},
    }
    urls = {}
    warnings = 0
    relevant_requests = set()
    with (session / "events.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = Event.model_validate_json(line).model_dump(mode="json")
                kind, payload = event["type"], event["payload"]
            except (ValueError, KeyError):
                warnings += 1
                continue  # A crash may leave one incomplete final JSONL line.
            group = groups.get(event.get("annotation_id"), unannotated)
            page_id = event.get("page_id")
            if kind == "annotation.start":
                group["initial_urls"] = dict(urls)
            if kind in {"navigation", "location"}:
                urls[f"{page_id}/{event.get('frame_id')}"] = payload.get("url")
            if kind == "page.state":
                group["final_state"][page_id] = payload
                continue
            item = {
                "seq": event["seq"],
                "elapsed_ms": round(event["elapsed_ms"], 1),
                "type": kind,
                "page_id": page_id,
                "frame_id": event.get("frame_id"),
            }
            if kind.startswith("action."):
                item["payload"] = payload
                item["locator_candidates"] = locator_candidates(
                    payload.get("locator", {})
                )
                # Collapse consecutive keystrokes for the same field into a fill action.
                timeline = group["timeline"]
                if (
                    kind == "action.input"
                    and timeline
                    and timeline[-1]["type"] == kind
                    and timeline[-1].get("frame_id") == item["frame_id"]
                    and timeline[-1]["payload"].get("locator") == payload.get("locator")
                ):
                    item["first_seq"] = timeline[-1].get(
                        "first_seq", timeline[-1]["seq"]
                    )
                    timeline[-1] = item
                    continue
            elif kind.startswith("network."):
                if kind == "network.request":
                    if payload.get("resource_type") not in {"document", "xhr", "fetch"}:
                        continue
                    relevant_requests.add(payload.get("request_id"))
                if (
                    kind != "network.redirect"
                    and payload.get("request_id") not in relevant_requests
                ):
                    continue
                # Headers and bulky response payloads add little to generated tests.
                item["payload"] = {k: v for k, v in payload.items() if k != "headers"}
                if "body_artifact" in payload:
                    body = read_artifact(session, payload["body_artifact"]).get("body")
                    item["payload"]["body_excerpt"] = json.dumps(
                        body, ensure_ascii=False
                    )[:2000]
                elif "body" in payload:
                    serialized = json.dumps(payload["body"], ensure_ascii=False)
                    if len(serialized) > 2000:
                        item["payload"]["body"] = {
                            "excerpt": serialized[:2000],
                            "truncated": True,
                        }
            elif kind == "dom":
                summary = dom_summary(session, payload)
                if not summary:
                    continue
                item["payload"] = summary
            elif kind in {
                "navigation",
                "location",
                "console.error",
                "page.error",
                "page.popup",
                "frame.attached",
                "frame.detached",
                "checkpoint",
                "annotation.start",
                "annotation.end",
                "recorder.warning",
                "sensor.error",
                "network.redirect",
                "websocket.error",
            }:
                item["payload"] = payload
            else:
                continue
            group["timeline"].append(item)
    result = {
        "schema_version": 1,
        "session_id": session.name.removeprefix("session-"),
        "guidance": "Use role, label, then test ID candidates; verify uniqueness. Values marked [REDACTED] need test fixtures. DOM entries are ordered between actions. No screenshots or raw DOM snapshots included.",
        "annotations": list(groups.values()),
        "unannotated": unannotated,
        "incomplete_lines_skipped": warnings,
    }
    target = session / "derived" / "llm-context.json"
    target.parent.mkdir(exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    return target
