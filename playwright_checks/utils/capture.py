import json
import os
import time

from PIL import Image, ImageChops
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from playwright_checks.artifacts.dynamic import (
    audit_dynamic_region,
    audit_page_dynamic_masks,
    dynamic_region_for_case,
)
from playwright_checks.utils.dom import hide_dynamic_elements
from playwright_checks.utils.waits import build_paths, locate_element


IMAGE_STABILITY_SCRIPT = """
    async (container, options) => {
        const images = Array.from(container.querySelectorAll('img'));
        const tracked = [];

        for (const img of images) {
            const rect = img.getBoundingClientRect();
            const style = window.getComputedStyle(img);
            const rendered =
                rect.width > 0
                && rect.height > 0
                && style.display !== 'none'
                && style.visibility !== 'hidden'
                && style.opacity !== '0';

            const inViewport =
                rect.bottom > 0
                && rect.right > 0
                && rect.top < window.innerHeight
                && rect.left < window.innerWidth;

            if (options.visibleOnly && !rendered) continue;
            if (options.viewportOnly && (!rendered || !inViewport)) continue;

            if (img.loading === 'lazy') img.loading = 'eager';
            if (img.dataset.src && !img.getAttribute('src')) {
                img.src = img.dataset.src;
            }
            if (img.dataset.srcset && !img.getAttribute('srcset')) {
                img.srcset = img.dataset.srcset;
            }

            tracked.push(img);
        }

        await Promise.all(tracked.map(async (img) => {
            if (!img.complete || img.naturalWidth === 0) return;
            if (typeof img.decode !== 'function') return;
            try {
                await img.decode();
            } catch (_) {
                // A rejected decode is handled by the readiness check below.
            }
        }));

        await new Promise((resolve) => requestAnimationFrame(
            () => requestAnimationFrame(resolve)
        ));

        const ready = tracked.every(
            (img) => img.complete && img.naturalWidth > 0
        );
        const signature = tracked.map((img) => {
            const rect = img.getBoundingClientRect();
            return [
                img.currentSrc || img.src || '',
                img.naturalWidth,
                img.naturalHeight,
                Math.round(rect.width * 100) / 100,
                Math.round(rect.height * 100) / 100,
            ];
        });

        return {ready, signature};
    }
"""


def _wait_for_stable_images(
    element,
    timeout=10,
    visible_only=False,
    viewport_only=False,
    label="images",
):
    end_time = time.time() + timeout
    previous_signature = None
    stable_count = 0
    required_stable = 2

    while time.time() < end_time:
        state = element.evaluate(
            IMAGE_STABILITY_SCRIPT,
            {
                "visibleOnly": bool(visible_only),
                "viewportOnly": bool(viewport_only),
            },
        )
        ready = bool(state.get("ready"))
        signature = state.get("signature")

        if ready and signature == previous_signature:
            stable_count += 1
            if stable_count >= required_stable:
                time.sleep(0.2)
                return True
        else:
            stable_count = 0

        previous_signature = signature if ready else None
        time.sleep(0.3)

    print(f"      wait for {label} timeout ({timeout}s)")
    return False


def wait_for_images(element, timeout=10):
    return _wait_for_stable_images(
        element,
        timeout=timeout,
        visible_only=False,
        label="images",
    )


def wait_for_visible_images(element, timeout=10):
    return _wait_for_stable_images(
        element,
        timeout=timeout,
        visible_only=True,
        label="visible images",
    )


def wait_for_viewport_images(element, timeout=10):
    return _wait_for_stable_images(
        element,
        timeout=timeout,
        visible_only=True,
        viewport_only=True,
        label="viewport images",
    )


def wait_for_rendered_page_images(page, timeout=30):
    """Wait for every rendered image, including ones activated by page scrolling."""
    body = page.locator("body").first
    return _wait_for_stable_images(
        body,
        timeout=timeout,
        visible_only=True,
        label="rendered page images",
    )


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


