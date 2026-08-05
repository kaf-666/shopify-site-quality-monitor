import sys
import time

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
from playwright_checks.utils.structure import run_structure_checks


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


def accelerated_checkout_config(page_model):
    configured = page_model.config.get("accelerated_checkout") or {}
    return dict(configured) if isinstance(configured, dict) else {}


def probe_optional_accelerated_checkout(page_model, target):
    """Briefly observe outer checkout geometry without requiring the widget."""

    configured = accelerated_checkout_config(page_model)
    selector = str(configured.get("container_selector") or "").strip()
    if not selector:
        return {"configured": False, "present": False, "optional": True}

    def read_state():
        return target.evaluate(
            """
            (root, selector) => {
                const node = root.querySelector(selector);
                if (!node) {
                    return {
                        configured: true,
                        present: false,
                        optional: true,
                    };
                }
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                return {
                    configured: true,
                    present: true,
                    optional: true,
                    visible: rect.width > 0 && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0',
                    rect: {
                        left: rect.left,
                        top: rect.top,
                        right: rect.right,
                        bottom: rect.bottom,
                        width: rect.width,
                        height: rect.height,
                    },
                };
            }
            """,
            selector,
        )

    started = time.perf_counter()
    try:
        state = read_state()
        if not state.get("present"):
            state["probeDurationMs"] = round(
                (time.perf_counter() - started) * 1000,
                2,
            )
            return state

        timeout = max(
            0,
            float(configured.get("optional_probe_timeout_ms", 750)) / 1000,
        )
        interval = max(
            0.05,
            float(configured.get("optional_probe_interval_ms", 150)) / 1000,
        )
        required = max(
            1,
            int(configured.get("optional_probe_stable_samples", 2)),
        )
        deadline = time.perf_counter() + timeout
        previous_size = None
        stable_samples = 0
        while True:
            rect = state.get("rect") or {}
            size = (
                round(float(rect.get("width", 0) or 0), 1),
                round(float(rect.get("height", 0) or 0), 1),
            )
            if size == previous_size:
                stable_samples += 1
            else:
                stable_samples = 1
            if stable_samples >= required or time.perf_counter() >= deadline:
                break
            previous_size = size
            time.sleep(interval)
            state = read_state()
            if not state.get("present"):
                break

        state["stableSamples"] = stable_samples
        state["probeDurationMs"] = round(
            (time.perf_counter() - started) * 1000,
            2,
        )
        return state
    except Exception as error:
        # Optional third-party content must never block the product capture.
        return {
            "configured": True,
            "present": False,
            "optional": True,
            "probeError": type(error).__name__,
            "probeDurationMs": round(
                (time.perf_counter() - started) * 1000,
                2,
            ),
        }


def check_add_to_cart(page_model):
    print("\nAdd To Cart state")
    failures = []

    try:
        button = locate_ready_add_to_cart(page_model)
        state = add_to_cart_button_state(button)
        print(format_button_state(state))
        if not state.get("ready"):
            failures.append(
                "Add To Cart button is not ready: "
                + format_button_state(state)
            )

    except Exception as e:
        print(f"FAIL {e}")
        failures.append(f"Add To Cart state error: {e}")

    return failures


def product_main_target(page_model):
    configured = page_model.config.get("product_main")
    if configured:
        return locate_visible_content(page_model.page, configured)[0]

    gallery = page_model.module("gallery")
    info = page_model.module("info")
    gallery_handle = gallery.element_handle()
    info_handle = info.element_handle()
    common = page_model.page.evaluate_handle(
        """
        (nodes) => {
            let current = nodes.gallery;
            while (current && !current.contains(nodes.info)) {
                current = current.parentElement;
            }
            if (!current || ['BODY', 'HTML'].includes(current.tagName)) {
                return null;
            }
            return current;
        }
        """,
        {"gallery": gallery_handle, "info": info_handle},
    )
    target = common.as_element()
    if target is None:
        common.dispose()
        raise AssertionError("product_main common container not found")
    return target


