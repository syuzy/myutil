import asyncio
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui import ControlWindow
from app.recorder import Recorder


async def test_gui_buttons_drive_recording(tmp_path, demo_server, chromium_path):
    application = QApplication.instance() or QApplication([])
    recorder = Recorder(tmp_path)
    window = ControlWindow(
        recorder,
        asyncio.get_running_loop(),
        url=demo_server,
        headless=True,
        executable_path=chromium_path,
    )
    window.show()

    async def settled():
        async with asyncio.timeout(15):
            while window.busy:
                application.processEvents()
                await asyncio.sleep(0.02)

    try:
        assert window.start.isEnabled() and not window.stop.isEnabled()
        window.start.click()
        await settled()
        assert recorder.recording and window.stop.isEnabled(), window.status.text()
        window.name.setText("GUI case")
        window.begin.click()
        await settled()
        assert window.end.isEnabled() and not window.begin.isEnabled()
        await recorder.context.pages[0].get_by_label("Full name").fill("GUI Customer")
        window.checkpoint.click()
        await settled()
        window.end.click()
        await settled()
        window.stop.click()
        await settled()
        assert recorder.store.closed
        assert window.start.isEnabled()
        result = json.loads(
            (recorder.store.path / "derived/llm-context.json").read_text()
        )
        assert result["annotations"][0]["name"] == "GUI case"
        assert "GUI Customer" in json.dumps(result)
    finally:
        await recorder.stop()
        window.close()
        async with asyncio.timeout(5):
            while not window.allow_close:
                application.processEvents()
                await asyncio.sleep(0.02)