def disable_motion(page):
    page.evaluate("""
        () => {
            const styleId = 'visual-regression-disable-motion';
            if (!document.getElementById(styleId)) {
                const style = document.createElement('style');
                style.id = styleId;
                style.textContent = `
                    *, *::before, *::after {
                        animation-duration: 0s !important;
                        animation-delay: 0s !important;
                        transition-duration: 0s !important;
                        transition-delay: 0s !important;
                        scroll-behavior: auto !important;
                    }
                `;
                document.head.appendChild(style);
            }
        }
    """)


def prepare_for_screenshot(
    page,
    element,
    site_config=None,
    page_config=None,
    require_reviews=True,
    timeout=10,
    settle_delay=1,
    before_capture=None,
    hide_dynamic=True,
):
    scroll_to_center(element)
    disable_motion(page)
    if hide_dynamic:
        hide_dynamic_elements(page, site_config, page_config)

    if before_capture:
        before_capture(element)

    wait_for_capture_ready(
        page,
        element,
        require_reviews=require_reviews,
        timeout=timeout,
    )

    if settle_delay:
        time.sleep(settle_delay)

    disable_motion(page)
    if hide_dynamic:
        hide_dynamic_elements(page, site_config, page_config)

    if before_capture:
        before_capture(element)


def first_screen_crop_box(page_config, page):
    clip_config = page_config.get("first_screen_clip") if page_config else None
    if not clip_config:
        return None

    if not isinstance(clip_config, dict):
        raise ValueError("first_screen_clip must be a mapping")

    x = int(float(clip_config.get("x", 0)))
    y = int(float(clip_config.get("y", 0)))
    width = int(float(clip_config.get("width", 0)))
    height = int(float(clip_config.get("height", 0)))

    if width <= 0 or height <= 0:
        raise ValueError("first_screen_clip width and height must be positive")

    pixel_ratio = float(page.evaluate("() => window.devicePixelRatio || 1"))
    return (
        int(x * pixel_ratio),
        int(y * pixel_ratio),
        int((x + width) * pixel_ratio),
        int((y + height) * pixel_ratio),
    )


def crop_image(path, crop_box):
    if not crop_box:
        return False

    with Image.open(path).convert("RGB") as image:
        x1, y1, x2, y2 = crop_box
        x1 = max(0, min(x1, image.width))
        y1 = max(0, min(y1, image.height))
        x2 = max(x1, min(x2, image.width))
        y2 = max(y1, min(y2, image.height))

        if (x1, y1, x2, y2) == (0, 0, image.width, image.height):
            return False

        cropped = image.crop((x1, y1, x2, y2))
        cropped.save(path)
        return True


def crop_bottom_whitespace(path, threshold=248, margin=24):
    with Image.open(path).convert("RGB") as image:
        white = Image.new("RGB", image.size, (255, 255, 255))
        diff = ImageChops.difference(image, white)
        tolerance = max(0, 255 - int(threshold))
        mask = diff.convert("L").point(lambda pixel: 255 if pixel > tolerance else 0)
        bbox = mask.getbbox()

        if not bbox:
            return False

        bottom = min(image.height, bbox[3] + int(margin))
        if bottom >= image.height:
            return False

        cropped = image.crop((0, 0, image.width, bottom))
        cropped.save(path)
        return True


def _selector_list(value):
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    raise ValueError(f"selector list must be a string or list, got: {value!r}")


def hide_global_screenshot_elements(page, site_config=None, page_config=None):
    selectors = []
    css_blocks = []
    if site_config:
        selectors.extend(
            _selector_list(site_config.get("global_screenshot_hide_selectors"))
        )
        css_blocks.extend(
            _selector_list(site_config.get("global_screenshot_css"))
        )
    if page_config:
        selectors.extend(
            _selector_list(page_config.get("global_screenshot_hide_selectors"))
        )
        css_blocks.extend(
            _selector_list(page_config.get("global_screenshot_css"))
        )

    seen = set()
    selectors = [
        selector
        for selector in selectors
        if selector and not (selector in seen or seen.add(selector))
    ]

    page.evaluate("""
        ({ selectors, css }) => {
            const styleId = 'global-screenshot-hide';
            let style = document.getElementById(styleId);
            if (!style) {
                style = document.createElement('style');
                style.id = styleId;
                document.head.appendChild(style);
            }
            style.innerHTML = selectors.length
                ? selectors.join(',\\n') + ' { display: none !important; }'
                : '';

            const cssStyleId = 'global-screenshot-css';
            let cssStyle = document.getElementById(cssStyleId);
            if (!cssStyle) {
                cssStyle = document.createElement('style');
                cssStyle.id = cssStyleId;
                document.head.appendChild(cssStyle);
            }
            cssStyle.textContent = css.join('\\n');
        }
    """, {"selectors": selectors, "css": css_blocks})