def product_main_snapshot(page_model, target):
    gallery = page_model.module("gallery").element_handle()
    info = page_model.module("info").element_handle()
    add_to_cart = locate_ready_add_to_cart(page_model).element_handle()
    checkout_config = accelerated_checkout_config(page_model)
    return target.evaluate(
        """
        (root, payload) => {
            const nodes = payload.nodes;
            const checkout = payload.checkout || {};
            const rectOf = (node) => {
                const rect = node.getBoundingClientRect();
                return {
                    left: rect.left,
                    top: rect.top,
                    right: rect.right,
                    bottom: rect.bottom,
                    width: rect.width,
                    height: rect.height,
                };
            };
            const visible = (node) => {
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.opacity !== '0';
            };
            const rootRect = rectOf(root);
            const infoRect = rectOf(nodes.info);
            const overlap = (first, second) => {
                if (!first || !second) return {width: 0, height: 0};
                return {
                    width: Math.max(
                        0,
                        Math.min(first.right, second.right)
                            - Math.max(first.left, second.left)
                    ),
                    height: Math.max(
                        0,
                        Math.min(first.bottom, second.bottom)
                            - Math.max(first.top, second.top)
                    ),
                };
            };
            const unionRect = (items) => {
                if (!items.length) return null;
                const rects = items.map(rectOf);
                const left = Math.min(...rects.map((rect) => rect.left));
                const top = Math.min(...rects.map((rect) => rect.top));
                const right = Math.max(...rects.map((rect) => rect.right));
                const bottom = Math.max(...rects.map((rect) => rect.bottom));
                return {
                    left,
                    top,
                    right,
                    bottom,
                    width: right - left,
                    height: bottom - top,
                };
            };
            const queryAll = (boundary, selectors) => {
                if (!boundary) return [];
                const found = [];
                const seen = new Set();
                for (const selector of selectors || []) {
                    for (const node of boundary.querySelectorAll(selector)) {
                        if (!seen.has(node)) {
                            seen.add(node);
                            found.push(node);
                        }
                    }
                }
                return found;
            };
            const relativeBox = (node, boundary) => {
                const rect = node.getBoundingClientRect();
                const boundaryRect = boundary
                    ? boundary.getBoundingClientRect()
                    : rootRect;
                return {
                    left: Math.max(
                        0,
                        rect.left - rootRect.left,
                        boundaryRect.left - rootRect.left
                    ),
                    top: Math.max(
                        0,
                        rect.top - rootRect.top,
                        boundaryRect.top - rootRect.top
                    ),
                    right: Math.min(
                        rootRect.width,
                        rect.right - rootRect.left,
                        boundaryRect.right - rootRect.left
                    ),
                    bottom: Math.min(
                        rootRect.height,
                        rect.bottom - rootRect.top,
                        boundaryRect.bottom - rootRect.top
                    ),
                };
            };
            const galleryDynamicNodes = [
                ...nodes.gallery.querySelectorAll('img, picture, video')
            ];
            const infoDynamicNodes = [
                ...nodes.info.querySelectorAll([
                    '.product-single__title',
                    '.product__title',
                    'h1',
                    '.price',
                    '[class*="price"]',
                    '[class*="inventory"]',
                    '[class*="review"]',
                    '[class*="countdown"]'
                ].join(','))
            ];
            const paymentContainer = checkout.container_selector
                ? nodes.info.querySelector(checkout.container_selector)
                : null;
            const purchaseArea = checkout.purchase_area_selector
                ? nodes.info.querySelector(checkout.purchase_area_selector)
                : null;
            const variantNodes = checkout.variant_area_selector
                ? Array.from(
                    nodes.info.querySelectorAll(checkout.variant_area_selector)
                )
                : [];
            const paymentMaskCandidates = queryAll(
                paymentContainer,
                checkout.content_mask_selectors || []
            );
            const paymentBrandNodes = queryAll(
                paymentContainer,
                checkout.brand_presence_selectors || []
            );
            const paymentMaskNodes = paymentMaskCandidates.filter(
                (candidate) => !paymentMaskCandidates.some(
                    (other) => other !== candidate && other.contains(candidate)
                )
            );
            const paymentRect = paymentContainer
                ? rectOf(paymentContainer)
                : null;
            const purchaseAreaRect = purchaseArea
                ? rectOf(purchaseArea)
                : null;
            const variantAreaRect = unionRect(variantNodes);
            const addToCartRect = rectOf(nodes.addToCart);
            const paymentAddOverlap = overlap(paymentRect, addToCartRect);
            const paymentVariantOverlap = overlap(
                paymentRect,
                variantAreaRect
            );
            const maximumOverlap = Number(
                checkout.maximum_overlap_px || 8
            );
            const paymentLoading = Boolean(
                paymentContainer
                && (
                    /disabled|loading/i.test(paymentContainer.className || '')
                    || paymentContainer.querySelector(
                        '.shopify-payment-button__skeleton'
                    )
                )
            );
            const images = Array.from(
                nodes.gallery.querySelectorAll('img')
            ).filter(visible);
            const readyImages = images.filter((image) => (
                image.complete
                && image.naturalWidth > 0
                && image.naturalHeight > 0
            ));
            const mainImage = readyImages[0] || images[0] || null;
            const mainRect = mainImage ? mainImage.getBoundingClientRect() : null;
            const renderedRatio = mainRect && mainRect.height
                ? mainRect.width / mainRect.height
                : null;
            const naturalRatio = mainImage && mainImage.naturalHeight
                ? mainImage.naturalWidth / mainImage.naturalHeight
                : null;
            return {
                root: rectOf(root),
                gallery: rectOf(nodes.gallery),
                info: infoRect,
                addToCart: addToCartRect,
                galleryVisible: visible(nodes.gallery),
                infoVisible: visible(nodes.info),
                addToCartVisible: visible(nodes.addToCart),
                imageCount: images.length,
                readyImageCount: readyImages.length,
                renderedRatio,
                naturalRatio,
                pageHorizontalOverflow:
                    document.documentElement.scrollWidth
                        > document.documentElement.clientWidth + 2,
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight,
                acceleratedCheckout: {
                    configured: Boolean(checkout.container_selector),
                    diagnosticName: String(
                        checkout.diagnostic_name || 'accelerated_checkout'
                    ),
                    optional: true,
                    present: Boolean(paymentContainer),
                    visible: Boolean(
                        paymentContainer && visible(paymentContainer)
                    ),
                    loading: paymentLoading,
                    brandContentPresent: paymentBrandNodes.some(visible),
                    iframePresent: Boolean(
                        paymentContainer
                        && paymentContainer.querySelector('iframe')
                    ),
                    rect: paymentRect,
                    purchaseAreaPresent: Boolean(purchaseArea),
                    purchaseAreaVisible: Boolean(
                        purchaseArea && visible(purchaseArea)
                    ),
                    purchaseAreaRect,
                    variantAreaRect,
                    withinHorizontalViewport: Boolean(
                        paymentRect
                        && paymentRect.left >= -2
                        && paymentRect.right <= window.innerWidth + 2
                    ),
                    withinInfo: Boolean(
                        paymentRect
                        && paymentRect.left >= infoRect.left - 2
                        && paymentRect.right <= infoRect.right + 2
                        && paymentRect.top >= infoRect.top - 2
                        && paymentRect.bottom <= infoRect.bottom + 2
                    ),
                    purchaseAreaWithinInfo: Boolean(
                        purchaseAreaRect
                        && purchaseAreaRect.left >= infoRect.left - 2
                        && purchaseAreaRect.right <= infoRect.right + 2
                        && purchaseAreaRect.top >= infoRect.top - 2
                        && purchaseAreaRect.bottom <= infoRect.bottom + 2
                    ),
                    containerHorizontalOverflow: Boolean(
                        paymentContainer
                        && paymentContainer.scrollWidth
                            > paymentContainer.clientWidth + 2
                    ),
                    pageHorizontalOverflow:
                        document.documentElement.scrollWidth
                            > document.documentElement.clientWidth + 2,
                    overlapsAddToCart:
                        paymentAddOverlap.width > maximumOverlap
                            && paymentAddOverlap.height > maximumOverlap,
                    overlapsVariantRegion:
                        paymentVariantOverlap.width > maximumOverlap
                            && paymentVariantOverlap.height > maximumOverlap,
                    addToCartOverlap: paymentAddOverlap,
                    variantOverlap: paymentVariantOverlap,
                    minimumHeight: Number(
                        checkout.minimum_height_px || 0
                    ),
                    maximumHeight: Number(
                        checkout.maximum_height_px || 0
                    ),
                    maximumPurchaseAreaHeight: Number(
                        checkout.maximum_purchase_area_height_px || 0
                    ),
                    maximumPurchaseAreaInfoRatio: Number(
                        checkout.maximum_purchase_area_info_ratio || 0
                    ),
                    purchaseAreaInfoRatio: purchaseAreaRect && infoRect.height
                        ? purchaseAreaRect.height / infoRect.height
                        : null,
                    contentMaskCount: paymentMaskNodes.length,
                },
                maskBoxes: [
                    ...galleryDynamicNodes.filter(visible).map(
                        (node) => relativeBox(node, nodes.gallery)
                    ),
                    ...infoDynamicNodes.filter(visible).map(
                        (node) => relativeBox(node, nodes.info)
                    ),
                    ...paymentMaskNodes.filter(visible).map(
                        (node) => relativeBox(node, paymentContainer)
                    ),
                ].filter(
                    (box) => box.right > box.left && box.bottom > box.top
                ),
            };
        }
        """,
        {
            "nodes": {
                "gallery": gallery,
                "info": info,
                "addToCart": add_to_cart,
            },
            "checkout": checkout_config,
        },
    )


