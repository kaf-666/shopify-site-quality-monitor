import os
import sys
import time

from PIL import Image
from playwright.sync_api import Error as PlaywrightError

from playwright_checks.core.driver import close_browser, init_browser
from playwright_checks.core.test_results import add_result, clear_results, write_results
from playwright_checks.core.viewport import is_mobile_viewport
from playwright_checks.utils.capture import (
    capture_modules,
    screenshot_element_with_retry,
    scroll_to_center,
    wait_for_images,
    wait_for_layout_stable,
)
from playwright_checks.utils.dom import dom_check, hide_dynamic_elements
from playwright_checks.utils.waits import (
    build_paths,
    create_dirs,
    get_page_config,
    load_site_config,
    locate_element,
    locator,
    locator_map,
    open_page_with_retry,
    screenshot_root,
    selector_for,
    wait_for_page_load,
    wait_for_visible,
)
from playwright_checks.utils.visual import build_result, process_results


SUITE = "visual"
PAGE = "collection"
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
PRODUCT_CARD = None
EXPECTED_COUNT = None
CAPTURE_EXCLUDE = set()


def configure_context():
    global SITE_CONFIG, PAGE_CONFIG, URL, SITE
    global ROOT_DIR, PAGE_DIR, BASELINE_DIR, CURRENT_DIR, DIFF_DIR
    global MODULES, PRODUCT_CARD, EXPECTED_COUNT, CAPTURE_EXCLUDE

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
    PRODUCT_CARD = locator(PAGE_CONFIG["product_card"])
    EXPECTED_COUNT = PAGE_CONFIG.get("expected_count")
    CAPTURE_EXCLUDE = set(PAGE_CONFIG.get("capture_exclude", []))


def get_product_cards(page):
    return page.locator(selector_for(PRODUCT_CARD))


def scroll_to_load_all(page, timeout=10, max_scrolls=20):
    last_count = 0
    stable_count = 0
    end_time = time.time() + timeout
    cards = get_product_cards(page)

    for _ in range(max_scrolls):
        if time.time() > end_time:
            break

        try:
            current_count = cards.count()
        except PlaywrightError as e:
            print(f"product card count interrupted: {e}")
            return last_count

        if current_count == last_count:
            stable_count += 1
            if stable_count >= 3:
                return current_count
        else:
            stable_count = 0
            last_count = current_count

        try:
            page.evaluate("() => window.scrollBy(0, window.innerHeight)")
        except PlaywrightError as e:
            print(f"scroll interrupted: {e}")
            return last_count

        time.sleep(0.5)

    return last_count


def wait_for_collection_page(page):
    wait_for_page_load(page, label="PLP")
    wait_for_visible(page, MODULES["product_grid"])
    wait_for_visible(page, MODULES["filter"])
    get_product_cards(page).first.wait_for(state="visible", timeout=45000)


def check_product_count(page):
    print("\nProduct count checks")
    failures = []

    scroll_to_load_all(page, timeout=15)
    count = get_product_cards(page).count()

    if EXPECTED_COUNT is None:
        print(f"product count: {count} (expected_count not configured, skipped)")
        return failures

    if count == EXPECTED_COUNT:
        print(f"OK product count: {count}")
    else:
        print(f"FAIL product count: {count}, expected {EXPECTED_COUNT}")
        failures.append(
            f"product count mismatch: actual {count}, expected {EXPECTED_COUNT}"
        )

    return failures


def mask_card_image(source_path, output_path, target_size=None, target_mode=None):
    with Image.open(source_path) as image:
        masked = image.copy()

        if target_size and masked.size != target_size:
            normalized = Image.new(
                masked.mode,
                target_size,
                (255, 255, 255, 0)
                if "A" in masked.mode else (255, 255, 255)
            )
            crop = masked.crop((
                0,
                0,
                min(masked.width, target_size[0]),
                min(masked.height, target_size[1])
            ))
            normalized.paste(crop, (0, 0))
            masked = normalized

        if target_mode and masked.mode != target_mode:
            masked = masked.convert(target_mode)

        mask_height = max(86, int(masked.height * 0.17))
        y = max(0, masked.height - mask_height)
        color = (255, 255, 255, 0) if "A" in masked.mode else (255, 255, 255)
        masked.paste(color, (0, y, masked.width, masked.height))
        masked.save(output_path)


