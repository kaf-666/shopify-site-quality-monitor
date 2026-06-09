import sys
import time

from playwright_checks.core.driver import close_browser, init_browser
from playwright_checks.core.config_loader import get_page_config, load_site_config, locator_map
from playwright_checks.utils.waits import (
    locate_element,
    open_page_with_retry,
    wait_for_page_load,
    wait_for_visible,
)


PAGE = "product"


def configure_context():
    site_config = load_site_config()
    page_config = get_page_config(PAGE, site_config)
    modules = locator_map(page_config["modules"])
    return site_config, page_config, modules


def wait_for_product_page(page, modules):
    wait_for_page_load(page, label="PDP")
    wait_for_visible(page, modules["gallery"])
    wait_for_visible(page, modules["info"])
    wait_for_visible(page, modules["add_to_cart"])


def cart_count_value(value):
    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    return None


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


def clear_cart(page):
    return page.evaluate("""
        async () => {
            try {
                const response = await fetch('/cart/clear.js', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json' }
                });
                if (!response.ok) {
                    return { error: 'cart/clear.js HTTP ' + response.status };
                }
                const cart = await response.json();
                return cart.item_count;
            } catch (error) {
                return { error: error.message };
            }
        }
    """)


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


def run():
    _, page_config, modules = configure_context()
    failures = []
    playwright = browser = context = page = None

    try:
        playwright, browser, context, page = init_browser()
        open_page_with_retry(
            page,
            page_config["url"],
            lambda target: wait_for_product_page(target, modules),
            "PDP add-to-cart flow",
        )
        time.sleep(2)

        cleared_count = cart_count_value(clear_cart(page))
        if cleared_count is None:
            raise Exception("cannot clear cart before flow")
        print("OK cart cleared before flow")

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

        button = locate_element(page, modules["add_to_cart"])
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
        error = f"Add-to-cart flow error: {type(e).__name__}: {e}"
        print(f"FAIL {error}")
        failures.append(error)
    finally:
        if page:
            try:
                cleared_count = cart_count_value(clear_cart(page))
                if cleared_count == 0:
                    print("OK cart cleared after flow")
                else:
                    print(f"WARN cart clear returned item_count={cleared_count}")
            except Exception as e:
                print(f"WARN cart cleanup failed: {e}")
        close_browser(playwright, browser, context)

    return failures


if __name__ == "__main__":
    flow_failures = run()
    if flow_failures:
        print("\nAdd-to-cart flow failures")
        for index, failure in enumerate(flow_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if flow_failures else 0)
