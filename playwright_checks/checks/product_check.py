import os
import sys
import tempfile
import time

import numpy as np
from PIL import Image

from playwright_checks.checks.context import PageCheckContext
from playwright_checks.core.driver import close_browser, init_browser
from playwright_checks.core.test_results import add_result, clear_results, write_results
from playwright_checks.core.viewport import get_current_viewport_name
from playwright_checks.pages.product_page import ProductPage
from playwright_checks.utils.capture import (
    capture_modules,
    prepare_for_screenshot,
)
from playwright_checks.utils.dom import dom_check, hide_dynamic_elements
from playwright_checks.utils.waits import (
    build_paths,
    create_dirs,
    selector_for,
)
from playwright_checks.utils.visual import build_result, process_results


SUITE = "visual"
PAGE = "product"


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
    probe_path = os.path.join(
        tempfile.gettempdir(),
        f"variant_probe_{time.time_ns()}.png"
    )
    target.screenshot(path=probe_path)

    if any(image_paths_close(path, probe_path) for path in captured_paths):
        os.remove(probe_path)
        raise Exception(f"{variant_source} {candidate_index} duplicated screenshot")

    os.replace(probe_path, paths["current"])
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


def run():
    ctx = PageCheckContext(PAGE, suite=SUITE)
    failures = []
    create_dirs(ctx.baseline_dir, ctx.current_dir, ctx.diff_dir)
    playwright = browser = context = page = None

    try:
        playwright, browser, context, page = init_browser()
        page_model = ProductPage(page, site_config=ctx.site_config)
        page_model.open()
        time.sleep(2)
        page_model.wait_until_ready()

        failures.extend(dom_check(page, page_model.modules))
        failures.extend(check_product_content(page_model))
        failures.extend(check_add_to_cart(page_model))
        check_variant_count(page_model)

        hide_dynamic_elements(page, ctx.site_config, ctx.page_config)
        module_results = capture_modules(
            page,
            ctx.module_locators_for_capture(),
            ctx.current_dir,
            ctx.baseline_dir,
            ctx.diff_dir,
            require_reviews=ctx.page_config.get("require_reviews", True),
            site_config=ctx.site_config,
            page_config=ctx.page_config,
            legacy_baseline_dir=ctx.legacy_baseline_dir,
        )
        variant_results, variant_failures = test_variants(ctx, page_model)
        failures.extend(variant_failures)

        failures.extend(process_results(module_results, ctx.site, ctx.suite, ctx.page_name))
        failures.extend(process_results(variant_results, ctx.site, ctx.suite, ctx.page_name))

    except Exception as e:
        error = f"Playwright runtime error: {type(e).__name__}: {e}"
        failures.append(f"PDP: {error}")
        add_result(
            build_result(ctx.site, ctx.suite, ctx.page_name, "runtime", "failed", None, error=error)
        )
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