def prepare_card_compare_images(paths):
    baseline = paths["baseline"]
    current = paths["current"]

    if not os.path.exists(baseline) or not os.path.exists(current):
        return

    compare_root = os.path.splitext(paths["diff"])[0]
    compare_baseline = f"{compare_root}_baseline_compare.png"
    compare_current = f"{compare_root}_current_compare.png"

    with Image.open(baseline) as base_image:
        base_size = base_image.size
        base_mode = base_image.mode

    mask_card_image(
        baseline,
        compare_baseline,
        target_size=base_size,
        target_mode=base_mode
    )
    mask_card_image(
        current,
        compare_current,
        target_size=base_size,
        target_mode=base_mode
    )

    paths["compare_baseline"] = compare_baseline
    paths["compare_current"] = compare_current


def get_product_card_by_index(page, index):
    cards = get_product_cards(page)
    count = cards.count()

    if index >= count:
        raise Exception(f"product card {index} does not exist")

    card = cards.nth(index)
    card.wait_for(state="visible", timeout=10000)
    return card


def capture_card_image_stable(page, index, output_path, hover=False):
    def locate():
        return get_product_card_by_index(page, index)

    def prepare(card):
        scroll_to_center(card)
        hide_dynamic_elements(page, SITE_CONFIG, PAGE_CONFIG)

        if not wait_for_images(card, timeout=10):
            raise Exception("wait for images timeout")

        if not wait_for_layout_stable(card, timeout=10):
            raise Exception("layout is not stable")

        card = locate()
        scroll_to_center(card)
        hide_dynamic_elements(page, SITE_CONFIG, PAGE_CONFIG)

        if hover:
            card.hover(timeout=10000)
            time.sleep(1)

    return screenshot_element_with_retry(
        locate,
        output_path,
        prepare=prepare,
        attempts=3,
        delay=1
    )


def capture_product_cards(page):
    print("\nProduct card screenshots")
    results = {}
    card_count = min(8, get_product_cards(page).count())

    for index in range(card_count):
        name = f"product_{index}"
        paths = build_paths(CURRENT_DIR, BASELINE_DIR, DIFF_DIR, name)

        try:
            metrics = capture_card_image_stable(page, index, paths["current"])
            paths.update(metrics)
            prepare_card_compare_images(paths)
            results[name] = paths
            print(f"OK {name}")
        except Exception as e:
            print(f"FAIL {name} capture failed: {e}")
            results[name] = {"error": f"capture failed: {e}"}

    return results


def capture_hover_cards(page):
    print("\nHover screenshots")
    results = {}

    if is_mobile_viewport():
        print("mobile viewport: hover screenshots skipped")
        return results

    card_count = min(8, get_product_cards(page).count())

    for index in range(card_count):
        name = f"hover_{index}"
        paths = build_paths(CURRENT_DIR, BASELINE_DIR, DIFF_DIR, name)

        try:
            metrics = capture_card_image_stable(
                page,
                index,
                paths["current"],
                hover=True
            )
            paths.update(metrics)
            prepare_card_compare_images(paths)
            results[name] = paths
            print(f"OK {name}")
        except Exception as e:
            print(f"FAIL {name} capture failed: {e}")
            results[name] = {"error": f"capture failed: {e}"}

    return results


def run():
    configure_context()
    failures = []
    create_dirs(BASELINE_DIR, CURRENT_DIR, DIFF_DIR)
    playwright = browser = context = page = None

    try:
        playwright, browser, context, page = init_browser()
        open_page_with_retry(page, URL, wait_for_collection_page, "PLP")
        time.sleep(2)

        failures.extend(dom_check(page, MODULES))
        failures.extend(check_product_count(page))

        hide_dynamic_elements(page, SITE_CONFIG, PAGE_CONFIG)

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
        product_results = capture_product_cards(page)
        hover_results = capture_hover_cards(page)

        failures.extend(process_results(module_results, SITE, SUITE, PAGE))
        failures.extend(process_results(product_results, SITE, SUITE, PAGE))
        failures.extend(process_results(hover_results, SITE, SUITE, PAGE))

    except Exception as e:
        error = f"Playwright runtime error: {type(e).__name__}: {e}"
        failures.append(f"PLP: {error}")
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
        print("\nPLP Playwright failures")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
