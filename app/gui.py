import asyncio

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
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
        await self.recorder.start(
            self.url.text().strip() or "about:blank",
            headless=self.headless,
            executable_path=self.executable_path,
        )
        self.status.setText("Recording — use the Chromium window normally")

    async def stop_session(self):
        await self.recorder.stop()
        self.status.setText("Session saved — compact LLM context exported")

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
        self.active.setText(
            "Active: " + self.recorder.annotations[-1].name
            if annotated
            else "No active annotation"
        )
        if store:
            actions = sum(v for k, v in store.counts.items() if k.startswith("action."))
            self.counters.setText(
                f"{store.seq} events · {actions} actions · {store.counts['network.request']} requests · {store.counts['dom']} DOM events"
            )
            self.output.setText(str(store.path))
            if store.closed and self.status.text().startswith("Recording"):
                self.status.setText(
                    "Browser closed — session saved and context exported"
                )

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