def clear_global_screenshot_elements(page):
    page.evaluate("""
        () => {
            ['global-screenshot-hide', 'global-screenshot-css'].forEach((id) => {
                const style = document.getElementById(id);
                if (style) style.remove();
            });
        }
    """)


def prepare_first_screen(
    page,
    site_config=None,
    page_config=None,
    timeout=10,
    settle_delay=0.5,
    before_capture=None,
):
    page.evaluate("() => window.scrollTo(0, 0)")
    disable_motion(page)
    hide_dynamic_elements(page, site_config, page_config)

    if before_capture:
        before_capture(page)

    body = page.locator("body").first
    body.wait_for(state="visible", timeout=10000)

    if page_config and page_config.get("first_screen_wait_for_images"):
        if not wait_for_viewport_images(body, timeout=timeout):
            raise Exception("wait for viewport images timeout")

    if page_config and page_config.get("first_screen_wait_for_fonts"):
        page.evaluate("""
            () => document.fonts ? document.fonts.ready.then(() => true) : true
        """)

    if not wait_for_layout_stable(body, timeout=timeout):
        raise Exception("layout is not stable")

    if settle_delay:
        time.sleep(settle_delay)

    page.evaluate("() => window.scrollTo(0, 0)")
    disable_motion(page)
    hide_dynamic_elements(page, site_config, page_config)

    if before_capture:
        before_capture(page)


def page_scroll_metrics(page):
    return page.evaluate("""
        () => {
            const doc = document.scrollingElement || document.documentElement;
            const body = document.body || document.documentElement;
            return {
                scrollHeight: Math.max(
                    doc.scrollHeight,
                    body.scrollHeight,
                    document.documentElement.scrollHeight
                ),
                viewportHeight: window.innerHeight || document.documentElement.clientHeight,
            };
        }
    """)


def global_screenshot_option(site_config, page_config, key, default=None):
    """Resolve global-capture options with page settings taking precedence."""

    if page_config and key in page_config:
        return page_config[key]
    if site_config and key in site_config:
        return site_config[key]
    return default


def wait_for_scroll_height_stable(
    page,
    timeout=10,
    check_interval=0.5,
    required_stable=4,
    min_height=None,
):
    previous_height = None
    stable_count = 0
    end_time = time.time() + timeout

    while time.time() < end_time:
        metrics = page_scroll_metrics(page)
        current_height = round(metrics.get("scrollHeight") or 0)

        if min_height and current_height < min_height:
            stable_count = 0
        elif current_height == previous_height and current_height > 0:
            stable_count += 1
            if stable_count >= required_stable:
                return True
        else:
            stable_count = 0

        previous_height = current_height
        time.sleep(check_interval)

    print(
        f"      scroll height is not stable ({timeout}s), "
        f"current_height={previous_height}, min_height={min_height}"
    )
    return False


def scroll_page_to_load_lazy_content(
    page,
    site_config=None,
    page_config=None,
    max_scrolls=80,
    delay=0.2,
    max_passes=3,
):
    """Walk the full document until lazy loading stops extending it."""

    for _ in range(max(1, int(max_passes))):
        metrics = page_scroll_metrics(page)
        viewport_height = max(1, int(metrics.get("viewportHeight") or 1))
        starting_height = max(viewport_height, int(metrics.get("scrollHeight") or 0))
        step = max(200, int(viewport_height * 0.8))
        positions = list(range(0, starting_height + step, step))[:max_scrolls]

        for y in positions:
            page.evaluate("(y) => window.scrollTo(0, y)", y)
            disable_motion(page)
            hide_dynamic_elements(page, site_config, page_config)
            hide_global_screenshot_elements(page, site_config, page_config)
            time.sleep(delay)

        page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(delay)
        ending_height = int(page_scroll_metrics(page).get("scrollHeight") or 0)
        if ending_height <= starting_height:
            break

    page.evaluate("() => window.scrollTo(0, 0)")
    time.sleep(delay)


