import os
import sys
import time

import numpy as np
from PIL import Image

from playwright_checks.checks.context import PageCheckContext
from playwright_checks.artifacts.screenshot_manager import safe_move
from playwright_checks.core.driver import close_browser, init_browser
from playwright_checks.core.test_results import (
    add_result,
    clear_results,
    write_results,
)
from playwright_checks.core.viewport import get_current_viewport_name
from playwright_checks.pages.product_page import ProductPage
from playwright_checks.runtime.evidence import redact_text
from playwright_checks.runtime.session import (
    collect_runtime_health_fail_open,
    finalize_runtime_health_fail_open,
    record_runtime_error_fail_open,
)
from playwright_checks.utils.waits import TerminalMainDocumentError
from playwright_checks.utils.capture import (
    capture_first_screen,
    capture_global_screenshot,
    capture_modules,
    prepare_for_screenshot,
    wait_for_layout_stable,
)
from playwright_checks.utils.dom import (
    dom_check,
    dom_presence_check,
    hide_dynamic_elements,
)
from playwright_checks.utils.waits import (
    build_paths,
    create_dirs,
    selector_for,
)
from playwright_checks.utils.visual import build_result, process_results


SUITE = "visual"
PAGE = "product"
ADD_TO_CART_EXPECTED_TEXTS = (
    "add to cart",
    "add to bag",
    "add to basket",
)
ADD_TO_CART_LOADING_CLASSES = (
    "loading",
    "is-loading",
    "btn--loading",
)


def check_add_to_cart(page_model):
    print("\nAdd To Cart state")
    failures = []

    try:
        button = page_model.module("add_to_cart")
        enabled = button.is_enabled()
        print(f"enabled={enabled}")

        if not enabled:
            failures.append("Add To Cart button is disabled")

    except Exception as e:
        print(f"FAIL {e}")
        failures.append(f"Add To Cart state error: {e}")

    return failures


def normalized_text(value):
    return " ".join(str(value or "").split()).lower()


def add_to_cart_text_matches(text):
    normalized = normalized_text(text)
    return any(expected in normalized for expected in ADD_TO_CART_EXPECTED_TEXTS)


def add_to_cart_button_state(button):
    state = button.evaluate(
        """
        (button) => {
            const rect = button.getBoundingClientRect();
            const style = window.getComputedStyle(button);
            const textValues = [
                button.innerText,
                button.textContent,
                button.getAttribute('aria-label'),
                button.getAttribute('value')
            ].filter(Boolean);

            return {
                text: textValues.join(' ').replace(/\\s+/g, ' ').trim(),
                className: String(button.className || '').toLowerCase(),
                ariaBusy: String(button.getAttribute('aria-busy') || '').toLowerCase(),
                ariaDisabled: String(button.getAttribute('aria-disabled') || '').toLowerCase(),
                disabled: Boolean(button.disabled),
                hasForm: Boolean(button.closest('form')),
                visibleStyle: style.display !== 'none' && style.visibility !== 'hidden',
                top: Math.round(rect.top),
                bottom: Math.round(rect.bottom),
                left: Math.round(rect.left),
                right: Math.round(rect.right),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight,
            };
        }
        """
    )
    state["visible"] = button.is_visible(timeout=500)
    state["enabled"] = button.is_enabled(timeout=500)
    state["has_text"] = bool(normalized_text(state.get("text")))
    state["text_matches"] = add_to_cart_text_matches(state.get("text"))
    state["loading"] = any(
        marker in state.get("className", "")
        for marker in ADD_TO_CART_LOADING_CLASSES
    )
    state["busy"] = state.get("ariaBusy") == "true"
    state["disabled_state"] = (
        state.get("disabled")
        or state.get("ariaDisabled") == "true"
        or not state.get("enabled")
    )
    state["viewport_ready"] = (
        state.get("top", 0) >= 0
        and state.get("left", 0) >= 0
        and state.get("bottom", 0) <= state.get("viewportHeight", 0)
        and state.get("right", 0) <= state.get("viewportWidth", 0)
    )
    state["content_ready"] = (
        state["visible"]
        and state["visibleStyle"]
        and state["enabled"]
        and state["has_text"]
        and not state["loading"]
        and not state["busy"]
        and not state["disabled_state"]
        and state.get("width", 0) > 0
        and state.get("height", 0) > 0
    )
    state["ready"] = state["content_ready"] and state["viewport_ready"]
    return state


