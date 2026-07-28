import os

from playwright.sync_api import sync_playwright

from playwright_checks.core.config_loader import load_settings, load_site_config
from playwright_checks.core.request_headers import (
    install_signed_request_routing,
    load_signed_request_headers,
)
from playwright_checks.core.viewport import get_viewport_config


def init_browser(site_config=None):
    """Start Playwright with a deterministic browser context."""

    settings = load_settings()
    configured_site = site_config or load_site_config()
    load_signed_request_headers(configured_site)
    playwright = sync_playwright().start()
    browser_settings = dict(settings.get("browser", {}))
    site_browser_settings = configured_site.get("browser", {})
    browser_settings.update(site_browser_settings)
    viewport_config = get_viewport_config()

    configured_channel = os.environ.get(
        "PLAYWRIGHT_BROWSER_CHANNEL",
        browser_settings.get("channel", "chrome"),
    )
    channel = (
        None
        if str(configured_channel or "").strip().lower() == "chromium"
        else configured_channel
    )
    headed_override = os.environ.get("PLAYWRIGHT_HEADED")
    if "headed" in site_browser_settings:
        headless = not bool(browser_settings.get("headed", False))
        mode_source = "site config"
    elif headed_override is None:
        headless = not bool(browser_settings.get("headed", False))
        mode_source = "settings config"
    else:
        headless = headed_override.lower() not in ("1", "true", "yes")
        mode_source = "PLAYWRIGHT_HEADED"

    print(
        "Browser mode: "
        f"{'headless' if headless else 'headed'} "
        f"({mode_source}, site={configured_site.get('site', 'unknown')})"
    )

    launch_options = {
        "headless": headless,
        "args": [
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ],
    }
    if channel:
        launch_options["channel"] = channel

    browser = playwright.chromium.launch(
        **launch_options,
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
    install_signed_request_routing(context, configured_site)
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