def global_screenshot_target_height(page, paths, site_config=None, page_config=None):
    """Return the target PNG height for a baseline-height strategy."""

    strategy = str(
        global_screenshot_option(
            site_config,
            page_config,
            "global_screenshot_height_strategy",
            "stable",
        )
    ).strip().lower()

    if strategy in ("", "stable", "natural"):
        return None, strategy
    if strategy != "baseline":
        raise ValueError(
            "global_screenshot_height_strategy must be 'stable', 'natural', "
            f"or 'baseline', got: {strategy!r}"
        )

    baseline_path = paths.get("baseline")
    if not baseline_path or not os.path.exists(baseline_path):
        print("      global height strategy=baseline; baseline missing, using natural height")
        return None, strategy

    with Image.open(baseline_path) as baseline:
        baseline_height = baseline.height

    metrics = page_scroll_metrics(page)
    current_height = int(metrics.get("scrollHeight") or 0)
    print(
        "      global height strategy=baseline "
        f"target={baseline_height}px current_css={current_height}px"
    )
    return baseline_height, strategy


def normalize_image_height(path, target_height):
    """White-pad a PNG to the baseline height without discarding page content."""

    with Image.open(path).convert("RGB") as image:
        # A baseline is useful for keeping a shorter page comparable, but it
        # must never act as a maximum height.  Full-page screenshots that grow
        # past an older baseline contain real content near the bottom; cropping
        # them made the resulting "global" image incomplete.
        if image.height >= target_height:
            return False

        normalized = Image.new("RGB", (image.width, target_height), "white")
        normalized.paste(image, (0, 0))
        normalized.save(path)
        return True


def prepare_global_screenshot(
    page,
    site_config=None,
    page_config=None,
    timeout=15,
    settle_delay=0.5,
    before_capture=None,
):
    page.evaluate("() => window.scrollTo(0, 0)")
    disable_motion(page)
    hide_dynamic_elements(page, site_config, page_config)
    hide_global_screenshot_elements(page, site_config, page_config)

    if before_capture:
        before_capture(page)

    body = page.locator("body").first
    body.wait_for(state="visible", timeout=10000)

    scroll_page_to_load_lazy_content(
        page,
        site_config=site_config,
        page_config=page_config,
        max_scrolls=global_screenshot_option(
            site_config, page_config, "global_screenshot_max_scrolls", 80
        ),
        delay=global_screenshot_option(
            site_config, page_config, "global_screenshot_scroll_delay", 0.2
        ),
        max_passes=global_screenshot_option(
            site_config, page_config, "global_screenshot_scroll_passes", 3
        ),
    )

    if not wait_for_scroll_height_stable(
        page,
        timeout=global_screenshot_option(
            site_config, page_config, "global_screenshot_height_timeout", timeout
        ),
        required_stable=global_screenshot_option(
            site_config, page_config, "global_screenshot_height_stable_checks", 4
        ),
        min_height=global_screenshot_option(
            site_config, page_config, "global_screenshot_min_height"
        ),
    ):
        raise Exception("scroll height is not stable")

    scroll_page_to_load_lazy_content(
        page,
        site_config=site_config,
        page_config=page_config,
        max_scrolls=global_screenshot_option(
            site_config, page_config, "global_screenshot_max_scrolls", 80
        ),
        delay=global_screenshot_option(
            site_config, page_config, "global_screenshot_scroll_delay", 0.2
        ),
        max_passes=global_screenshot_option(
            site_config, page_config, "global_screenshot_scroll_passes", 3
        ),
    )

    if global_screenshot_option(
        site_config, page_config, "global_screenshot_wait_for_images", True
    ):
        image_timeout = global_screenshot_option(
            site_config,
            page_config,
            "global_screenshot_image_timeout",
            max(timeout, 30),
        )
        if not wait_for_rendered_page_images(page, timeout=image_timeout):
            raise Exception("wait for rendered page images timeout")
        if not wait_for_scroll_height_stable(
            page,
            timeout=global_screenshot_option(
                site_config, page_config, "global_screenshot_height_timeout", timeout
            ),
            required_stable=global_screenshot_option(
                site_config, page_config, "global_screenshot_height_stable_checks", 4
            ),
            min_height=global_screenshot_option(
                site_config, page_config, "global_screenshot_min_height"
            ),
        ):
            raise Exception("scroll height changed after image loading")

    if global_screenshot_option(
        site_config, page_config, "global_screenshot_wait_for_fonts", False
    ):
        page.evaluate("""
            () => document.fonts ? document.fonts.ready.then(() => true) : true
        """)

    if not wait_for_layout_stable(body, timeout=timeout):
        raise Exception("layout is not stable")

    if settle_delay:
        time.sleep(settle_delay)

    page.evaluate("() => window.scrollTo(0, 0)")
    disable_motion(page)
    hide_dynamic_elements(page, site_config, page_config)
    hide_global_screenshot_elements(page, site_config, page_config)

    if before_capture:
        before_capture(page)