def accelerated_checkout_issues(state):
    checkout = state.get("acceleratedCheckout") or {}
    if not checkout.get("configured"):
        return []

    issues = []
    purchase = checkout.get("purchaseAreaRect") or {}
    if not checkout.get("purchaseAreaPresent"):
        issues.append("purchase_area_missing")
    else:
        if not checkout.get("purchaseAreaVisible"):
            issues.append("purchase_area_not_visible")
        if purchase.get("width", 0) <= 0 or purchase.get("height", 0) <= 0:
            issues.append("purchase_area_has_no_size")
        if not checkout.get("purchaseAreaWithinInfo"):
            issues.append("purchase_area_outside_product_info")
        maximum_purchase_height = checkout.get("maximumPurchaseAreaHeight", 0)
        maximum_purchase_ratio = checkout.get(
            "maximumPurchaseAreaInfoRatio",
            0,
        )
        purchase_ratio = checkout.get("purchaseAreaInfoRatio")
        if (
            maximum_purchase_height
            and purchase.get("height", 0) > maximum_purchase_height
        ) or (
            maximum_purchase_ratio
            and purchase_ratio is not None
            and purchase_ratio > maximum_purchase_ratio
        ):
            issues.append("purchase_area_height_unreasonable")

    if not checkout.get("present"):
        return issues

    rect = checkout.get("rect") or {}
    if not checkout.get("visible") and not checkout.get("loading"):
        issues.append("accelerated_checkout_not_visible")
    if rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
        issues.append("accelerated_checkout_has_no_size")
    minimum_height = checkout.get("minimumHeight", 0)
    maximum_height = checkout.get("maximumHeight", 0)
    if (
        minimum_height and rect.get("height", 0) < minimum_height
    ) or (
        maximum_height and rect.get("height", 0) > maximum_height
    ):
        issues.append("accelerated_checkout_height_unreasonable")
    if not checkout.get("withinInfo"):
        issues.append("accelerated_checkout_outside_product_info")
    if not checkout.get("withinHorizontalViewport"):
        issues.append("accelerated_checkout_outside_horizontal_viewport")
    if checkout.get("containerHorizontalOverflow") or checkout.get(
        "pageHorizontalOverflow"
    ):
        issues.append("accelerated_checkout_horizontal_overflow")
    if checkout.get("overlapsAddToCart"):
        issues.append("accelerated_checkout_overlaps_add_to_cart")
    if checkout.get("overlapsVariantRegion"):
        issues.append("accelerated_checkout_overlaps_variant_region")
    return issues


