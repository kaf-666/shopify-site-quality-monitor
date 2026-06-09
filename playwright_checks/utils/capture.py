import time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playwright_checks.utils.dom import hide_dynamic_elements
from playwright_checks.utils.waits import build_paths, locate_element


def wait_for_images(element, timeout=10):
    end_time = time.time() + timeout

    while time.time() < end_time:
        ready = element.evaluate("""
            (container) => {
                const imgs = container.querySelectorAll('img');

                if (imgs.length === 0) return true;

                for (let i = 0; i < imgs.length; i++) {
                    const img = imgs[i];

                    if (img.loading === 'lazy') {
                        img.loading = 'eager';
                    }

                    if (img.dataset.src && !img.getAttribute('src')) {
                        img.src = img.dataset.src;
                    }

                    if (img.dataset.srcset && !img.getAttribute('srcset')) {
                        img.srcset = img.dataset.srcset;
                    }

                    if (!img.complete || img.naturalWidth === 0) {
                        return false;
                    }
                }

                return true;
            }
        """)

        if ready:
            time.sleep(0.3)
            return True

        time.sleep(0.3)

    print(f"      wait for images timeout ({timeout}s)")
    return False


def wait_for_reviews(page, timeout=10):
    end_time = time.time() + timeout

    while time.time() < end_time:
        ready = page.evaluate("""
            () => {
                const all = document.querySelectorAll('.alireviews-review-star-rating');

                if (all.length === 0) return true;

                for (let i = 0; i < all.length; i++) {
                    if (all[i].getAttribute('data-status') !== 'initialized') {
                        return false;
                    }
                }

                return true;
            }
        """)

        if ready:
            time.sleep(0.3)
            return True

        time.sleep(0.3)

    print(f"      wait for reviews timeout ({timeout}s)")
    return False


def wait_for_layout_stable(element, timeout=10, check_interval=0.5):
    previous_size = None
    stable_count = 0
    required_stable = 2
    end_time = time.time() + timeout

    while time.time() < end_time:
        box = element.bounding_box()
        current_size = None
        if box:
            current_size = {
                "width": round(box["width"]),
                "height": round(box["height"]),
            }

        if current_size == previous_size and current_size is not None:
            stable_count += 1
            if stable_count >= required_stable:
                return True
        else:
            stable_count = 0

        previous_size = current_size
        time.sleep(check_interval)

    print(
        f"      layout is not stable ({timeout}s), "
        f"current_size={previous_size}"
    )
    return False


def wait_for_capture_ready(page, element, require_reviews=True, timeout=10):
    if not wait_for_images(element, timeout=timeout):
        raise Exception("wait for images timeout")

    if require_reviews and not wait_for_reviews(page, timeout=timeout):
        print("      reviews not ready; continue capture")

    if not wait_for_layout_stable(element, timeout=timeout):
        raise Exception("layout is not stable")


def screenshot_element_with_retry(locate, output_path, prepare=None, attempts=3, delay=1):
    start = time.perf_counter()
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            element = locate()

            if prepare:
                prepare(element)

            element = locate()
            element.screenshot(path=output_path)

            return {
                "capture_duration_ms": round(
                    (time.perf_counter() - start) * 1000,
                    2
                ),
                "capture_attempts": attempt,
            }

        except (PlaywrightError, PlaywrightTimeoutError, Exception) as e:
            last_error = e

            if attempt == attempts:
                break

            print(
                f"      screenshot retry {attempt}/{attempts}: "
                f"{type(e).__name__}"
            )
            time.sleep(delay)

    raise last_error


def scroll_to_center(element):
    element.scroll_into_view_if_needed(timeout=10000)
    time.sleep(0.5)


def capture_modules(
    page,
    modules,
    current_dir,
    baseline_dir,
    diff_dir,
    require_reviews=True,
    site_config=None,
    page_config=None,
    before_capture=None
):
    print("\nModule screenshots")
    results = {}

    for name, locator in modules.items():
        paths = build_paths(
            current_dir,
            baseline_dir,
            diff_dir,
            name
        )

        def locate(locator=locator):
            return locate_element(page, locator)

        def prepare(element):
            scroll_to_center(element)
            hide_dynamic_elements(page, site_config, page_config)
            if before_capture:
                before_capture(name, page, element)
            wait_for_capture_ready(
                page,
                element,
                require_reviews=require_reviews,
                timeout=10
            )
            time.sleep(1)
            hide_dynamic_elements(page, site_config, page_config)
            if before_capture:
                before_capture(name, page, element)

        try:
            metrics = screenshot_element_with_retry(
                locate,
                paths["current"],
                prepare=prepare,
                attempts=3,
                delay=1
            )
            paths.update(metrics)
            results[name] = paths

            print(
                f"OK [{name}] "
                f"{paths['capture_duration_ms']}ms "
                f"attempts={paths['capture_attempts']}"
            )

        except Exception as e:
            print(f"FAIL [{name}] capture failed: {e}")
            results[name] = {
                "error": f"capture failed: {e}",
                "capture_duration_ms": None,
                "capture_attempts": 3,
            }

    return results