def capture_global_screenshot(ctx, page, before_capture=None):
    if (
        not ctx.page_config.get("capture_global_screenshot")
        or not ctx.case_is_captured("global")
    ):
        return {}

    print("\nGlobal screenshots")
    name = "global"
    paths = build_paths(
        ctx.current_dir,
        ctx.baseline_dir,
        ctx.diff_dir,
        name,
        legacy_baseline_dir=ctx.legacy_baseline_dir,
    )
    paths["report_case"] = ctx.screenshot_policy(name)["report_case"]
    capture_start = time.perf_counter()

    try:
        prepare_global_screenshot(
            page,
            site_config=ctx.site_config,
            page_config=ctx.page_config,
            timeout=global_screenshot_option(
                ctx.site_config, ctx.page_config, "global_screenshot_timeout", 15
            ),
            settle_delay=global_screenshot_option(
                ctx.site_config,
                ctx.page_config,
                "global_screenshot_settle_delay",
                0.5,
            ),
            before_capture=before_capture,
        )
        target_height, height_strategy = global_screenshot_target_height(
            page,
            paths,
            site_config=ctx.site_config,
            page_config=ctx.page_config,
        )
        paths.update(
            audit_page_dynamic_masks(
                page,
                ctx.page_config,
                full_page=True,
            )
        )
        ctx.artifact_manager.capture_page(
            page,
            paths["current"],
            full_page=True,
        )
        if target_height:
            normalize_image_height(paths["current"], target_height)
        with Image.open(paths["current"]) as captured_image:
            paths["capture_height_px"] = captured_image.height
        paths["capture_height_strategy"] = height_strategy

        if ctx.page_config.get("global_screenshot_crop_bottom_whitespace"):
            paths["cropped_bottom_whitespace"] = crop_bottom_whitespace(
                paths["current"],
                threshold=ctx.page_config.get(
                    "global_screenshot_crop_white_threshold",
                    248,
                ),
                margin=ctx.page_config.get("global_screenshot_crop_bottom_margin", 24),
            )

        paths["capture_duration_ms"] = round(
            (time.perf_counter() - capture_start) * 1000,
            2
        )
        paths["capture_attempts"] = 1
        print(
            f"OK [{name}] "
            f"{paths['capture_duration_ms']}ms "
            f"attempts={paths['capture_attempts']}"
        )
        return {name: paths}

    except Exception as e:
        print(f"FAIL [{name}] capture failed: {e}")
        return {
            name: {
                "error": f"capture failed: {e}",
                "capture_duration_ms": round(
                    (time.perf_counter() - capture_start) * 1000,
                    2
                ),
                "capture_attempts": 1,
            }
        }
    finally:
        try:
            clear_global_screenshot_elements(page)
        except Exception:
            pass


