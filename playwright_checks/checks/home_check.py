import os
import sys
import tempfile
import time

import numpy as np
from PIL import Image

from playwright_checks.core.driver import close_browser, init_browser
from playwright_checks.core.test_results import add_result, clear_results, write_results
from playwright_checks.utils.capture import capture_modules, scroll_to_center, wait_for_layout_stable
from playwright_checks.utils.dom import dom_check, hide_dynamic_elements
from playwright_checks.utils.waits import (
    build_paths,
    create_dirs,
    get_page_config,
    load_site_config,
    locate_element,
    locator_map,
    open_page_with_retry,
    screenshot_root,
    wait_for_page_load,
)
from playwright_checks.utils.visual import build_result, process_results


SUITE = "visual"
PAGE = "home"
SITE_CONFIG = None
PAGE_CONFIG = None
URL = None
SITE = None

ROOT_DIR = None
PAGE_DIR = None
BASELINE_DIR = None
CURRENT_DIR = None
DIFF_DIR = None

MODULES = {}
PLUGINS = {}
CAPTURE_EXCLUDE = set()


def configure_context():
    global SITE_CONFIG, PAGE_CONFIG, URL, SITE
    global ROOT_DIR, PAGE_DIR, BASELINE_DIR, CURRENT_DIR, DIFF_DIR
    global MODULES, PLUGINS, CAPTURE_EXCLUDE

    SITE_CONFIG = load_site_config()
    PAGE_CONFIG = get_page_config(PAGE, SITE_CONFIG)
    URL = PAGE_CONFIG["url"]
    SITE = SITE_CONFIG["site"]

    ROOT_DIR = screenshot_root(SITE)
    PAGE_DIR = os.path.join(ROOT_DIR, PAGE)
    BASELINE_DIR = os.path.join(PAGE_DIR, "baseline")
    CURRENT_DIR = os.path.join(PAGE_DIR, "current")
    DIFF_DIR = os.path.join(PAGE_DIR, "diff")

    MODULES = locator_map(PAGE_CONFIG["modules"])
    PLUGINS = locator_map(PAGE_CONFIG.get("plugins", {}))
    CAPTURE_EXCLUDE = set(PAGE_CONFIG.get("capture_exclude", []))


def check_plugins(page):
    print("\nPlugin checks")
    failures = []

    for name, locator in PLUGINS.items():
        try:
            element = locate_element(page, locator)
            visible = element.is_visible()
            print(f"OK {name} visible={visible}")
            if not visible:
                failures.append(f"plugin [{name}] visible=False")
        except Exception as e:
            print(f"FAIL {name} plugin error: {e}")
            failures.append(f"plugin [{name}] error: {e}")

    return failures


def normalize_plugin_image_for_compare(name, path, output_path=None):
    if name != "wishlist" or not os.path.exists(path):
        return

    image = Image.open(path).convert("RGB")
    pixels = np.array(image)
    height, width = pixels.shape[:2]

    y, x = np.ogrid[:height, :width]
    center_x = (width - 1) / 2
    center_y = (height - 1) / 2
    radius = min(width, height) / 2

    keep_badge = (x < width * 0.4) & (y < height * 0.4)
    pixels[~keep_badge] = [255, 255, 255]

    Image.fromarray(pixels).save(output_path or path)


def prepare_plugin_compare_images(name, paths):
    if name != "wishlist":
        return

    baseline = paths["baseline"]
    current = paths["current"]

    if not os.path.exists(baseline) or not os.path.exists(current):
        return

    compare_root = os.path.splitext(paths["diff"])[0]
    compare_baseline = f"{compare_root}_baseline_compare.png"
    compare_current = f"{compare_root}_current_compare.png"

    normalize_plugin_image_for_compare(
        name,
        baseline,
        output_path=compare_baseline
    )
    normalize_plugin_image_for_compare(
        name,
        current,
        output_path=compare_current
    )

    paths["compare_baseline"] = compare_baseline
    paths["compare_current"] = compare_current


def images_close(path1, path2, threshold=0.001):
    img1 = np.array(Image.open(path1).convert("RGB"))
    img2 = np.array(Image.open(path2).convert("RGB"))

    if img1.shape != img2.shape:
        return False

    diff = np.abs(img1.astype(int) - img2.astype(int))
    changed = (diff > 25).any(axis=2)
    return changed.sum() / changed.size <= threshold


def plugin_image_ready(name, path):
    if name == "wishlist":
        with Image.open(path) as image:
            width, height = image.size
        ratio = width / height if height else 0
        return width >= 45 and height >= 45 and 0.8 <= ratio <= 1.25

    if name != "currency":
        return True

    image = np.array(Image.open(path).convert("RGB"))
    white_ratio = ((image > 235).all(axis=2)).sum() / image.shape[0] / image.shape[1]
    return white_ratio >= 0.4