def add_to_cart_candidate_score(state):
    return (
        1 if state.get("hasForm") else 0,
        1 if state.get("text_matches") else 0,
        len(normalized_text(state.get("text"))),
        state.get("width", 0) * state.get("height", 0),
    )


def format_button_state(state):
    if not state:
        return "no candidate state"

    return (
        f"text={state.get('text')!r}, visible={state.get('visible')}, "
        f"enabled={state.get('enabled')}, loading={state.get('loading')}, "
        f"busy={state.get('busy')}, "
        f"size={state.get('width')}x{state.get('height')}, "
        f"rect=({state.get('left')},{state.get('top')})-"
        f"({state.get('right')},{state.get('bottom')}), "
        f"viewport={state.get('viewportWidth')}x{state.get('viewportHeight')}"
    )


def scroll_add_to_cart_context_into_view(button):
    button.evaluate(
        """
        (button) => {
            const form = button.closest('form');
            const target = form || button;
            target.scrollIntoView({block: 'center', inline: 'nearest'});
        }
        """
    )
    time.sleep(0.2)


def locate_ready_add_to_cart(page_model, timeout=10000):
    selector = selector_for(page_model.modules["add_to_cart"])
    end_time = time.time() + timeout / 1000
    last_state = None

    while time.time() < end_time:
        buttons = page_model.page.locator(selector)
        candidates = []
        offscreen_candidates = []

        for index in range(buttons.count()):
            button = buttons.nth(index)
            try:
                state = add_to_cart_button_state(button)
                last_state = state
                if state["ready"]:
                    candidates.append((add_to_cart_candidate_score(state), button, state))
                elif state["content_ready"]:
                    offscreen_candidates.append(
                        (add_to_cart_candidate_score(state), button, state)
                    )
            except Exception:
                continue

        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            button = candidates[0][1]
            remaining = max(1, end_time - time.time())
            if wait_for_layout_stable(button, timeout=min(remaining, 5)):
                state = add_to_cart_button_state(button)
                if state["ready"]:
                    return button
                last_state = state

        if offscreen_candidates:
            offscreen_candidates.sort(key=lambda item: item[0], reverse=True)
            scroll_add_to_cart_context_into_view(offscreen_candidates[0][1])
            time.sleep(0.2)
            continue

        time.sleep(0.2)

    raise Exception(
        "Add To Cart button not ready before screenshot: "
        f"{format_button_state(last_state)}"
    )


def assert_add_to_cart_ready(button):
    state = add_to_cart_button_state(button)
    if not state["ready"]:
        raise Exception(
            "Add To Cart button became unready before screenshot: "
            f"{format_button_state(state)}"
        )


def image_has_visible_pixels(path, threshold=250):
    with Image.open(path).convert("RGB") as image:
        pixels = np.array(image)
    return bool((pixels < threshold).any(axis=2).any())


def capture_add_to_cart(ctx, page_model, attempts=3):
    print("\nAdd To Cart screenshot")
    name = "add_to_cart"
    results = {}
    paths = build_paths(
        ctx.current_dir,
        ctx.baseline_dir,
        ctx.diff_dir,
        name,
        legacy_baseline_dir=ctx.legacy_baseline_dir,
    )
    capture_start = time.perf_counter()
    last_error = None

    for attempt in range(1, attempts + 1):
        probe_path = ctx.temporary_path(
            name,
            f"add-to-cart-probe-{attempt}",
        )

        try:
            target = locate_ready_add_to_cart(page_model)
            prepare_for_screenshot(
                page_model.page,
                target,
                site_config=ctx.site_config,
                page_config=ctx.page_config,
                require_reviews=ctx.page_config.get("require_reviews", True),
                timeout=10,
                settle_delay=0.5,
                before_capture=assert_add_to_cart_ready,
            )

            target = locate_ready_add_to_cart(page_model, timeout=5000)
            ctx.artifact_manager.capture_element(
                target,
                probe_path,
            )

            if not image_has_visible_pixels(probe_path):
                raise Exception("Add To Cart screenshot is blank")

            safe_move(probe_path, paths["current"])
            paths["capture_duration_ms"] = round(
                (time.perf_counter() - capture_start) * 1000,
                2
            )
            paths["capture_attempts"] = attempt
            results[name] = paths
            print(
                f"OK [{name}] "
                f"{paths['capture_duration_ms']}ms "
                f"attempts={paths['capture_attempts']}"
            )
            return results

        except Exception as e:
            last_error = e
            ctx.artifact_manager.discard_temporary(probe_path)

            if attempt < attempts:
                print(
                    f"      add_to_cart screenshot retry {attempt}/{attempts}: "
                    f"{type(e).__name__}: {e}"
                )
                time.sleep(1)

    print(f"FAIL [{name}] capture failed: {last_error}")
    results[name] = {
        "error": f"capture failed: {last_error}",
        "capture_duration_ms": round(
            (time.perf_counter() - capture_start) * 1000,
            2
        ),
        "capture_attempts": attempts,
    }
    return results


