import os
import sys
import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from playwright_checks.core.config_loader import (  # noqa: E402
    get_access_denied_patterns,
    get_page_config,
    load_settings,
    load_site_config,
    locator,
    locator_map,
)
from playwright_checks.core.paths import legacy_screenshot_root
from playwright_checks.core.viewport import get_current_viewport_name


class AccessBlockedError(Exception):
    """Raised when the browser lands on an anti-bot or access-denied page."""


TRANSIENT_VERIFICATION_PATTERNS = [
    "just a moment",
    "connection needs to be verified",
    "verification successful. waiting",
]


def screenshot_root(site):
    return str(legacy_screenshot_root(site))


def selector_for(locator_value):
    method, value = locator_value
    if method == "css":
        return value
    if method == "xpath":
        return f"xpath={value}"
    raise ValueError(f"Unsupported locator method: {method!r}")


def locate_element(page, locator_value):
    element = page.locator(selector_for(locator_value)).first
    element.wait_for(state="visible", timeout=10000)
    return element


def wait_for_visible(page, locator_value, timeout=45000):
    element = page.locator(selector_for(locator_value)).first
    element.wait_for(state="visible", timeout=timeout)
    return element


def wait_for_page_load(page, timeout=15000, label="page"):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        state = page.evaluate("document.readyState").strip()
        print(f"{label} readyState wait skipped, state={state}, url={page.url}")
        return False


def assert_page_not_blocked(page, timeout=20):
    end_time = time.time() + timeout
    patterns = get_access_denied_patterns()
    transient_seen = None

    while time.time() < end_time:
        title = page.title() or ""
        body_text = page.locator("body").inner_text(timeout=1000)[:2000]
        haystack = f"{title}\n{body_text}".lower()

        for pattern in TRANSIENT_VERIFICATION_PATTERNS:
            if pattern in haystack:
                transient_seen = pattern
                time.sleep(1)
                break
        else:
            transient_seen = None

            for pattern in patterns:
                if pattern.lower() in haystack:
                    raise AccessBlockedError(
                        f"Access blocked by verification page: {pattern!r}, "
                        f"url={page.url}, title={title!r}"
                    )

            if body_text.strip():
                return

        time.sleep(0.2)

    if transient_seen:
        raise AccessBlockedError(
            f"Verification page did not finish: {transient_seen!r}, "
            f"url={page.url}, title={page.title()!r}"
        )


def open_page_with_retry(page, url, wait_until_ready, label="page", attempts=3, delay=2):
    for attempt in range(1, attempts + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            assert_page_not_blocked(page)
            wait_until_ready(page)
            time.sleep(1)
            assert_page_not_blocked(page)
            wait_until_ready(page)
            return
        except AccessBlockedError:
            raise
        except Exception:
            if attempt == attempts:
                raise
            print(f"{label} page not ready, retry {attempt}/{attempts}")
            time.sleep(delay)


def create_dirs(*dirs):
    for directory in dirs:
        os.makedirs(directory, exist_ok=True)


def build_paths(current_dir, baseline_dir, diff_dir, name, legacy_baseline_dir=None):
    baseline = os.path.join(baseline_dir, f"{name}.png")
    legacy_baseline = (
        os.path.join(legacy_baseline_dir, f"{name}.png")
        if legacy_baseline_dir
        else None
    )

    active_baseline = baseline
    if (
        legacy_baseline
        and not os.path.exists(baseline)
        and os.path.exists(legacy_baseline)
    ):
        active_baseline = legacy_baseline

    return {
        "current": os.path.join(current_dir, f"{name}.png"),
        "baseline": active_baseline,
        "target_baseline": baseline,
        "legacy_baseline": legacy_baseline,
        "diff": os.path.join(diff_dir, f"{name}.png"),
    }
