"""Exercise the actual Qt/asyncio pump in its own main-thread process."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import app.gui
from app.config import Config
from app.main import run_gui

result = {}
OriginalWindow = app.gui.ControlWindow


class AutomatedWindow(OriginalWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.driver = self.loop.create_task(self.exercise())

    async def settled(self):
        async with asyncio.timeout(15):
            while self.busy:
                await asyncio.sleep(0.02)

    async def exercise(self):
        try:
            assert self.start.isEnabled() and not self.stop.isEnabled()
            self.device.setCurrentText("Pixel 7")
            self.start.click()
            await self.settled()
            assert self.recorder.recording and self.stop.isEnabled(), self.status.text()
            assert self.recorder.config.device == "Pixel 7"
            self.name.setText("GUI case")
            self.begin.click()
            await self.settled()
            assert self.end.isEnabled()
            await (
                self.recorder.context.pages[0]
                .get_by_label("Full name")
                .fill("GUI Customer")
            )
            self.checkpoint.click()
            await self.settled()
            self.end.click()
            await self.settled()
            self.stop.click()
            await self.settled()
            assert self.recorder.store.closed
            data = json.loads(self.recorder.store.path.read_text())
            assert data["annotations"][0]["name"] == "GUI case"
            assert "GUI Customer" in json.dumps(data)
            result["passed"] = True
        except Exception as error:  # noqa: BLE001 -- propagate across the Qt application boundary
            result["error"] = repr(error)
        finally:
            self.close()


app.gui.ControlWindow = AutomatedWindow
run_gui(
    SimpleNamespace(
        output=Path(sys.argv[1]),
        url=sys.argv[2],
        headless=True,
        chromium=sys.argv[3] or None,
    ),
    Config(dom_budget_ms=50),
)
assert result.get("passed"), result
print("GUI passed")
