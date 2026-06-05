import os

from playwright.sync_api import sync_playwright

from playwright_checks.core.config_loader import load_settings
from playwright_checks.core.viewport import get_viewport_config


def init_browser():
    """Start Playwright with a deterministic Chrome context."""

    playwright = sync_playwright().start()
    settings = load_settings()
    browser_settings = settings.get("browser", {})
    viewport_config = get_viewport_config()

    channel = os.environ.get(
        "PLAYWRIGHT_BROWSER_CHANNEL",
        browser_settings.get("channel", "chrome"),
    )
    headless = os.environ.get("PLAYWRIGHT_HEADED", "").lower() not in (
        "1",
        "true",
        "yes",
    )
    if "PLAYWRIGHT_HEADED" not in os.environ:
        headless = not bool(browser_settings.get("headed", False))

    browser = playwright.chromium.launch(
        channel=channel,
        headless=headless,
        args=[
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ],
    )

    viewport = {
        "width": int(viewport_config.get("width", 1600)),
        "height": int(viewport_config.get("height", 4000)),
    }
    context_options = {
        "viewport": viewport,
        "device_scale_factor": viewport_config.get("device_scale_factor", 1),
        "locale": "en-US",
    }

    for key in ("is_mobile", "has_touch", "user_agent"):
        if key in viewport_config:
            context_options[key] = viewport_config[key]

    context = browser.new_context(
        **context_options,
    )
    context.set_default_timeout(30000)
    context.set_default_navigation_timeout(45000)

    page = context.new_page()

    return playwright, browser, context, page


def close_browser(playwright, browser, context):
    if context:
        context.close()
    if browser:
        browser.close()
    if playwright:
        playwright.stop()