def normalize_plugin_for_screenshot(name, element):
    if name != "currency":
        return

    element.evaluate(
        """
        (el) => {
            el.style.setProperty('background', '#fff', 'important');
            el.style.setProperty('background-color', '#fff', 'important');
            el.style.setProperty('box-shadow', 'inset 0 0 0 9999px #fff', 'important');
            el.style.setProperty('color', '#000', 'important');
            el.querySelectorAll('svg').forEach(function(svg) {
                svg.style.setProperty('fill', '#000', 'important');
            });
            void el.offsetHeight;
        }
        """
    )


def capture_stable_plugin(page, name, locator, output_path, timeout=10):
    end_time = time.time() + timeout
    previous_path = None

    while time.time() < end_time:
        element = locate_element(page, locator)
        normalize_plugin_for_screenshot(name, element)
        scroll_to_center(element)
        time.sleep(0.2)

        if not wait_for_layout_stable(element, timeout=2):
            time.sleep(0.3)
            continue

        current_path = os.path.join(
            tempfile.gettempdir(),
            f"plugin_probe_{time.time_ns()}.png"
        )
        element.screenshot(path=current_path)
        normalize_plugin_image_for_compare(name, current_path)

        if not plugin_image_ready(name, current_path):
            previous_path = None
            time.sleep(0.3)
            continue

        if previous_path and images_close(previous_path, current_path):
            os.replace(current_path, output_path)
            return

        previous_path = current_path
        time.sleep(0.3)

    if previous_path:
        os.replace(previous_path, output_path)
        return

    raise Exception("plugin screenshot is not stable")


def capture_plugins(page):
    print("\nPlugin screenshots")
    results = {}

    for name, locator in PLUGINS.items():
        paths = build_paths(CURRENT_DIR, BASELINE_DIR, DIFF_DIR, name)
        max_attempts = 2
        capture_start = time.perf_counter()

        for attempt in range(1, max_attempts + 1):
            try:
                capture_stable_plugin(
                    page,
                    name,
                    locator,
                    paths["current"],
                    timeout=20
                )
                normalize_plugin_image_for_compare(name, paths["current"])
                prepare_plugin_compare_images(name, paths)
                paths["capture_duration_ms"] = round(
                    (time.perf_counter() - capture_start) * 1000,
                    2
                )
                paths["capture_attempts"] = attempt
                results[name] = paths
                print(f"OK [{name}]")
                break
            except Exception as e:
                if attempt == max_attempts:
                    print(f"FAIL [{name}] plugin screenshot failed: {e}")
                    results[name] = {
                        "error": f"plugin screenshot failed: {e}",
                        "capture_duration_ms": round(
                            (time.perf_counter() - capture_start) * 1000,
                            2
                        ),
                        "capture_attempts": attempt,
                    }
                else:
                    print(f"WARN [{name}] plugin retry {attempt}/{max_attempts}")
                    time.sleep(1)

    return results


def stabilize_banner(page):
    page.evaluate("""
        () => {
            document.querySelectorAll('.flickity-enabled').forEach(function(el) {
                if (window.Flickity && Flickity.data(el)) {
                    const flkty = Flickity.data(el);
                    flkty.stopPlayer();
                    flkty.select(0, true);
                }
            });
        }
    """)
    time.sleep(1)


def wait_for_home_page(page):
    wait_for_page_load(page, label="Home")
    locate_element(page, MODULES["header_1"])
    locate_element(page, MODULES["banner"])


def run():
    configure_context()
    failures = []
    create_dirs(BASELINE_DIR, CURRENT_DIR, DIFF_DIR)

    playwright = browser = context = page = None

    try:
        playwright, browser, context, page = init_browser()
        open_page_with_retry(page, URL, wait_for_home_page, "Home")
        time.sleep(2)

        failures.extend(dom_check(page, MODULES))
        failures.extend(check_plugins(page))

        plugin_results = capture_plugins(page)

        hide_dynamic_elements(page, SITE_CONFIG, PAGE_CONFIG)
        stabilize_banner(page)

        module_locators = {
            name: module_locator
            for name, module_locator in MODULES.items()
            if name not in CAPTURE_EXCLUDE
        }
        module_results = capture_modules(
            page,
            module_locators,
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR,
            require_reviews=False,
            site_config=SITE_CONFIG,
            page_config=PAGE_CONFIG
        )

        failures.extend(process_results(module_results, SITE, SUITE, PAGE))
        failures.extend(process_results(plugin_results, SITE, SUITE, PAGE))

    except Exception as e:
        error = f"Playwright runtime error: {type(e).__name__}: {e}"
        failures.append(f"Home: {error}")
        add_result(build_result(SITE, SUITE, PAGE, "runtime", "failed", None, error=error))
    finally:
        close_browser(playwright, browser, context)

    return failures


if __name__ == "__main__":
    clear_results()
    page_failures = run()
    results_file = write_results()
    print(f"\nVisual test results: {results_file}")
    if page_failures:
        print("\nHome Playwright failures")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