def print_accelerated_checkout_diagnostics(state):
    checkout = state.get("acceleratedCheckout") or {}
    if not checkout.get("configured"):
        return
    name = str(checkout.get("diagnosticName") or "accelerated_checkout")
    if not checkout.get("present"):
        print(f"{name}_present=false {name}_check=optional")
        return
    rect = checkout.get("rect") or {}
    formatted_rect = (
        f"({rect.get('left', 0):.1f},{rect.get('top', 0):.1f})-"
        f"({rect.get('right', 0):.1f},{rect.get('bottom', 0):.1f})"
    )
    print(
        f"{name}_present=true "
        f"{name}_visible={str(bool(checkout.get('visible'))).lower()} "
        f"{name}_loading={str(bool(checkout.get('loading'))).lower()} "
        f"{name}_brand_content="
        f"{str(bool(checkout.get('brandContentPresent'))).lower()} "
        f"{name}_iframe_present="
        f"{str(bool(checkout.get('iframePresent'))).lower()} "
        f"{name}_rect={formatted_rect} "
        f"{name}_within_viewport="
        f"{str(bool(checkout.get('withinHorizontalViewport'))).lower()} "
        f"{name}_overlaps_add_to_cart="
        f"{str(bool(checkout.get('overlapsAddToCart'))).lower()} "
        f"{name}_horizontal_overflow="
        f"{str(bool(checkout.get('containerHorizontalOverflow') or checkout.get('pageHorizontalOverflow'))).lower()}"
    )


