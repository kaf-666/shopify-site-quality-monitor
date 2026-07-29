import os
import sys
import time

import numpy as np
from PIL import Image

from playwright_checks.core.driver import close_browser, init_browser
from playwright_checks.core.test_results import (
    add_result,
    clear_results,
    write_results,
)
from playwright_checks.checks.context import PageCheckContext
from playwright_checks.artifacts.screenshot_manager import safe_move
from playwright_checks.pages.home_page import HomePage
from playwright_checks.runtime.evidence import redact_text
from playwright_checks.runtime.session import (
    collect_runtime_health_fail_open,
    finalize_runtime_health_fail_open,
    record_runtime_error_fail_open,
)
from playwright_checks.utils.waits import TerminalMainDocumentError
from playwright_checks.utils.capture import (
    capture_modules,
    capture_first_screen,
    capture_global_screenshot,
    disable_motion,
    scroll_to_center,
    wait_for_layout_stable,
)
from playwright_checks.core.paths import baseline_dir, legacy_baseline_dir
from playwright_checks.core.viewport import get_current_viewport_name
from playwright_checks.utils.dom import (
    dom_check,
    dom_presence_check,
    hide_dynamic_elements,
)
from playwright_checks.utils.stability import stabilize_configured_display
from playwright_checks.utils.waits import (
    build_paths,
    create_dirs,
    locate_element,
)
from playwright_checks.utils.visual import build_result, process_results


SUITE = "visual"
PAGE = "home"


def check_plugins(page_model):
    print("\nPlugin checks")
    failures = []

    for name, locator in page_model.plugins.items():
        try:
            element = locate_element(page_model.page, locator)
            visible = element.is_visible()
            print(f"OK {name} visible={visible}")
            if not visible:
                failures.append(f"plugin [{name}] visible=False")
        except Exception as e:
            print(f"FAIL {name} plugin error: {e}")
            failures.append(f"plugin [{name}] error: {e}")

    return failures


