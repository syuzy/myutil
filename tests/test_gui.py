import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_gui_buttons_drive_recording(tmp_path, demo_server, chromium_path):
    if importlib.util.find_spec("PySide6") is None:
        pytest.skip("Install the gui extra to exercise Qt controls")
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("gui_smoke.py")),
            str(tmp_path),
            demo_server,
            chromium_path or "",
        ],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GUI passed" in result.stdout
