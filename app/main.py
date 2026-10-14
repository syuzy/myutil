import argparse
import asyncio
import json
import os
from pathlib import Path

from .config import Config


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Lightweight HTML-change recorder; one JSON file per session"
    )
    parser.add_argument(
        "--config", type=Path, default=os.environ.get("RECORDER_CONFIG")
    )
    parser.add_argument(
        "--output", type=Path, default=os.environ.get("RECORDER_OUTPUT", "sessions")
    )
    parser.add_argument("--url", default="about:blank")
    parser.add_argument(
        "--chromium",
        default=os.environ.get("RECORDER_CHROMIUM"),
        help="Optional Chromium/Chrome executable; defaults to Playwright's browser on all platforms",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Optional Qt controls (install the gui extra)",
    )
    parser.add_argument("--headless", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--mobile", action="store_true", help="Emulate Pixel 7 in Chromium"
    )
    group.add_argument("--device", help="Playwright device profile, e.g. 'iPhone 13'")
    group.add_argument("--desktop", action="store_true")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument(
        "--api",
        action="append",
        default=None,
        metavar="URL_GLOB",
        help="Allow an API URL pattern; repeatable; replaces configured allowlist",
    )
    parser.add_argument("--root", help="CSS selector of the UI to capture")
    parser.add_argument(
        "--repair",
        type=Path,
        metavar="JSON_FILE",
        help="Recover complete events to a new .recovered.json file after a crash",
    )
    args = parser.parse_args(argv)
    try:
        settings = json.loads(args.config.read_text()) if args.config else {}
        env_device = os.environ.get("RECORDER_DEVICE")
        if env_device:
            settings["device"] = env_device
        if args.mobile or args.device or args.desktop:
            settings["device"] = "Pixel 7" if args.mobile else args.device
        if args.api is not None:
            settings["api_allowlist"] = args.api
        if args.root:
            settings["root_selector"] = args.root
        config = Config.model_validate(settings)
    except (ValueError, OSError, TypeError) as error:
        parser.error(
            f"Invalid configuration ({type(error).__name__}); check parameter names, types, and interval limits in USAGE.md"
        )
    return parser, args, config


async def list_devices():
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        for name, profile in playwright.devices.items():
            if profile.get("is_mobile"):
                print(name)


async def run_terminal(args, config):
    from .recorder import Recorder

    recorder = Recorder(args.output, config)
    try:
        await recorder.start(
            args.url, headless=args.headless, executable_path=args.chromium
        )
        print(f"Recording to {recorder.store.path}")
        print("Commands: annotate NAME | end | checkpoint | status | stop")
        while recorder.recording:
            # input() runs outside the browser event loop. A separate daemon
            # reader lets browser closure exit even while the prompt is idle.
            command_task = asyncio.create_task(read_command())
            while not command_task.done() and recorder.recording:
                await asyncio.sleep(0.1)
            if not recorder.recording:
                command_task.cancel()
                await asyncio.gather(command_task, return_exceptions=True)
                break
            command = (await command_task).strip()
            if command in {"stop", "quit", "exit"}:
                break
            try:
                if command.startswith("annotate "):
                    recorder.start_annotation(command.removeprefix("annotate "))
                elif command == "end":
                    await recorder.end_annotation()
                elif command == "checkpoint":
                    await recorder.checkpoint()
                elif command == "status":
                    print(dict(recorder.store.counts))
                elif command:
                    print("Commands: annotate NAME | end | checkpoint | status | stop")
            except (ValueError, RuntimeError) as error:
                print(str(error))
    finally:
        await recorder.stop()
        if recorder.store:
            print(f"Saved {recorder.store.path}")


async def read_command():
    import threading

    loop = asyncio.get_running_loop()
    future = loop.create_future()

    def deliver(value):
        if not future.done():
            future.set_result(value)

    def read():
        try:
            value = input("> ")
        except EOFError:
            value = "stop"
        if not loop.is_closed():
            try:
                loop.call_soon_threadsafe(deliver, value)
            except RuntimeError:
                pass  # Browser was closed while the reader was blocked.

    threading.Thread(target=read, daemon=True).start()
    return await future


def run_gui(args, config):
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
    timer = QTimer()

    def tick():
        loop.call_soon(loop.stop)
        loop.run_forever()

    timer.timeout.connect(tick)
    timer.start(20)
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
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


def main():
    parser, args, config = parse_args()
    if args.list_devices:
        asyncio.run(list_devices())
    elif args.repair:
        from .storage import repair_recording

        try:
            print(repair_recording(args.repair))
        except (OSError, ValueError) as error:
            parser.error(f"Cannot repair recording ({type(error).__name__})")
    elif args.gui:
        try:
            run_gui(args, config)
        except ImportError:
            parser.error(
                "GUI not installed: run uv sync --extra gui, or pip install -e '.[gui]'"
            )
    else:
        try:
            asyncio.run(run_terminal(args, config))
        except KeyboardInterrupt:
            pass
        except (ValueError, OSError) as error:
            parser.error(
                f"Cannot start recorder ({type(error).__name__}); check --list-devices and installation instructions"
            )


if __name__ == "__main__":
    main()