def locate_visible_content(page, locator_value, timeout=10000):
    end_time = time.time() + timeout / 1000
    elements = page.locator(selector_for(locator_value))

    while time.time() < end_time:
        count = elements.count()

        for index in range(count):
            element = elements.nth(index)
            try:
                text = element.inner_text(timeout=1000).strip()
                if element.is_visible() and text:
                    return element, text
            except Exception:
                continue

        time.sleep(0.2)

    raise Exception(f"no visible content found for {selector_for(locator_value)}")


def check_product_content(page_model):
    print("\nProduct content checks")
    failures = []

    for name in ("title", "price"):
        try:
            element, text = locate_visible_content(
                page_model.page,
                page_model.content_locator(name)
            )
            visible = element.is_visible()
            print(f"OK {name} visible={visible} text={bool(text)}")

            if not visible or not text:
                failures.append(
                    f"product {name} visible={visible}, text={bool(text)}"
                )

        except Exception as e:
            print(f"FAIL {name} content error: {e}")
            failures.append(f"product {name} content error: {e}")

    return failures


def check_variant_count(page_model):
    variants = page_model.variant_inputs()
    print(f"\nVariant count: {variants.count()}")


def variant_candidate_sets(page_model):
    candidate_sets = []

    options = page_model.variant_gallery_options()
    if options:
        if options.count():
            candidate_sets.append((options, "gallery option"))

    candidate_sets.append((page_model.variant_inputs(), "variant input"))

    return candidate_sets


def visible_candidate_indices(candidates, limit=20):
    indices = []
    count = min(candidates.count(), limit)

    for index in range(count):
        candidate = candidates.nth(index)
        try:
            if candidate.is_visible(timeout=500):
                indices.append(index)
        except Exception:
            continue

    return indices


def image_paths_close(path1, path2, threshold=0.005):
    img1 = Image.open(path1).convert("RGB")
    img2 = Image.open(path2).convert("RGB")

    if img1.size != img2.size:
        normalized = Image.new("RGB", img1.size, (255, 255, 255))
        crop = img2.crop((
            0,
            0,
            min(img1.width, img2.width),
            min(img1.height, img2.height),
        ))
        normalized.paste(crop, (0, 0))
        img2 = normalized

    pixels1 = np.array(img1)
    pixels2 = np.array(img2)
    diff = np.abs(pixels1.astype(int) - pixels2.astype(int))
    changed = (diff > 25).any(axis=2)
    return changed.sum() / changed.size <= threshold


def gallery_capture_target(page_model):
    gallery = page_model.module("gallery")

    if get_current_viewport_name() == "mobile":
        slideshow = gallery.locator(".product-slideshow.flickity-enabled").first
        try:
            slideshow.wait_for(state="visible", timeout=1000)
            box = slideshow.bounding_box()
            if box and box["width"] > 0 and box["height"] > 0:
                return slideshow
        except Exception:
            pass

    return gallery