def product_main_issues(state):
    issues = []
    root = state.get("root") or {}
    gallery = state.get("gallery") or {}
    info = state.get("info") or {}
    add_to_cart = state.get("addToCart") or {}
    if root.get("width", 0) <= 0 or root.get("height", 0) <= 0:
        issues.append("product_main_has_no_size")
    if root.get("height", 0) > state.get("viewportHeight", 1) * 12:
        issues.append("product_main_height_unreasonable")
    if not state.get("galleryVisible") or not state.get("infoVisible"):
        issues.append("product_main_section_missing")
    if not state.get("addToCartVisible"):
        issues.append("add_to_cart_not_visible")
    if state.get("readyImageCount", 0) < 1:
        issues.append("product_main_image_not_loaded")
    if state.get("pageHorizontalOverflow"):
        issues.append("page_horizontal_overflow")
    for label, rect in (("gallery", gallery), ("info", info), ("add", add_to_cart)):
        if (
            rect.get("left", 0) < root.get("left", 0) - 2
            or rect.get("right", 0) > root.get("right", 0) + 2
            or rect.get("top", 0) < root.get("top", 0) - 2
            or rect.get("bottom", 0) > root.get("bottom", 0) + 2
        ):
            issues.append(f"{label}_outside_product_main")
    overlap_width = max(
        0,
        min(gallery.get("right", 0), info.get("right", 0))
        - max(gallery.get("left", 0), info.get("left", 0)),
    )
    overlap_height = max(
        0,
        min(gallery.get("bottom", 0), info.get("bottom", 0))
        - max(gallery.get("top", 0), info.get("top", 0)),
    )
    # Themes often stack the mobile gallery and information panels with a small
    # negative margin. Treat that decorative seam as valid, while retaining a
    # gate for material panel overlap.
    if overlap_width > 32 and overlap_height > 32:
        issues.append("gallery_and_info_overlap")
    rendered = state.get("renderedRatio")
    natural = state.get("naturalRatio")
    if rendered and natural and max(rendered / natural, natural / rendered) > 2.5:
        issues.append("product_main_image_severely_stretched")
    issues.extend(accelerated_checkout_issues(state))
    return issues


def capture_product_main(ctx, page_model, case_name="product_main"):
    policy = ctx.screenshot_policy(case_name)
    if not policy["enabled"]:
        return {}
    paths = build_paths(
        ctx.current_dir,
        ctx.baseline_dir,
        ctx.diff_dir,
        case_name,
        legacy_baseline_dir=ctx.legacy_baseline_dir,
    )
    paths["report_case"] = policy["report_case"]
    started = time.perf_counter()
    try:
        target = product_main_target(page_model)
        prepare_for_screenshot(
            page_model.page,
            target,
            site_config=ctx.site_config,
            page_config=ctx.page_config,
            require_reviews=False,
            timeout=15,
            settle_delay=0.5,
            hide_dynamic=False,
        )
        target = product_main_target(page_model)
        payment_probe = probe_optional_accelerated_checkout(page_model, target)
        state = product_main_snapshot(page_model, target)
        if state.get("acceleratedCheckout") is not None:
            state["acceleratedCheckout"]["probe"] = payment_probe
        print_accelerated_checkout_diagnostics(state)
        issues = product_main_issues(state)
        ctx.artifact_manager.capture_element(target, paths["current"])
        paths.update(
            {
                "capture_duration_ms": round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
                "capture_attempts": 1,
                "dynamic_strategy": "mask_content",
                "content_mask_boxes": state.get("maskBoxes", []),
                "content_mask_coordinate_size": {
                    "width": state["root"]["width"],
                    "height": state["root"]["height"],
                },
                "structural_status": "failed" if issues else "passed",
                "structural_issues": issues,
                "structural_diagnostics": state,
            }
        )
        print(f"OK [{case_name}] product core captured")
        return {case_name: paths}
    except Exception as error:
        print(f"FAIL [{case_name}] capture failed: {error}")
        return {
            case_name: {
                "error": f"capture failed: {error}",
                "report_case": policy["report_case"],
            }
        }


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


