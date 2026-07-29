import os
import sys
import time

from PIL import Image
from playwright.sync_api import Error as PlaywrightError

from playwright_checks.checks.context import PageCheckContext
from playwright_checks.core.driver import close_browser, init_browser
from playwright_checks.core.test_results import (
    add_result,
    clear_results,
    write_results,
)
from playwright_checks.core.viewport import (
    get_current_viewport_name,
    is_mobile_viewport,
)
from playwright_checks.pages.collection_page import CollectionPage
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
    disable_motion,
    screenshot_element_with_retry,
    scroll_to_center,
    wait_for_images,
    wait_for_layout_stable,
)
from playwright_checks.utils.dom import (
    dom_check,
    dom_presence_check,
    hide_dynamic_elements,
)
from playwright_checks.utils.stability import stabilize_card_media
from playwright_checks.utils.waits import (
    build_paths,
    create_dirs,
)
from playwright_checks.utils.visual import build_result, process_results


SUITE = "visual"
PAGE = "collection"


def get_product_cards(page_model):
    return page_model.product_cards()


def scroll_to_load_all(page_model, timeout=10, max_scrolls=20):
    last_count = 0
    stable_count = 0
    end_time = time.time() + timeout
    cards = get_product_cards(page_model)

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
            page_model.page.evaluate("() => window.scrollBy(0, window.innerHeight)")
        except PlaywrightError as e:
            print(f"scroll interrupted: {e}")
            return last_count

        time.sleep(0.5)

    return last_count


