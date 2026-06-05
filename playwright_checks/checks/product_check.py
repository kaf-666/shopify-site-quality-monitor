import os
import sys
import time

from playwright_checks.core.driver import close_browser, init_browser
from playwright_checks.core.test_results import add_result, clear_results, write_results
from playwright_checks.utils.capture import (
    capture_modules,
    scroll_to_center,
    wait_for_capture_ready,
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
PAGE = "product"
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
VARIANT_INPUTS = None
REQUIRE_REVIEWS = True
CAPTURE_EXCLUDE = set()


def configure_context():
    global SITE_CONFIG, PAGE_CONFIG, URL, SITE
    global ROOT_DIR, PAGE_DIR, BASELINE_DIR, CURRENT_DIR, DIFF_DIR
    global MODULES, VARIANT_INPUTS, REQUIRE_REVIEWS, CAPTURE_EXCLUDE

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
    VARIANT_INPUTS = locator(PAGE_CONFIG["variant_inputs"])
    REQUIRE_REVIEWS = PAGE_CONFIG.get("require_reviews", True)
    CAPTURE_EXCLUDE = set(PAGE_CONFIG.get("capture_exclude", []))


def find_elements(page, locator_value):
    return page.locator(selector_for(locator_value))


def check_add_to_cart(page):
    print("\nAdd To Cart state")
    failures = []

    try:
        button = locate_element(page, MODULES["add_to_cart"])
        enabled = button.is_enabled()
        print(f"enabled={enabled}")

        if not enabled:
            failures.append("Add To Cart button is disabled")

    except Exception as e:
        print(f"FAIL {e}")
        failures.append(f"Add To Cart state error: {e}")

    return failures


def check_variant_count(page):
    variants = find_elements(page, VARIANT_INPUTS)
    print(f"\nVariant count: {variants.count()}")


def get_cart_item_count(page):
    return page.evaluate("""
        async () => {
            try {
                const response = await fetch('/cart.js', { credentials: 'same-origin' });
                if (!response.ok) {
                    return { error: 'cart.js HTTP ' + response.status };
                }
                const cart = await response.json();
                return cart.item_count;
            } catch (error) {
                return { error: error.message };
            }
        }
    """)


def cart_count_value(value):
    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    return None


def visible_add_to_cart_error(page):
    selectors = [
        "#size_error",
        ".errors",
        ".error",
        ".product-form__error-message",
    ]

    for selector in selectors:
        elements = page.locator(selector)
        for index in range(elements.count()):
            element = elements.nth(index)
            try:
                text = element.inner_text(timeout=1000).strip()
                if element.is_visible() and text:
                    return text
            except Exception:
                continue

    return None


def select_first_available_variant_options(page):
    return page.evaluate("""
        () => {
            const form = document.querySelector('form[action*="/cart/add"]') || document;
            const roots = [
                form,
                ...document.querySelectorAll('.my-infiniteoptions-container')
            ];
            const selected = [];

            roots.forEach(function(root) {
                root.querySelectorAll('select').forEach(function(select) {
                    if (select.disabled) return;

                    const current = select.options[select.selectedIndex];
                    if (current && current.value && !current.disabled) return;

                    const option = Array.from(select.options).find(function(item) {
                        return item.value && !item.disabled;
                    });

                    if (!option) return;

                    select.value = option.value;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    selected.push(select.name || select.id || option.textContent.trim());
                });
            });

            const groups = new Map();
            roots.forEach(function(root) {
                root.querySelectorAll('input[type="radio"]').forEach(function(input) {
                    if (!input.name || input.disabled || !input.value) return;

                    if (!groups.has(input.name)) {
                        groups.set(input.name, []);
                    }

                    groups.get(input.name).push(input);
                });
            });

            groups.forEach(function(inputs, name) {
                if (inputs.some(function(input) { return input.checked; })) return;

                const input = inputs.find(function(item) {
                    return !item.disabled;
                });

                if (!input) return;

                input.click();
                input.dispatchEvent(new Event('change', { bubbles: true }));
                selected.push(name + '=' + input.value);
            });

            return selected;
        }
    """)


def test_variants(page):
    print("\nVariant checks")
    results = {}
    failures = []

    try:
        variants = find_elements(page, VARIANT_INPUTS)

        if variants.count() == 0:
            print("WARN variant not found")
            failures.append("Variant not found")
            return results, failures

        variant_count = min(3, variants.count())

        for index in range(variant_count):
            name = f"variant_{index}"
            paths = build_paths(CURRENT_DIR, BASELINE_DIR, DIFF_DIR, name)
            max_attempts = 3
            capture_start = time.perf_counter()

            for attempt in range(1, max_attempts + 1):
                try:
                    variants = find_elements(page, VARIANT_INPUTS)

                    if index >= variants.count():
                        raise Exception(f"Variant {index} does not exist")

                    variant = variants.nth(index)
                    variant.scroll_into_view_if_needed(timeout=10000)
                    variant.click(force=True, timeout=10000)
                    time.sleep(2)

                    gallery = locate_element(page, MODULES["gallery"])
                    scroll_to_center(gallery)
                    hide_dynamic_elements(page, SITE_CONFIG, PAGE_CONFIG)
                    wait_for_capture_ready(
                        page,
                        gallery,
                        require_reviews=REQUIRE_REVIEWS,
                        timeout=10
                    )
                    hide_dynamic_elements(page, SITE_CONFIG, PAGE_CONFIG)
                    time.sleep(0.5)

                    gallery = locate_element(page, MODULES["gallery"])
                    gallery.screenshot(path=paths["current"])
                    paths["capture_duration_ms"] = round(
                        (time.perf_counter() - capture_start) * 1000,
                        2
                    )
                    paths["capture_attempts"] = attempt
                    results[name] = paths
                    print(f"OK Variant {index}")
                    break

                except Exception as e:
                    if attempt == max_attempts:
                        print(f"FAIL Variant {index} capture failed: {e}")
                        results[name] = {
                            "error": f"capture failed: {e}",
                            "capture_duration_ms": round(
                                (time.perf_counter() - capture_start) * 1000,
                                2
                            ),
                            "capture_attempts": attempt,
                        }
                    else:
                        print(f"WARN Variant {index} retry {attempt}/{max_attempts}")
                        time.sleep(1)

    except Exception as e:
        print(f"FAIL Variant checks failed: {e}")
        failures.append(f"Variant checks failed: {e}")

    return results, failures


def test_add_to_cart(page):
    print("\nAdd To Cart check")
    failures = []

    try:
        selected_options = select_first_available_variant_options(page)
        if selected_options:
            print(
                "selected product options: "
                + ", ".join(str(item) for item in selected_options)
            )
            time.sleep(1)

        before_count = cart_count_value(get_cart_item_count(page))

        if before_count is None:
            raise Exception("cannot read cart.js item_count before click")

        button = locate_element(page, MODULES["add_to_cart"])
        button.click(timeout=10000)

        end_time = time.time() + 10
        after_count = None

        while time.time() < end_time:
            current_count = cart_count_value(get_cart_item_count(page))
            if current_count is not None and current_count > before_count:
                after_count = current_count
                break

            error = visible_add_to_cart_error(page)
            if error:
                raise Exception(f"Add To Cart error message: {error}")

            time.sleep(0.5)

        if after_count is None:
            raise Exception("cart item_count did not increase")

        print(f"OK Add To Cart: cart item_count {before_count} -> {after_count}")

    except Exception as e:
        print(f"FAIL {e}")
        failures.append(f"Add To Cart validation failed: {e}")

    return failures


def wait_for_product_page(page):
    wait_for_page_load(page, label="PDP")
    wait_for_visible(page, MODULES["gallery"])
    wait_for_visible(page, MODULES["info"])
    wait_for_visible(page, MODULES["add_to_cart"])


def run():
    configure_context()
    failures = []
    create_dirs(BASELINE_DIR, CURRENT_DIR, DIFF_DIR)
    playwright = browser = context = page = None

    try:
        playwright, browser, context, page = init_browser()
        open_page_with_retry(page, URL, wait_for_product_page, "PDP")
        time.sleep(2)

        failures.extend(dom_check(page, MODULES))
        failures.extend(check_add_to_cart(page))
        check_variant_count(page)

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
            require_reviews=REQUIRE_REVIEWS,
            site_config=SITE_CONFIG,
            page_config=PAGE_CONFIG
        )
        variant_results, variant_failures = test_variants(page)
        failures.extend(variant_failures)
        failures.extend(test_add_to_cart(page))

        failures.extend(process_results(module_results, SITE, SUITE, PAGE))
        failures.extend(process_results(variant_results, SITE, SUITE, PAGE))

    except Exception as e:
        error = f"Playwright runtime error: {type(e).__name__}: {e}"
        failures.append(f"PDP: {error}")
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
        print("\nPDP Playwright failures")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