def capture_first_screen(ctx, page, before_capture=None):
    if (
        not ctx.page_config.get("capture_first_screen")
        or not ctx.case_is_captured("first_screen")
    ):
        return {}

    print("\nViewport screenshots")
    name = "first_screen"
    paths = build_paths(
        ctx.current_dir,
        ctx.baseline_dir,
        ctx.diff_dir,
        name,
        legacy_baseline_dir=ctx.legacy_baseline_dir,
    )
    paths["report_case"] = ctx.screenshot_policy(name)["report_case"]
    capture_start = time.perf_counter()

    try:
        prepare_first_screen(
            page,
            site_config=ctx.site_config,
            page_config=ctx.page_config,
            timeout=ctx.page_config.get("first_screen_timeout", 10),
            settle_delay=ctx.page_config.get("first_screen_settle_delay", 0.5),
            before_capture=before_capture,
        )
        paths.update(
            audit_page_dynamic_masks(
                page,
                ctx.page_config,
                full_page=False,
            )
        )
        ctx.artifact_manager.capture_page(
            page,
            paths["current"],
            full_page=False,
        )
        crop_box = first_screen_crop_box(ctx.page_config, page)
        paths["cropped_first_screen"] = crop_image(paths["current"], crop_box)

        if ctx.page_config.get("first_screen_crop_bottom_whitespace"):
            paths["cropped_bottom_whitespace"] = crop_bottom_whitespace(
                paths["current"],
                threshold=ctx.page_config.get(
                    "first_screen_crop_white_threshold",
                    248,
                ),
                margin=ctx.page_config.get("first_screen_crop_bottom_margin", 24),
            )

        paths["capture_duration_ms"] = round(
            (time.perf_counter() - capture_start) * 1000,
            2
        )
        paths["capture_attempts"] = 1
        print(
            f"OK [{name}] "
            f"{paths['capture_duration_ms']}ms "
            f"attempts={paths['capture_attempts']}"
        )
        return {name: paths}

    except Exception as e:
        print(f"FAIL [{name}] capture failed: {e}")
        return {
            name: {
                "error": f"capture failed: {e}",
                "capture_duration_ms": round(
                    (time.perf_counter() - capture_start) * 1000,
                    2
                ),
                "capture_attempts": 1,
            }
        }


def screenshot_element_with_retry(
    locate,
    output_path,
    prepare=None,
    attempts=3,
    delay=1,
    artifact_manager=None,
):
    start = time.perf_counter()
    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            element = locate()

            if prepare:
                prepare(element)

            element = locate()
            if artifact_manager is not None:
                artifact_manager.capture_element(
                    element,
                    output_path,
                )
            else:
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
    before_capture=None,
    legacy_baseline_dir=None,
    artifact_manager=None,
):
    print("\nModule screenshots")
    results = {}

    for name, locator in modules.items():
        paths = build_paths(
            current_dir,
            baseline_dir,
            diff_dir,
            name,
            legacy_baseline_dir=legacy_baseline_dir,
        )

        def locate(locator=locator):
            return locate_element(page, locator)

        def prepare(element):
            prepare_for_screenshot(
                page,
                element,
                site_config=site_config,
                page_config=page_config,
                require_reviews=require_reviews,
                timeout=10,
                settle_delay=1,
                before_capture=(
                    (lambda prepared: before_capture(name, page, prepared))
                    if before_capture
                    else None
                ),
            )

        try:
            metrics = screenshot_element_with_retry(
                locate,
                paths["current"],
                prepare=prepare,
                attempts=3,
                delay=1,
                artifact_manager=artifact_manager,
            )
            paths.update(metrics)
            dynamic_region = dynamic_region_for_case(
                page_config,
                name,
            )
            if dynamic_region:
                dynamic_audit = audit_dynamic_region(
                    locate(),
                    dynamic_region,
                    page_config=page_config,
                )
                paths.update(dynamic_audit)
                print(
                    "      dynamic_structure="
                    + json.dumps(
                        dynamic_audit.get(
                            "structural_diagnostics",
                            {},
                        ),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
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