def capture_gallery_variant(
    ctx,
    page_model,
    results,
    captured_paths,
    captured_count,
    variant_source,
    candidate_index,
):
    name = f"variant_{captured_count}"
    paths = build_paths(
        ctx.current_dir,
        ctx.baseline_dir,
        ctx.diff_dir,
        name,
        legacy_baseline_dir=ctx.legacy_baseline_dir,
    )
    capture_start = time.perf_counter()

    target = gallery_capture_target(page_model)
    prepare_for_screenshot(
        page_model.page,
        target,
        site_config=ctx.site_config,
        page_config=ctx.page_config,
        require_reviews=ctx.page_config.get("require_reviews", True),
        timeout=10,
        settle_delay=0.5,
    )

    target = gallery_capture_target(page_model)
    probe_path = ctx.temporary_path(
        name,
        f"variant-probe-{candidate_index}",
    )
    ctx.artifact_manager.capture_element(
        target,
        probe_path,
    )

    if any(image_paths_close(path, probe_path) for path in captured_paths):
        ctx.artifact_manager.discard_temporary(probe_path)
        raise Exception(f"{variant_source} {candidate_index} duplicated screenshot")

    safe_move(probe_path, paths["current"])
    captured_paths.append(paths["current"])
    paths["capture_duration_ms"] = round(
        (time.perf_counter() - capture_start) * 1000,
        2
    )
    paths["capture_attempts"] = 1
    paths["variant_candidate_index"] = candidate_index
    paths["variant_source"] = variant_source
    results[name] = paths
    print(f"OK Variant {captured_count} from {variant_source} {candidate_index}")

    return captured_count + 1


def gallery_slide_count(page_model):
    gallery = page_model.module("gallery")
    return gallery.evaluate(
        """
        (gallery) => {
            const slideshow =
                gallery.querySelector('.product-slideshow.flickity-enabled') ||
                gallery.querySelector('.flickity-enabled');
            if (!slideshow || !window.Flickity || !window.Flickity.data) {
                return 0;
            }
            const flickity = window.Flickity.data(slideshow);
            return flickity ? flickity.slides.length : 0;
        }
        """
    )


def select_gallery_slide(page_model, slide_index):
    gallery = page_model.module("gallery")
    return gallery.evaluate(
        """
        (gallery, slideIndex) => {
            const slideshow =
                gallery.querySelector('.product-slideshow.flickity-enabled') ||
                gallery.querySelector('.flickity-enabled');
            if (!slideshow || !window.Flickity || !window.Flickity.data) {
                return false;
            }
            const flickity = window.Flickity.data(slideshow);
            if (!flickity) {
                return false;
            }
            flickity.select(slideIndex, false, true);
            return true;
        }
        """,
        slide_index,
    )


def capture_gallery_slides(ctx, page_model, results, captured_paths, captured_count, target_count):
    slide_count = min(gallery_slide_count(page_model), 20)

    if not slide_count:
        return captured_count

    print(f"Gallery slide candidates: {slide_count}")

    for slide_index in range(slide_count):
        if captured_count >= target_count:
            break

        try:
            if not select_gallery_slide(page_model, slide_index):
                raise Exception("Flickity slideshow is not available")
            time.sleep(1)
            captured_count = capture_gallery_variant(
                ctx,
                page_model,
                results,
                captured_paths,
                captured_count,
                "gallery slide",
                slide_index,
            )
        except Exception as e:
            print(f"WARN Gallery slide {slide_index} skipped: {e}")

    return captured_count