def gallery_runtime_state(page_model):
    gallery = page_model.module("gallery")
    return gallery.evaluate(
        """
        (gallery) => {
            const visible = (node) => {
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden';
            };
            const images = Array.from(gallery.querySelectorAll('img'))
                .filter(visible);
            const active = gallery.querySelector([
                '.is-selected',
                '.active',
                '[aria-current="true"]',
                '[aria-selected="true"]'
            ].join(','));
            const rect = gallery.getBoundingClientRect();
            return {
                currentSources: images.map((image) => (
                    image.currentSrc || image.src || ''
                )).filter(Boolean),
                imageCount: images.length,
                readyImageCount: images.filter((image) => (
                    image.complete
                    && image.naturalWidth > 0
                    && image.naturalHeight > 0
                )).length,
                activeState: active ? [
                    active.getAttribute('data-index') || '',
                    active.getAttribute('aria-label') || '',
                    active.className || ''
                ].join('|') : '',
                width: rect.width,
                height: rect.height,
                loading: Boolean(gallery.querySelector(
                    '[aria-busy="true"], .loading, .is-loading'
                )),
                pageHorizontalOverflow:
                    document.documentElement.scrollWidth
                        > document.documentElement.clientWidth + 2,
            };
        }
        """
    )


def variant_selected_state(variant):
    return variant.evaluate(
        """
        (variant) => ({
            checked: Boolean(variant.checked),
            selected: Boolean(variant.selected),
            ariaChecked: variant.getAttribute('aria-checked'),
            ariaPressed: variant.getAttribute('aria-pressed'),
            ariaSelected: variant.getAttribute('aria-selected'),
            className: String(variant.className || ''),
            value: String(variant.value || variant.getAttribute('value') || ''),
        })
        """
    )


def state_is_selected(state):
    return bool(
        state.get("checked")
        or state.get("selected")
        or state.get("ariaChecked") == "true"
        or state.get("ariaPressed") == "true"
        or state.get("ariaSelected") == "true"
        or any(
            marker in str(state.get("className") or "").lower().split()
            for marker in ("active", "selected", "is-selected", "checked")
        )
    )


def variant_click_target(page_model, variant):
    element_id = variant.get_attribute("id")
    if element_id:
        label = page_model.page.locator(f"label[for='{element_id}']").first
        try:
            if label.is_visible(timeout=500):
                return label
        except Exception:
            pass
    try:
        return variant if variant.is_visible(timeout=500) else None
    except Exception:
        return None


def record_variant_skip(ctx, reason):
    policy = ctx.screenshot_policy("variant_changed_state")
    print(f"SKIP [variant_changed_state] {reason}")
    add_result(
        build_result(
            ctx.site,
            ctx.suite,
            ctx.page_name,
            policy["report_case"],
            "skipped",
            None,
            details={
                "screenshot_purpose": policy["purpose"],
                "skip_reason": reason,
                "affects_exit_code": False,
            },
        )
    )