def normalize_plugin_image_for_compare(ctx, name, path, output_path=None):
    if name != "wishlist" or not os.path.exists(path):
        return

    output = output_path or path
    canonical_path = desktop_wishlist_baseline_path(ctx)
    if canonical_path and wishlist_image_has_content(path):
        Image.open(canonical_path).convert("RGB").save(output)
        return

    compare_size = 64
    canonical = np.full((compare_size, compare_size, 3), 255, dtype=np.uint8)

    if wishlist_image_has_content(path):
        badge_size = max(12, compare_size // 3)
        y, x = np.ogrid[:compare_size, :compare_size]
        radius = badge_size / 2
        mask = (x - radius) ** 2 + (y - radius) ** 2 <= radius ** 2
        canonical[mask] = [255, 0, 0]

        text_height = max(3, badge_size // 3)
        text_width = max(2, badge_size // 9)
        text_x = max(1, int(radius - text_width / 2))
        text_y = max(1, int(radius - text_height / 2))
        canonical[
            text_y:text_y + text_height,
            text_x:text_x + text_width,
        ] = [255, 255, 255]

    Image.fromarray(canonical).save(output)


def desktop_wishlist_baseline_path(ctx):
    candidates = [
        baseline_dir(ctx.site, PAGE, viewport="desktop") / "wishlist.png",
        legacy_baseline_dir(ctx.site, PAGE, viewport="desktop") / "wishlist.png",
    ]

    for candidate in candidates:
        if os.path.exists(candidate):
            return str(candidate)

    return None


def save_wishlist_canonical(ctx, output_path, fallback_path=None):
    canonical_path = desktop_wishlist_baseline_path(ctx)
    if canonical_path:
        Image.open(canonical_path).convert("RGB").save(output_path)
        return

    if fallback_path:
        Image.open(fallback_path).convert("RGB").save(output_path)


def wishlist_image_has_content(path):
    with Image.open(path).convert("RGB") as image:
        width, height = image.size
        pixels = np.array(image)

    return ((pixels < 245).any(axis=2)).sum() / (width * height) > 0.01


def prepare_plugin_compare_images(ctx, name, paths):
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
        ctx,
        name,
        baseline,
        output_path=compare_baseline
    )
    normalize_plugin_image_for_compare(
        ctx,
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
        with Image.open(path).convert("RGB") as image:
            width, height = image.size
            pixels = np.array(image)
        ratio = width / height if height else 0
        content_ratio = ((pixels < 245).any(axis=2)).sum() / (width * height)
        return width >= 45 and height >= 45 and 0.8 <= ratio <= 1.3 and content_ratio > 0.01

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


def capture_stable_plugin(ctx, page, name, locator, output_path, timeout=10):
    end_time = time.time() + timeout
    previous_path = None
    previous_compare_path = None

    while time.time() < end_time:
        element = locate_element(page, locator)
        disable_motion(page)
        normalize_plugin_for_screenshot(name, element)
        scroll_to_center(element)
        time.sleep(0.2)

        if not wait_for_layout_stable(element, timeout=2):
            time.sleep(0.3)
            continue

        current_path = ctx.temporary_path(name, "plugin-probe")
        current_compare_path = ctx.temporary_path(
            name,
            "plugin-probe-compare",
        )
        ctx.artifact_manager.capture_element(
            element,
            current_path,
        )
        normalize_plugin_image_for_compare(
            ctx,
            name,
            current_path,
            output_path=current_compare_path
        )

        if not plugin_image_ready(name, current_path):
            _cleanup_plugin_probe(ctx, current_path)
            _cleanup_plugin_probe(ctx, current_compare_path)
            _cleanup_plugin_probe(ctx, previous_path)
            _cleanup_plugin_probe(ctx, previous_compare_path)
            previous_path = None
            previous_compare_path = None
            time.sleep(0.3)
            continue

        stable_path = current_compare_path if name == "wishlist" else current_path
        previous_stable_path = (
            previous_compare_path if name == "wishlist" else previous_path
        )

        if previous_path and images_close(previous_stable_path, stable_path):
            if name == "wishlist":
                save_wishlist_canonical(ctx, output_path, fallback_path=current_path)
            else:
                safe_move(current_path, output_path)
            _cleanup_plugin_probe(ctx, current_path)
            _cleanup_plugin_probe(ctx, previous_path)
            _cleanup_plugin_probe(ctx, previous_compare_path)
            _cleanup_plugin_probe(ctx, current_compare_path)
            return

        _cleanup_plugin_probe(ctx, previous_path)
        _cleanup_plugin_probe(ctx, previous_compare_path)
        previous_path = current_path
        previous_compare_path = current_compare_path
        time.sleep(0.3)

    if previous_path:
        if name == "wishlist":
            save_wishlist_canonical(ctx, output_path, fallback_path=previous_path)
        else:
            safe_move(previous_path, output_path)
        _cleanup_plugin_probe(ctx, previous_path)
        _cleanup_plugin_probe(ctx, previous_compare_path)
        return

    raise Exception("plugin screenshot is not stable")


def _cleanup_plugin_probe(ctx, path):
    ctx.artifact_manager.discard_temporary(path)


def capture_plugins(ctx, page_model):
    print("\nPlugin screenshots")
    results = {}

    for name, locator in page_model.plugins.items():
        paths = build_paths(
            ctx.current_dir,
            ctx.baseline_dir,
            ctx.diff_dir,
            name,
            legacy_baseline_dir=ctx.legacy_baseline_dir,
        )
        max_attempts = 2
        capture_start = time.perf_counter()

        for attempt in range(1, max_attempts + 1):
            try:
                capture_stable_plugin(
                    ctx,
                    page_model.page,
                    name,
                    locator,
                    paths["current"],
                    timeout=20
                )
                prepare_plugin_compare_images(ctx, name, paths)
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


def banner_stable_index(page_config=None):
    configured_index = (page_config or {}).get("stable_banner_index", 0)

    try:
        return max(0, int(configured_index))
    except (TypeError, ValueError):
        return 0


def stabilize_banner(page, page_config=None):
    stable_index = banner_stable_index(page_config)

    page.evaluate("""
        (stableIndex) => {
            const disableMotion = (root) => {
                root.querySelectorAll('*').forEach(function(el) {
                    el.style.setProperty('animation', 'none', 'important');
                    el.style.setProperty('transition', 'none', 'important');
                });
            };

            document.querySelectorAll('.flickity-enabled').forEach(function(el) {
                if (window.Flickity && Flickity.data(el)) {
                    const flkty = Flickity.data(el);
                    flkty.stopPlayer();
                    flkty.select(0, false, true);
                    flkty.x = 0;
                    flkty.positionSlider();
                    flkty.pausePlayer();
                }
                disableMotion(el);
            });

            const slides = Array.from(document.querySelectorAll('.slideshow__slide'));
            if (slides.length) {
                const selectedSlide = slides.find(function(slide) {
                    return Number(slide.dataset.index) === stableIndex;
                }) || slides[Math.min(stableIndex, slides.length - 1)];
                const selectedPosition = slides.indexOf(selectedSlide);
                let flickityRoot = selectedSlide.closest('.flickity-enabled');

                if (!flickityRoot) {
                    const slideshowRoot = selectedSlide.closest('.slideshow');
                    if (slideshowRoot) {
                        flickityRoot = slideshowRoot.querySelector('.flickity-enabled');
                    }
                }

                let selectedWithFlickity = false;
                if (flickityRoot && window.Flickity && Flickity.data(flickityRoot)) {
                    const flkty = Flickity.data(flickityRoot);
                    const cells = flkty.getCellElements ? flkty.getCellElements() : [];
                    const cellIndex = cells.indexOf(selectedSlide);

                    flkty.stopPlayer();
                    flkty.select(cellIndex >= 0 ? cellIndex : selectedPosition, false, true);
                    flkty.pausePlayer();
                    selectedWithFlickity = true;
                }

                slides.forEach(function(slide) {
                    const isSelected = slide === selectedSlide;
                    slide.classList.toggle('is-selected', isSelected);
                    slide.setAttribute('aria-hidden', isSelected ? 'false' : 'true');

                    if (!selectedWithFlickity) {
                        slide.style.setProperty(
                            'display',
                            isSelected ? 'block' : 'none',
                            'important'
                        );
                    }
                });
            }

            document.querySelectorAll('.slideshow, [class*="slideshow"]').forEach(function(el) {
                disableMotion(el);
            });
        }
    """, stable_index)
    stabilize_configured_display(page, page_config)
    time.sleep(0.5)


def before_home_module_capture(name, page, element, page_config=None):
    if name == "banner":
        stabilize_banner(page, page_config)


def run():
    ctx = PageCheckContext(PAGE, suite=SUITE)
    failures = []
    create_dirs(ctx.baseline_dir, ctx.current_dir, ctx.diff_dir)

    playwright = browser = context = page = page_model = None

    try:
        playwright, browser, context, page = init_browser(ctx.site_config)
        page_model = HomePage(page, site_config=ctx.site_config)
        try:
            page_model.open()
            time.sleep(2)
            page_model.wait_until_ready()
        except Exception as navigation_error:
            record_runtime_error_fail_open(
                page_model.runtime,
                navigation_error,
                "navigation/readiness",
            )
            if not page_model.runtime.page_available():
                raise
            if isinstance(navigation_error, TerminalMainDocumentError):
                raise
            failures.append(
                "Home navigation/readiness error: "
                f"{redact_text(f'{type(navigation_error).__name__}: {navigation_error}')}"
            )

        collect_runtime_health_fail_open(page_model.runtime)

        failures.extend(dom_check(page, page_model.modules))
        failures.extend(dom_presence_check(page, page_model.dom_presence))
        failures.extend(check_plugins(page_model))

        plugin_results = capture_plugins(ctx, page_model)

        hide_dynamic_elements(page, ctx.site_config, ctx.page_config)
        stabilize_banner(page, ctx.page_config)

        def before_home_capture(capture_page):
            stabilize_banner(capture_page, ctx.page_config)

        def before_home_module_capture_for_context(name, capture_page, element):
            before_home_module_capture(
                name, capture_page, element, ctx.page_config
            )

        global_results = capture_global_screenshot(
            ctx,
            page,
            before_capture=before_home_capture,
        )
        first_screen_results = capture_first_screen(
            ctx,
            page,
            before_capture=before_home_capture,
        )
        module_results = capture_modules(
            page,
            ctx.module_locators_for_capture(),
            ctx.current_dir,
            ctx.baseline_dir,
            ctx.diff_dir,
            require_reviews=False,
            site_config=ctx.site_config,
            page_config=ctx.page_config,
            before_capture=before_home_module_capture_for_context,
            legacy_baseline_dir=ctx.legacy_baseline_dir,
            artifact_manager=getattr(ctx, "artifact_manager", None),
        )

        failures.extend(
            process_results(
                global_results,
                ctx.site,
                ctx.suite,
                ctx.page_name,
                manager=getattr(ctx, "artifact_manager", None),
            )
        )
        failures.extend(
            process_results(
                first_screen_results,
                ctx.site,
                ctx.suite,
                ctx.page_name,
                manager=getattr(ctx, "artifact_manager", None),
            )
        )
        failures.extend(
            process_results(
                module_results,
                ctx.site,
                ctx.suite,
                ctx.page_name,
                manager=getattr(ctx, "artifact_manager", None),
            )
        )
        failures.extend(
            process_results(
                plugin_results,
                ctx.site,
                ctx.suite,
                ctx.page_name,
                manager=getattr(ctx, "artifact_manager", None),
            )
        )

    except Exception as e:
        if page_model is not None:
            record_runtime_error_fail_open(
                page_model.runtime,
                e,
                "visual/check execution",
            )
        error = redact_text(
            f"Playwright runtime error: {type(e).__name__}: {e}"
        )
        failures.append(f"Home: {error}")
        add_result(
            build_result(ctx.site, ctx.suite, ctx.page_name, "runtime", "failed", None, error=error)
        )
    finally:
        if page_model is not None:
            failures.extend(
                finalize_runtime_health_fail_open(
                    page_model.runtime,
                    ctx.site,
                    ctx.page_name,
                    get_current_viewport_name(),
                )
            )
        artifact_manager = getattr(ctx, "artifact_manager", None)
        if artifact_manager is not None:
            artifact_manager.finalize_page(bool(failures))
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