def test_variants(ctx, page_model):
    print("\nVariant checks")
    results = {}
    failures = []

    try:
        candidate_sets = variant_candidate_sets(page_model)

        if not any(candidates.count() for candidates, _ in candidate_sets):
            print("WARN variant not found")
            failures.append("Variant not found")
            return results, failures

        target_count = 3
        captured_count = 0
        captured_paths = []

        for candidates, variant_source in candidate_sets:
            candidate_indices = visible_candidate_indices(candidates)

            if not candidate_indices:
                print(f"WARN {variant_source} has no visible candidates")
                if variant_source == "gallery option":
                    captured_count = capture_gallery_slides(
                        ctx,
                        page_model,
                        results,
                        captured_paths,
                        captured_count,
                        target_count,
                    )
                    if captured_count >= target_count:
                        break
                continue

            for candidate_index in candidate_indices:
                if captured_count >= target_count:
                    break

                try:
                    if candidate_index >= candidates.count():
                        raise Exception(
                            f"Variant candidate {candidate_index} does not exist"
                        )

                    variant = candidates.nth(candidate_index)
                    variant.scroll_into_view_if_needed(timeout=10000)
                    variant.click(force=True, timeout=10000)
                    time.sleep(1)
                    captured_count = capture_gallery_variant(
                        ctx,
                        page_model,
                        results,
                        captured_paths,
                        captured_count,
                        variant_source,
                        candidate_index,
                    )

                except Exception as e:
                    print(f"WARN Variant candidate {candidate_index} skipped: {e}")

            if captured_count >= target_count:
                break

        if captured_count == 0:
            failures.append("No distinct variant gallery screenshots captured")
        elif captured_count < target_count:
            failures.append(
                f"Only {captured_count} distinct variant gallery screenshots captured"
            )

    except Exception as e:
        print(f"FAIL Variant checks failed: {e}")
        failures.append(f"Variant checks failed: {e}")

    return results, failures


def monitoring_product_is_unavailable(page_model, page_config):
    monitoring = (page_config or {}).get("monitoring_product") or {}
    if not monitoring.get("stable"):
        return False
    navigation = getattr(page_model.runtime, "navigation", None)
    status = getattr(navigation, "status", None)
    return status in (404, 410)


def run():
    ctx = PageCheckContext(PAGE, suite=SUITE)
    failures = []
    create_dirs(ctx.baseline_dir, ctx.current_dir, ctx.diff_dir)
    playwright = browser = context = page = page_model = None

    try:
        playwright, browser, context, page = init_browser(ctx.site_config)
        page_model = ProductPage(page, site_config=ctx.site_config)
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
                "PDP navigation/readiness error: "
                f"{redact_text(f'{type(navigation_error).__name__}: {navigation_error}')}"
            )

        collect_runtime_health_fail_open(page_model.runtime)

        if monitoring_product_is_unavailable(
            page_model,
            ctx.page_config,
        ):
            error = "monitoring_product_unavailable"
            failures.append(error)
            add_result(
                build_result(
                    ctx.site,
                    ctx.suite,
                    ctx.page_name,
                    "monitoring_product",
                    "failed",
                    None,
                    error=error,
                    details={
                        "structural_status": "failed",
                        "affects_exit_code": True,
                    },
                )
            )
            return failures

        failures.extend(dom_check(page, page_model.modules))
        failures.extend(dom_presence_check(page, page_model.dom_presence))
        failures.extend(check_product_content(page_model))
        failures.extend(check_add_to_cart(page_model))
        check_variant_count(page_model)

        hide_dynamic_elements(page, ctx.site_config, ctx.page_config)
        global_results = capture_global_screenshot(ctx, page)
        first_screen_results = capture_first_screen(ctx, page)

        if ctx.page_config.get("reload_before_module_capture"):
            print("Reloading product page before module capture")
            page_model.open()
            time.sleep(2)
            page_model.wait_until_ready()
            collect_runtime_health_fail_open(page_model.runtime)
            hide_dynamic_elements(page, ctx.site_config, ctx.page_config)

        module_locators = ctx.module_locators_for_capture()
        add_to_cart_locator = module_locators.pop("add_to_cart", None)
        module_results = {}

        if module_locators:
            module_results.update(
                capture_modules(
                    page,
                    module_locators,
                    ctx.current_dir,
                    ctx.baseline_dir,
                    ctx.diff_dir,
                    require_reviews=ctx.page_config.get("require_reviews", True),
                    site_config=ctx.site_config,
                    page_config=ctx.page_config,
                    legacy_baseline_dir=ctx.legacy_baseline_dir,
                    artifact_manager=getattr(
                        ctx,
                        "artifact_manager",
                        None,
                    ),
                )
            )

        if add_to_cart_locator:
            module_results.update(capture_add_to_cart(ctx, page_model))

        variant_results, variant_failures = test_variants(ctx, page_model)
        failures.extend(variant_failures)

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
                variant_results,
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
        failures.append(f"PDP: {error}")
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
        print("\nPDP Playwright failures")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
