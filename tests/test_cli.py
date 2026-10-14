import json
import os
import subprocess
import sys

import pytest

from app.main import parse_args


def test_configuration_precedence_and_validation(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "device": "iPhone 13",
                "root_selector": "main",
                "api_allowlist": ["https://example.com/api/*"],
            }
        )
    )
    monkeypatch.setenv("RECORDER_CONFIG", str(path))
    monkeypatch.setenv("RECORDER_DEVICE", "Pixel 7")
    _, args, config = parse_args(
        ["--desktop", "--api", "https://other.example/orders/*", "--root", "#app"]
    )
    assert config.device is None
    assert config.root_selector == "#app"
    assert config.api_allowlist == ["https://other.example/orders/*"]
    assert not args.gui
    with pytest.raises(SystemExit):
        parse_args(["--api", "/api/*"])


def test_terminal_creates_json_and_exits(tmp_path, demo_server, chromium_path):
    command = [
        sys.executable,
        "-m",
        "app.main",
        "--headless",
        "--url",
        demo_server,
        "--output",
        str(tmp_path),
    ]
    if chromium_path:
        command.extend(["--chromium", chromium_path])
    completed = subprocess.run(
        command,
        input="annotate Terminal case\ncheckpoint\nend\nstop\n",
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env={k: v for k, v in os.environ.items() if not k.startswith("RECORDER_")},
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    data = json.loads(next(tmp_path.glob("session-*.json")).read_text())
    assert data["status"] == "complete"
    assert data["annotations"][0]["name"] == "Terminal case"
    assert any(e["type"] == "html" for e in data["events"])
