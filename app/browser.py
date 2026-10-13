from pathlib import Path

from playwright.async_api import async_playwright


async def launch(*, headless: bool = False, executable_path: str | None = None):
    playwright = await async_playwright().start()
    try:
        browser = await playwright.chromium.launch(
            headless=headless, executable_path=executable_path
        )
        context = await browser.new_context()
        return playwright, browser, context
    except BaseException:
        await playwright.stop()
        raise


def sensor_script(config) -> str:
    import json

    root = Path(__file__).parent / "js"
    return (
        root.joinpath("rrweb.min.js").read_text(encoding="utf-8")
        + "\n;window.__recorderConfig = "
        + json.dumps(config.model_dump())
        + ";\n"
        + root.joinpath("recorder.js").read_text(encoding="utf-8")
    )
