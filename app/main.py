import argparse
import asyncio
import json
from pathlib import Path

from .redaction import Config


def main():
    parser = argparse.ArgumentParser(
        description="Record manual Chromium flows for Playwright test generation"
    )
    parser.add_argument("--output", type=Path, default=Path("sessions"))
    parser.add_argument("--url", default="about:blank")
    parser.add_argument(
        "--config", type=Path, help="JSON redaction and body-size settings"
    )
    parser.add_argument(
        "--chromium", help="Optional installed Chromium executable path"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="For automation/debugging; manual recording needs a visible browser",
    )
    parser.add_argument(
        "--export",
        type=Path,
        metavar="SESSION",
        help="Rebuild compact context from an existing session",
    )
    args = parser.parse_args()
    if args.export:
        from .context_export import export_context

        try:
            print(export_context(args.export))
        except (OSError, ValueError) as error:
            parser.error(
                f"Cannot export session ({type(error).__name__}); check the session path and recording files"
            )
        return
    try:
        config = Config.model_validate(
            json.loads(args.config.read_text()) if args.config else {}
        )
    except (ValueError, OSError) as error:
        parser.error(f"Cannot load recorder configuration ({type(error).__name__})")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .gui import ControlWindow
    from .recorder import Recorder

    application = QApplication([])
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    recorder = Recorder(args.output, config)
    window = ControlWindow(
        recorder,
        loop,
        url=args.url,
        headless=args.headless,
        executable_path=args.chromium,
    )
    # Qt owns the UI thread. Brief asyncio ticks keep Playwright responsive without
    # a second thread, cross-thread browser calls, or another event-loop dependency.
    timer = QTimer()

    def tick():
        loop.call_soon(loop.stop)
        loop.run_forever()

    timer.timeout.connect(tick)
    timer.start(10)
    window.show()
    try:
        application.exec()
    finally:
        timer.stop()
        loop.run_until_complete(recorder.stop("application exited"))
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == "__main__":
    main()