def test_variants(ctx, page_model):
    print("\nVariant changed-state check")
    results = {}
    failures = []
    variants = page_model.variant_inputs()
    variant_check = page_model.config.get("variant_check") or {}
    deterministic = bool(variant_check.get("enabled"))
    option_name = str(variant_check.get("option_name") or "").strip()
    option_value = str(variant_check.get("option_value") or "").strip()

    try:
        candidates = []
        info_handle = page_model.module("info").element_handle()
        for index in range(min(variants.count(), 100)):
            variant = variants.nth(index)
            try:
                inside_product_info = variant.evaluate(
                    "(variant, info) => Boolean(info && info.contains(variant))",
                    info_handle,
                )
                if not inside_product_info:
                    continue
                if deterministic and (
                    variant.get_attribute("name") != option_name
                    or str(variant.get_attribute("value") or "")
                    != option_value
                ):
                    continue
                if not variant.is_enabled(timeout=500):
                    continue
                before_selected = variant_selected_state(variant)
                if state_is_selected(before_selected):
                    if deterministic:
                        record_variant_skip(
                            ctx,
                            "configured_variant_already_selected",
                        )
                        return results, failures
                    continue
                click_target = variant_click_target(page_model, variant)
                if click_target is None:
                    continue
                candidates.append((index, variant, click_target, before_selected))
                if deterministic:
                    break
                if len(candidates) >= 20:
                    break
            except Exception:
                continue

        if not candidates:
            reason = (
                "configured_variant_not_switchable"
                if deterministic
                else "no_switchable_enabled_variant"
            )
            record_variant_skip(ctx, reason)
            return results, failures

        last_reason = "variant_gallery_state_unchanged"
        for index, variant, click_target, before_selected in candidates:
            before_gallery = gallery_runtime_state(page_model)
            click_target.scroll_into_view_if_needed(timeout=10000)
            click_target.click(timeout=10000)

            deadline = time.time() + 10
            after_gallery = gallery_runtime_state(page_model)
            while after_gallery.get("loading") and time.time() < deadline:
                time.sleep(0.2)
                after_gallery = gallery_runtime_state(page_model)

            after_selected = variant_selected_state(variant)
            selected_changed = (
                not state_is_selected(before_selected)
                and state_is_selected(after_selected)
            )
            gallery_changed = (
                before_gallery.get("currentSources")
                != after_gallery.get("currentSources")
                or before_gallery.get("activeState")
                != after_gallery.get("activeState")
            )
            gallery_ready = (
                after_gallery.get("imageCount", 0) > 0
                and after_gallery.get("readyImageCount", 0) > 0
                and after_gallery.get("width", 0) > 0
                and after_gallery.get("height", 0) > 0
                and not after_gallery.get("loading")
                and not after_gallery.get("pageHorizontalOverflow")
            )
            if not selected_changed:
                last_reason = f"variant_{index}_selected_state_unchanged"
                continue
            if not gallery_changed:
                last_reason = f"variant_{index}_gallery_state_unchanged"
                continue
            if not gallery_ready:
                failures.append(
                    f"Variant {index} gallery did not become ready"
                )
                return results, failures

            results = capture_product_main(
                ctx,
                page_model,
                case_name="variant_changed_state",
            )
            if results.get("variant_changed_state"):
                results["variant_changed_state"]["variant_assertions"] = {
                    "candidate_index": index,
                    "deterministic": deterministic,
                    "option_name": option_name if deterministic else None,
                    "option_value": option_value if deterministic else None,
                    "selected_state_changed": selected_changed,
                    "gallery_state_changed": gallery_changed,
                    "gallery_ready": gallery_ready,
                }
            if deterministic:
                print(
                    "OK configured variant "
                    f"{option_name}={option_value} changed selection "
                    "and gallery state"
                )
            else:
                print(
                    f"OK variant {index} changed selection and gallery state"
                )
            return results, failures

        if deterministic:
            failures.append(
                "Configured variant "
                f"{option_name}={option_value} failed: {last_reason}"
            )
        else:
            record_variant_skip(ctx, last_reason)
    except Exception as error:
        print(f"FAIL Variant checks failed: {error}")
        failures.append(f"Variant checks failed: {error}")

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
        structure_failures, _structure_results = run_structure_checks(
            ctx,
            page,
        )
        failures.extend(structure_failures)

        product_main_results = capture_product_main(ctx, page_model)

        hide_dynamic_elements(page, ctx.site_config, ctx.page_config)
        global_results = capture_global_screenshot(ctx, page)
        first_screen_results = capture_first_screen(ctx, page)

        print("Reloading product page before read-only variant selection")
        page_model.open()
        time.sleep(2)
        page_model.wait_until_ready()
        collect_runtime_health_fail_open(page_model.runtime)

        with page_model.runtime.phase("variant_interaction"):
            variant_results, variant_failures = test_variants(ctx, page_model)
        failures.extend(variant_failures)

        sticky_results = {}
        sticky_locator = ctx.locator("sticky_add_to_cart")
        if is_mobile_viewport() and sticky_locator:
            sticky_results = capture_modules(
                page,
                {"sticky_add_to_cart": sticky_locator},
                ctx.current_dir,
                ctx.baseline_dir,
                ctx.diff_dir,
                require_reviews=False,
                site_config=ctx.site_config,
                page_config=ctx.page_config,
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
                product_main_results,
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
        failures.extend(
            process_results(
                sticky_results,
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