def check_product_count(ctx, page_model):
    print("\nProduct count checks")
    failures = []

    scroll_to_load_all(page_model, timeout=15)
    count = get_product_cards(page_model).count()
    layout_checks = (
        ctx.page_config.get("layout_checks", {}).get(
            "product_grid",
            {},
        )
    )
    minimum_count = int(layout_checks.get("minimum_count", 1))

    if count < minimum_count:
        print(
            f"FAIL product count: {count}, minimum {minimum_count}"
        )
        failures.append(
            f"product grid count below minimum: "
            f"actual {count}, minimum {minimum_count}"
        )
        return failures

    if page_model.expected_count is None:
        print(
            f"OK product count: {count} "
            "(expected_count not configured)"
        )
        return failures

    if count == page_model.expected_count:
        print(f"OK product count: {count}")
    else:
        print(
            f"CONTENT_CHANGED product count: {count}, "
            f"reference {page_model.expected_count}"
        )
        _paths, retention = ctx.artifact_manager.finalize_result(
            "product_count",
            "content_changed",
            {},
            artifact_type="content_change",
            content_changes=["product_count_changed"],
            structural_status="passed",
        )
        add_result(
            build_result(
                ctx.site,
                ctx.suite,
                ctx.page_name,
                "product_count",
                "content_changed",
                None,
                details={
                    "actual_count": count,
                    "reference_count": page_model.expected_count,
                    "content_changes": ["product_count_changed"],
                    "structural_status": "passed",
                    "affects_exit_code": False,
                    **retention,
                },
            )
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


def get_product_card_by_index(page_model, index):
    cards = get_product_cards(page_model)
    count = cards.count()

    if index >= count:
        raise Exception(f"product card {index} does not exist")

    card = cards.nth(index)
    card.wait_for(state="visible", timeout=10000)
    return card


def capture_card_image_stable(ctx, page_model, index, output_path, hover=False):
    def locate():
        return get_product_card_by_index(page_model, index)

    def prepare(card):
        disable_motion(page_model.page)
        scroll_to_center(card)
        hide_dynamic_elements(page_model.page, ctx.site_config, ctx.page_config)
        stabilize_card_media(card, ctx.page_config, hover=False)

        if not wait_for_images(card, timeout=10):
            raise Exception("wait for images timeout")

        if not wait_for_layout_stable(card, timeout=10):
            raise Exception("layout is not stable")

        card = locate()
        scroll_to_center(card)
        hide_dynamic_elements(page_model.page, ctx.site_config, ctx.page_config)
        stabilize_card_media(card, ctx.page_config, hover=False)

        if hover:
            card.hover(timeout=10000)
            stabilize_card_media(card, ctx.page_config, hover=True)
            if not wait_for_images(card, timeout=10):
                raise Exception("wait for hover images timeout")
            time.sleep(0.3)

    return screenshot_element_with_retry(
        locate,
        output_path,
        prepare=prepare,
        attempts=3,
        delay=1,
        artifact_manager=ctx.artifact_manager,
    )


def capture_product_cards(ctx, page_model):
    print("\nProduct card screenshots")
    results = {}
    card_count = min(8, get_product_cards(page_model).count())

    for index in range(card_count):
        name = f"product_{index}"
        paths = build_paths(
            ctx.current_dir,
            ctx.baseline_dir,
            ctx.diff_dir,
            name,
            legacy_baseline_dir=ctx.legacy_baseline_dir,
        )

        try:
            metrics = capture_card_image_stable(ctx, page_model, index, paths["current"])
            paths.update(metrics)
            prepare_card_compare_images(paths)
            results[name] = paths
            print(f"OK {name}")
        except Exception as e:
            print(f"FAIL {name} capture failed: {e}")
            results[name] = {"error": f"capture failed: {e}"}

    return results


def capture_hover_cards(ctx, page_model):
    print("\nHover screenshots")
    results = {}

    if is_mobile_viewport():
        print("mobile viewport: hover screenshots skipped")
        return results

    card_count = min(8, get_product_cards(page_model).count())

    for index in range(card_count):
        name = f"hover_{index}"
        paths = build_paths(
            ctx.current_dir,
            ctx.baseline_dir,
            ctx.diff_dir,
            name,
            legacy_baseline_dir=ctx.legacy_baseline_dir,
        )

        try:
            metrics = capture_card_image_stable(
                ctx,
                page_model,
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
    ctx = PageCheckContext(PAGE, suite=SUITE)
    failures = []
    create_dirs(ctx.baseline_dir, ctx.current_dir, ctx.diff_dir)
    playwright = browser = context = page = page_model = None

    try:
        playwright, browser, context, page = init_browser(ctx.site_config)
        page_model = CollectionPage(page, site_config=ctx.site_config)
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
                "PLP navigation/readiness error: "
                f"{redact_text(f'{type(navigation_error).__name__}: {navigation_error}')}"
            )

        collect_runtime_health_fail_open(page_model.runtime)

        failures.extend(dom_check(page, page_model.modules))
        failures.extend(dom_presence_check(page, page_model.dom_presence))
        failures.extend(check_product_count(ctx, page_model))

        hide_dynamic_elements(page, ctx.site_config, ctx.page_config)

        global_results = capture_global_screenshot(ctx, page)
        first_screen_results = capture_first_screen(ctx, page)
        module_results = capture_modules(
            page,
            ctx.module_locators_for_capture(),
            ctx.current_dir,
            ctx.baseline_dir,
            ctx.diff_dir,
            require_reviews=False,
            site_config=ctx.site_config,
            page_config=ctx.page_config,
            legacy_baseline_dir=ctx.legacy_baseline_dir,
            artifact_manager=getattr(ctx, "artifact_manager", None),
        )
        product_grid_policy = next(
            (
                region
                for region in ctx.page_config.get(
                    "dynamic_regions",
                    [],
                )
                if region.get("name") == "product_grid"
            ),
            {},
        )
        if product_grid_policy.get("strategy") == "layout_only":
            print(
                "Collection product/hover pixel captures skipped: "
                "product_grid uses layout_only"
            )
            product_results = {}
            hover_results = {}
        else:
            product_results = capture_product_cards(ctx, page_model)
            hover_results = capture_hover_cards(ctx, page_model)

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
                product_results,
                ctx.site,
                ctx.suite,
                ctx.page_name,
                manager=getattr(ctx, "artifact_manager", None),
            )
        )
        failures.extend(
            process_results(
                hover_results,
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
        failures.append(f"PLP: {error}")
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
        print("\nPLP Playwright failures")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
