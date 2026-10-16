import asyncio
import json
import logging
import math
from pathlib import Path

from playwright.async_api import Error, async_playwright

from .config import Config

log = logging.getLogger(__name__)


async def launch(config: Config, *, headless=False, executable_path=None):
    playwright = await async_playwright().start()
    try:
        if config.device:
            if config.device not in playwright.devices:
                raise ValueError(f"Unknown device: {config.device}; use --list-devices")
            options = dict(playwright.devices[config.device])
            if not options.get("is_mobile"):
                raise ValueError("Use a mobile profile or --desktop")
            options.pop("default_browser_type", None)
        else:
            options = {
                "viewport": {
                    "width": config.viewport_width,
                    "height": config.viewport_height,
                }
            }
        browser = await playwright.chromium.launch(
            headless=headless, executable_path=executable_path
        )
        context = await browser.new_context(**options)
        context.set_default_navigation_timeout(config.navigation_timeout_ms)
        if config.device and not headless:
            window_metrics = {}

            def prepare(page):
                page._recorder_mobile_fit = asyncio.create_task(
                    fit_mobile_window(page, options, window_metrics)
                )

            context.on("page", prepare)
        return playwright, browser, context
    except BaseException:
        await playwright.stop()
        raise


async def prepare_page(page):
    task = getattr(page, "_recorder_mobile_fit", None)
    if task:
        await task


async def fit_mobile_window(page, profile, window_metrics):
    """Fit the phone's rendered image; keep its CSS viewport and device scale."""
    session = None
    viewport = profile["viewport"]
    device_screen = profile.get("screen", viewport)
    landscape = viewport["width"] > viewport["height"]
    metrics = {
        "width": viewport["width"],
        "height": viewport["height"],
        "deviceScaleFactor": profile.get("device_scale_factor", 1),
        "mobile": True,
        "scale": 1,
        "screenWidth": device_screen["width"],
        "screenHeight": device_screen["height"],
        "screenOrientation": {
            "type": "landscapePrimary" if landscape else "portraitPrimary",
            "angle": 90 if landscape else 0,
        },
    }
    try:
        session = await page.context.new_cdp_session(page)
        # Keep this session alive: detaching it can discard its emulation state.
        page._recorder_mobile_session = session
        window = await session.send("Browser.getWindowForTarget")
        window_id = window["windowId"]
        cached = window_metrics.get(window_id)
        if cached is None:
            # Device profiles override screen/viewport measurements. Temporarily
            # clear those overrides to measure the real monitor and browser chrome.
            # Explicitly disable metrics: clearing a secondary session does not
            # remove the override installed by Playwright's original session.
            await session.send(
                "Emulation.setDeviceMetricsOverride",
                {
                    "width": 0,
                    "height": 0,
                    "deviceScaleFactor": 0,
                    "mobile": False,
                },
            )
            screen = await page.evaluate("""() => ({
                width: screen.availWidth, height: screen.availHeight,
                left: screen.availLeft || 0, top: screen.availTop || 0,
                chromeWidth: Math.max(0, outerWidth - innerWidth),
                chromeHeight: Math.max(0, outerHeight - innerHeight)
            })""")
            viewport = profile["viewport"]
            # Leave room for OS decorations and browser borders on both macOS
            # and Linux. The scale affects display only, not responsive layout.
            margin = 24
            available_width = max(
                1, screen["width"] - screen["chromeWidth"] - margin * 2
            )
            available_height = max(
                1, screen["height"] - screen["chromeHeight"] - margin * 2
            )
            scale = min(
                1,
                available_width / viewport["width"],
                available_height / viewport["height"],
            )
            width = min(
                screen["width"],
                math.ceil(viewport["width"] * scale + screen["chromeWidth"] + margin),
            )
            height = min(
                screen["height"],
                math.ceil(viewport["height"] * scale + screen["chromeHeight"] + margin),
            )
            await session.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "normal"}},
            )
            await session.send(
                "Browser.setWindowBounds",
                {
                    "windowId": window_id,
                    "bounds": {
                        "width": width,
                        "height": height,
                        "left": int(screen["left"] + (screen["width"] - width) / 2),
                        "top": int(screen["top"] + margin / 2),
                    },
                },
            )
            metrics["scale"] = scale
            window_metrics[window_id] = metrics
        else:
            metrics = cached
        await session.send("Emulation.setDeviceMetricsOverride", metrics)
    except Error as error:
        # A popup can navigate or close during setup. Leave its device profile
        # usable rather than aborting the whole recording.
        if session:
            try:
                await session.send("Emulation.setDeviceMetricsOverride", metrics)
            except Error:
                pass
        log.warning("Could not fit mobile window (%s)", type(error).__name__)


def sensor_script(config):
    return (
        "(() => { const config = "
        + json.dumps(config.model_dump())
        + ";\n"
        + (Path(__file__).parent / "js" / "recorder.js").read_text(encoding="utf-8")
        + "\n})();"
    )
