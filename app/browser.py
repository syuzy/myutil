import json
from pathlib import Path

from playwright.async_api import async_playwright

from .config import Config


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
        return playwright, browser, context
    except BaseException:
        await playwright.stop()
        raise


def sensor_script(config):
    return (
        "(() => { const config = "
        + json.dumps(config.model_dump())
        + ";\n"
        + (Path(__file__).parent / "js" / "recorder.js").read_text(encoding="utf-8")
        + "\n})();"
    )
