import asyncio

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ControlWindow(QMainWindow):
    def __init__(
        self, recorder, loop, *, url="about:blank", headless=False, executable_path=None
    ):
        super().__init__()
        self.recorder = recorder
        self.loop = loop
        self.headless = headless
        self.executable_path = executable_path
        self.tasks = set()
        self.busy = False
        self.closing = False
        self.allow_close = False
        self.setWindowTitle("Browser UI Recorder")
        self.resize(530, 360)
        container = QWidget()
        self.setCentralWidget(container)
        layout = QVBoxLayout(container)
        self.status = QLabel("Ready — start a session to open Chromium")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        form = QFormLayout()
        self.url = QLineEdit(url)
        self.device = QComboBox()
        self.device.addItems(["Desktop", "Pixel 7", "iPhone 13"])
        if recorder.config.device and self.device.findText(recorder.config.device) < 0:
            self.device.addItem(recorder.config.device)
        self.device.setCurrentText(recorder.config.device or "Desktop")
        self.apis = QLineEdit(", ".join(recorder.config.api_allowlist))
        self.apis.setPlaceholderText("No APIs captured; e.g. https://app.example/api/*")
        form.addRow("Device", self.device)
        form.addRow("API allowlist", self.apis)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Payment Case")
        self.goal = QLineEdit()
        self.expected = QLineEdit()
        self.tags = QLineEdit()
        for title, field in [
            ("Starting URL", self.url),
            ("Annotation", self.name),
            ("Goal (optional)", self.goal),
            ("Expected result (optional)", self.expected),
            ("Tags (comma separated)", self.tags),
        ]:
            form.addRow(title, field)
        layout.addLayout(form)
        self.start = QPushButton("Start Session")
        self.stop = QPushButton("Stop Session")
        self.begin = QPushButton("Start Annotation")
        self.end = QPushButton("End Annotation")
        self.checkpoint = QPushButton("Checkpoint")
        for buttons in [
            (self.start, self.stop),
            (self.begin, self.end, self.checkpoint),
        ]:
            row = QHBoxLayout()
            for button in buttons:
                row.addWidget(button)
            layout.addLayout(row)
        self.active = QLabel("No active annotation")
        self.counters = QLabel("0 events")
        self.output = QLabel()
        self.output.setWordWrap(True)
        layout.addWidget(self.active)
        layout.addWidget(self.counters)
        layout.addWidget(self.output)
        self.start.clicked.connect(lambda: self.submit(self.start_session()))
        self.stop.clicked.connect(lambda: self.submit(self.stop_session()))
        self.begin.clicked.connect(lambda: self.submit(self.begin_annotation()))
        self.end.clicked.connect(lambda: self.submit(self.recorder.end_annotation()))
        self.checkpoint.clicked.connect(lambda: self.submit(self.recorder.checkpoint()))
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(200)
        self.refresh()

    def submit(self, coroutine):
        if self.busy:
            coroutine.close()
            return
        self.busy = True
        self.refresh()

        async def run():
            try:
                await coroutine
            except Exception as error:  # noqa: BLE001 -- keep Qt alive at the UI boundary
                # Playwright errors can embed website URLs or response content.
                self.status.setText(
                    f"Operation failed ({type(error).__name__}). Check the URL, browser installation, and display."
                )
            finally:
                self.busy = False
                self.refresh()

        task = self.loop.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def start_session(self):
        from .config import Config

        settings = self.recorder.config.model_dump()
        settings["device"] = (
            None
            if self.device.currentText() == "Desktop"
            else self.device.currentText()
        )
        settings["api_allowlist"] = [
            p.strip() for p in self.apis.text().split(",") if p.strip()
        ]
        self.recorder.config = Config.model_validate(settings)
        await self.recorder.start(
            self.url.text().strip() or "about:blank",
            headless=self.headless,
            executable_path=self.executable_path,
        )
        self.status.setText("Recording — use the Chromium window normally")

    async def stop_session(self):
        await self.recorder.stop()
        self.status.setText("Session saved — one JSON file")

    async def begin_annotation(self):
        self.recorder.start_annotation(
            self.name.text(),
            goal=self.goal.text() or None,
            expected_result=self.expected.text() or None,
            tags=[tag.strip() for tag in self.tags.text().split(",") if tag.strip()],
        )

    def refresh(self):
        recording = self.recorder.recording
        store = self.recorder.store
        annotated = bool(store and store.annotation_id)
        self.start.setEnabled(
            not recording and not self.busy and not self.recorder.stopping
        )
        self.stop.setEnabled(recording and not self.busy)
        self.begin.setEnabled(recording and not annotated and not self.busy)
        self.end.setEnabled(recording and annotated and not self.busy)
        self.checkpoint.setEnabled(recording and not self.busy)
        self.url.setEnabled(not recording and not self.busy)
        self.device.setEnabled(not recording and not self.busy)
        self.apis.setEnabled(not recording and not self.busy)
        self.active.setText(
            "Active: " + self.recorder.annotations[-1].name
            if annotated
            else "No active annotation"
        )
        if store:
            self.counters.setText(
                f"{store.counts['html']} HTML states · {store.counts['api']} APIs · "
                f"{store.dropped_events + store.dropped_apis} dropped at capacity"
            )
            self.output.setText(str(store.path))
            if store.closed and self.status.text().startswith("Recording"):
                self.status.setText("Browser closed — JSON recording saved")

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        self.closing = True

        async def close():
            try:
                if self.tasks:
                    await asyncio.gather(*list(self.tasks), return_exceptions=True)
                await self.recorder.stop("control window closed")
            finally:
                self.allow_close = True
                self.close()

        self.loop.create_task(close())
