from playwright_checks.core.config_loader import (
    get_dynamic_hide_config,
    get_screenshot_css,
)
from playwright_checks.utils.waits import locate_element, selector_for


def dom_check(page, modules):
    print("\nDOM checks")
    failures = []

    for name, locator in modules.items():
        try:
            element = locate_element(page, locator)
            box = element.bounding_box()
            visible = element.is_visible()
            size_ok = bool(box and box["width"] > 0 and box["height"] > 0)

            print(f"OK [{name}] visible={visible} | size_ok={size_ok}")

            if not visible or not size_ok:
                failures.append(f"DOM [{name}] visible={visible}, size_ok={size_ok}")

        except Exception as e:
            print(f"FAIL [{name}] DOM error: {e}")
            failures.append(f"DOM [{name}] error: {e}")

    return failures


def dom_presence_check(page, modules):
    if not modules:
        return []

    print("\nDOM presence checks")
    failures = []

    for name, locator in modules.items():
        selector = selector_for(locator)
        elements = page.locator(selector)

        try:
            elements.first.wait_for(state="attached", timeout=10000)
            count = elements.count()
            print(f"OK [{name}] attached=True | count={count}")

        except Exception as e:
            print(f"FAIL [{name}] DOM presence error: {e}")
            failures.append(f"DOM presence [{name}] error: {e}")

    return failures


def hide_dynamic_elements(page, site_config=None, page_config=None):
    hide_config = get_dynamic_hide_config(site_config, page_config)
    hide_config["screenshot_css"] = get_screenshot_css(site_config, page_config)

    page.evaluate("""
        (config) => {
        const selectors = config.selectors || [];
        const styleId = 'screenshot-hack';
        const customStyleId = 'screenshot-custom-css';

        if (!document.getElementById(styleId)) {
            const style = document.createElement('style');
            style.id = styleId;
            style.innerHTML = selectors.length
                ? selectors.join(',\\n') + ' { display: none !important; }'
                : '';
            document.head.appendChild(style);
        }

        let customStyle = document.getElementById(customStyleId);
        if (!customStyle) {
            customStyle = document.createElement('style');
            customStyle.id = customStyleId;
            document.head.appendChild(customStyle);
        }
        customStyle.innerHTML = config.screenshot_css || '';

        const exact = (config.text_exact || []).map(function(item) {
            return String(item).toLowerCase();
        });
        const contains = (config.text_contains || []).map(function(item) {
            return String(item).toLowerCase();
        });
        const maxLength = Number(config.text_max_length || 80);
        const containerSelector = config.container_selector || 'section, form, div';

        document.querySelectorAll('body *').forEach(function(el) {
            const text = (el.innerText || '').trim().toLowerCase();
            const compactText = text.replace(/\\s+/g, ' ');

            if (
                exact.indexOf(text) !== -1
                || contains.some(function(item) {
                    return compactText.indexOf(item) !== -1
                        && compactText.length < maxLength;
                })
            ) {
                const block = el.closest(containerSelector);

                if (block) {
                    block.style.display = 'none';
                }
            }
        });

        const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight;

        document.querySelectorAll('body *').forEach(function(el) {
            const style = window.getComputedStyle(el);
            if (style.position !== 'fixed') return;

            const rect = el.getBoundingClientRect();
            if (!rect.width || !rect.height) return;

            const isSmallFloating =
                rect.width <= 240
                && rect.height <= 240
                && rect.width < viewportWidth * 0.7
                && (
                    rect.left <= 96
                    || viewportWidth - rect.right <= 96
                    || viewportHeight - rect.bottom <= 160
                );

            if (isSmallFloating) {
                el.style.setProperty('display', 'none', 'important');
            }
        });
        }
    """, hide_config)
