import hashlib
import json
import time


VALID_DYNAMIC_STRATEGIES = (
    "mask_content",
    "layout_only",
    "ignore_visual",
)


def audit_page_dynamic_masks(page, page_config, full_page):
    regions = (page_config or {}).get("dynamic_regions", []) or []
    modules = (page_config or {}).get("modules", {}) or {}
    boxes = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        strategy = region.get("strategy")
        selectors = [
            value
            for value in (region.get("masks") or [])
            if isinstance(value, str) and value.strip()
        ]
        mask_root = strategy in ("layout_only", "ignore_visual")
        if not selectors and not mask_root:
            continue
        locator_value = modules.get(region.get("module"))
        if locator_value:
            method, value = locator_value
            selector = value if method == "css" else f"xpath={value}"
        else:
            selector = region.get("selector")
        if not selector:
            continue
        try:
            root = page.locator(selector).first
            boxes.extend(
                root.evaluate(
                    """
                    (root, options) => {
                        const visible = (node) => {
                            const rect = node.getBoundingClientRect();
                            const style = window.getComputedStyle(node);
                            return rect.width > 0 && rect.height > 0
                                && style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && style.opacity !== '0';
                        };
                        const nodes = options.maskRoot
                            ? [root]
                            : options.selectors.flatMap(
                                (selector) => Array.from(
                                    root.querySelectorAll(selector)
                                )
                            );
                        const width = options.fullPage
                            ? Math.max(
                                document.documentElement.scrollWidth,
                                document.body
                                    ? document.body.scrollWidth
                                    : 0
                            )
                            : window.innerWidth;
                        const height = options.fullPage
                            ? Math.max(
                                document.documentElement.scrollHeight,
                                document.body
                                    ? document.body.scrollHeight
                                    : 0
                            )
                            : window.innerHeight;
                        return nodes.filter(visible).map((node) => {
                            const rect = node.getBoundingClientRect();
                            const offsetX = options.fullPage
                                ? window.scrollX
                                : 0;
                            const offsetY = options.fullPage
                                ? window.scrollY
                                : 0;
                            return {
                                left: options.maskRoot
                                    && options.fullPage
                                    ? 0
                                    : Math.max(
                                        0,
                                        rect.left + offsetX
                                    ),
                                top: Math.max(
                                    0,
                                    rect.top + offsetY
                                ),
                                right: options.maskRoot
                                    && options.fullPage
                                    ? width
                                    : Math.min(
                                        width,
                                        rect.right + offsetX
                                    ),
                                bottom: options.maskRoot
                                    && options.fullPage
                                    ? height
                                    : Math.min(
                                        height,
                                        rect.bottom + offsetY
                                    ),
                            };
                        }).filter(
                            (box) => box.right > box.left
                                && box.bottom > box.top
                        );
                    }
                    """,
                    {
                        "selectors": selectors,
                        "maskRoot": mask_root,
                        "fullPage": bool(full_page),
                    },
                )
            )
        except Exception:
            # The normal DOM/module checks report a missing dynamic root.
            continue

    if not boxes:
        return {}
    coordinate_size = page.evaluate(
        """
        (fullPage) => ({
            width: fullPage
                ? Math.max(
                    document.documentElement.scrollWidth,
                    document.body ? document.body.scrollWidth : 0
                )
                : window.innerWidth,
            height: fullPage
                ? Math.max(
                    document.documentElement.scrollHeight,
                    document.body ? document.body.scrollHeight : 0
                )
                : window.innerHeight,
        })
        """,
        bool(full_page),
    )
    return {
        "dynamic_strategy": "mask_content",
        "structural_status": "passed",
        "structural_issues": [],
        "content_changes": ["dynamic_regions_content_changed"],
        "content_mask_boxes": boxes,
        "content_mask_coordinate_size": coordinate_size,
    }


def dynamic_region_for_case(page_config, case):
    for region in (page_config or {}).get("dynamic_regions", []) or []:
        if not isinstance(region, dict):
            continue
        if region.get("name") == case or region.get("module") == case:
            strategy = str(region.get("strategy") or "").strip().lower()
            if strategy not in VALID_DYNAMIC_STRATEGIES:
                raise ValueError(
                    f"Unsupported dynamic region strategy: {strategy!r}"
                )
            return dict(region)
    return None


def audit_dynamic_region(element, region, page_config=None):
    strategy = region.get("strategy")
    region_name = region.get("name") or region.get("module") or "dynamic"
    region_type = str(region.get("region_type") or "grid").strip().lower()
    item_selector = region.get("item_selector")
    mask_selectors = [
        value
        for value in (region.get("masks") or [])
        if isinstance(value, str) and value.strip()
    ]
    if not item_selector:
        product_card = (page_config or {}).get("product_card")
        if (
            isinstance(product_card, (list, tuple))
            and len(product_card) == 2
            and product_card[0] == "css"
        ):
            item_selector = product_card[1]
    checks = (
        ((page_config or {}).get("layout_checks") or {}).get(
            region.get("name"),
            {},
        )
    )
    region_selector = _region_selector(page_config, region)
    audit_started = time.perf_counter()
    state = element.evaluate(
        """
        (root, options) => {
            const visible = (node) => {
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                return rect.width > 0 && rect.height > 0
                    && style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && style.opacity !== '0';
            };
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
            const items = options.itemSelector
                ? Array.from(root.querySelectorAll(options.itemSelector))
                : [];
            const rootRect = rectOf(root);
            const visibleWithinRoot = (node) => {
                if (!visible(node)) return false;
                if (
                    node.getAttribute('aria-hidden') === 'true'
                    || node.closest('[aria-hidden="true"]')
                ) {
                    return false;
                }
                const rect = node.getBoundingClientRect();
                const intersectionWidth = Math.min(
                    rect.right,
                    rootRect.right
                ) - Math.max(rect.left, rootRect.left);
                const intersectionHeight = Math.min(
                    rect.bottom,
                    rootRect.bottom
                ) - Math.max(rect.top, rootRect.top);
                return intersectionWidth > 1 && intersectionHeight > 1;
            };
            const maskBoxes = [];
            for (const selector of options.maskSelectors || []) {
                for (const node of root.querySelectorAll(selector)) {
                    if (!visible(node)) continue;
                    const rect = node.getBoundingClientRect();
                    maskBoxes.push({
                        left: Math.max(0, rect.left - rootRect.left),
                        top: Math.max(0, rect.top - rootRect.top),
                        right: Math.min(
                            rootRect.width,
                            rect.right - rootRect.left
                        ),
                        bottom: Math.min(
                            rootRect.height,
                            rect.bottom - rootRect.top
                        ),
                    });
                }
            }
            const visibleItems = items.filter(visibleWithinRoot);
            const itemData = visibleItems.map((item) => {
                const image = item.querySelector('img');
                const title = item.querySelector(
                    options.titleSelector
                    || '.collection-item__title, '
                    + '[class*="collection"][class*="title"], '
                    + '.grid-product__title, .card__heading, '
                    + '.product-card__title, [class*="product"][class*="title"]'
                );
                const price = item.querySelector(
                    options.priceSelector
                    || '.price, [class*="price"]'
                );
                return {
                    rect: rectOf(item),
                    href: (item.querySelector('a[href]') || {}).href || '',
                    image: image ? (image.currentSrc || image.src || '') : '',
                    imageReady: image
                        ? Boolean(image.complete && image.naturalWidth > 0)
                        : false,
                    title: title ? String(title.textContent || '').trim() : '',
                    price: price ? String(price.textContent || '').trim() : '',
                    availability: String(
                        item.getAttribute('data-available') || ''
                    ),
                };
            });
            return {
                visible: visible(root),
                rootRect,
                containerHorizontalOverflow:
                    root.scrollWidth > root.clientWidth + 2,
                pageHorizontalOverflow:
                    document.documentElement.scrollWidth
                        > document.documentElement.clientWidth + 2,
                itemCount: items.length,
                visibleItemCount: visibleItems.length,
                isCarousel:
                    options.configuredCarousel
                    || root.matches(
                        '.flickity-enabled, .swiper, .slick-slider, '
                        + '[data-slider], [aria-roledescription="carousel"]'
                    )
                    || Boolean(root.querySelector(
                        '.flickity-enabled, .swiper, .slick-slider, '
                        + '[data-slider], [aria-roledescription="carousel"]'
                    )),
                items: itemData,
                maskBoxes,
            };
        }
        """,
        {
            "itemSelector": item_selector,
            "maskSelectors": mask_selectors,
            "titleSelector": checks.get("title_selector"),
            "priceSelector": checks.get("price_selector"),
            "configuredCarousel": region_type.endswith("carousel"),
        },
    )
    result = evaluate_structural_snapshot(
        state,
        strategy,
        checks,
        region_type=region_type,
    )
    diagnostic = result.setdefault("structural_diagnostics", {})
    diagnostic.update(
        {
            "region": region_name,
            "region_selector": region_selector,
            "item_selector": item_selector or "",
            "audit_duration_ms": round(
                (time.perf_counter() - audit_started) * 1000,
                2,
            ),
        }
    )
    result["content_mask_boxes"] = state.get("maskBoxes", [])
    root_rect = state.get("rootRect") or {}
    result["content_mask_coordinate_size"] = {
        "width": root_rect.get("width", 0),
        "height": root_rect.get("height", 0),
    }
    return result


def evaluate_structural_snapshot(
    state,
    strategy,
    checks=None,
    region_type="grid",
):
    checks = checks or {}
    normalized_type = str(region_type or "grid").strip().lower()
    is_carousel = bool(
        state.get("isCarousel")
        or normalized_type.endswith("carousel")
    )
    issues = []
    if not state.get("visible"):
        issues.append("dynamic_region_not_visible")
    root = state.get("rootRect") or {}
    if root.get("width", 0) <= 0 or root.get("height", 0) <= 0:
        issues.append("dynamic_region_has_no_size")
    page_overflow = bool(
        state.get(
            "pageHorizontalOverflow",
            state.get("horizontalOverflow", False),
        )
    )
    container_overflow = bool(
        state.get(
            "containerHorizontalOverflow",
            state.get("horizontalOverflow", False),
        )
    )
    if checks.get("check_horizontal_overflow", True) and (
        page_overflow or (container_overflow and not is_carousel)
    ):
        issues.append("horizontal_overflow")

    items = state.get("items") or []
    matched_count = int(state.get("itemCount", 0) or 0)
    visible_count = int(state.get("visibleItemCount", 0) or 0)
    hidden_count = max(0, matched_count - visible_count)
    image_success_count = sum(
        1 for item in items if item.get("imageReady")
    )
    image_total_count = len(items)
    count_rules_enabled = (
        strategy == "layout_only"
        or checks.get("minimum_count") is not None
    )
    minimum = (
        int(checks.get("minimum_count", 1))
        if count_rules_enabled
        else 0
    )
    diagnostics = {
        "region": "",
        "region_selector": "",
        "item_selector": "",
        "minimum_count": minimum,
        "matched_count": matched_count,
        "visible_count": visible_count,
        "hidden_count": hidden_count,
        "image_success_count": image_success_count,
        "image_total_count": image_total_count,
        "is_carousel": is_carousel,
        "region_type": normalized_type,
        "page_horizontal_overflow": page_overflow,
        "container_horizontal_overflow": container_overflow,
    }
    if count_rules_enabled:
        if matched_count == 0:
            issues.append("dynamic_region_item_selector_no_match")
        if visible_count == 0:
            issues.append("dynamic_region_no_visible_items")
        if is_carousel:
            if matched_count < minimum:
                issues.append("carousel_below_minimum_count")
        elif visible_count < minimum:
            issues.append("product_grid_below_minimum_count")
        rects = [item.get("rect") or {} for item in items]
        if checks.get("check_overlap", True) and _has_overlap(rects):
            issues.append("product_cards_overlap")
        if checks.get("check_uniform_width", True) and not _uniform_widths(
            rects
        ):
            issues.append("product_card_widths_inconsistent")
        if checks.get("check_reasonable_height", True) and not _reasonable_heights(
            rects,
            float(checks.get("maximum_height_ratio", 2.5)),
        ):
            issues.append("product_card_heights_inconsistent")
        expected_columns = checks.get("expected_columns")
        if expected_columns is not None and any(
            count != int(expected_columns)
            for count in _row_counts(rects)[:-1]
        ):
            issues.append("product_grid_column_count_unexpected")
        elif checks.get("check_columns", True):
            full_rows = _row_counts(rects)[:-1]
            if full_rows and len(set(full_rows)) > 1:
                issues.append("product_grid_column_count_unexpected")
        if is_carousel:
            require_image = checks.get("check_image_visible", True)
            require_title = checks.get("check_title_present", True)
            require_price = checks.get("check_price_present", True)
            valid_cards = [
                item
                for item in items
                if (not require_image or item.get("imageReady"))
                and (not require_title or item.get("title"))
                and (not require_price or item.get("price"))
            ]
            diagnostics["valid_card_count"] = len(valid_cards)
            if visible_count > 0 and not valid_cards:
                issues.append("carousel_item_structure_missing")
        else:
            if checks.get("check_image_visible", True) and items:
                success_rate = image_success_count / len(items)
                minimum_rate = float(
                    checks.get("minimum_image_success_rate", 0.9)
                )
                if success_rate < minimum_rate:
                    issues.append("product_image_success_rate_low")
            if checks.get("check_title_present", True) and any(
                not item.get("title") for item in items
            ):
                issues.append("product_title_missing")
            if checks.get("check_price_present", True) and any(
                not item.get("price") for item in items
            ):
                issues.append("product_price_missing")

    snapshot = content_snapshot(items)
    return {
        "dynamic_strategy": strategy,
        "structural_status": "failed" if issues else "passed",
        "structural_issues": issues,
        "structural_diagnostics": diagnostics,
        "layout_snapshot": {
            "item_count": matched_count,
            "visible_item_count": visible_count,
            "content_fingerprint": snapshot["fingerprint"],
        },
    }


def _region_selector(page_config, region):
    module_name = region.get("module")
    modules = (page_config or {}).get("modules", {}) or {}
    value = modules.get(module_name)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        method, selector = value
        return selector if method == "css" else f"xpath={selector}"
    selector = region.get("selector")
    return str(selector or "")


def content_snapshot(items):
    normalized = [
        {
            "href": item.get("href", ""),
            "image": item.get("image", ""),
            "title": item.get("title", ""),
            "price": item.get("price", ""),
            "availability": item.get("availability", ""),
        }
        for item in items or []
    ]
    serialized = json.dumps(
        normalized,
        sort_keys=True,
        ensure_ascii=False,
    )
    return {
        "items": normalized,
        "fingerprint": hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest(),
    }


def classify_content_changes(before, after):
    before_items = (before or {}).get("items", [])
    after_items = (after or {}).get("items", [])
    changes = []
    if len(before_items) != len(after_items):
        changes.append("product_count_changed")
    before_order = [item.get("href") for item in before_items]
    after_order = [item.get("href") for item in after_items]
    if before_order != after_order and len(before_items) == len(after_items):
        changes.append("product_order_changed")
    comparisons = (
        ("image", "product_image_changed"),
        ("title", "product_title_changed"),
        ("price", "product_price_changed"),
        ("availability", "availability_changed"),
    )
    before_by_href = {
        item.get("href"): item for item in before_items if item.get("href")
    }
    after_by_href = {
        item.get("href"): item for item in after_items if item.get("href")
    }
    for key, label in comparisons:
        if any(
            before_by_href[href].get(key) != after_by_href[href].get(key)
            for href in before_by_href.keys() & after_by_href.keys()
        ):
            changes.append(label)
    return changes


def _has_overlap(rects):
    for index, first in enumerate(rects):
        for second in rects[index + 1:]:
            x_overlap = min(first.get("right", 0), second.get("right", 0)) - max(
                first.get("left", 0),
                second.get("left", 0),
            )
            y_overlap = min(first.get("bottom", 0), second.get("bottom", 0)) - max(
                first.get("top", 0),
                second.get("top", 0),
            )
            if x_overlap > 2 and y_overlap > 2:
                return True
    return False


def _uniform_widths(rects, tolerance=0.15):
    widths = [float(rect.get("width", 0) or 0) for rect in rects]
    widths = [width for width in widths if width > 0]
    if len(widths) < 2:
        return True
    mean = sum(widths) / len(widths)
    return all(abs(width - mean) / mean <= tolerance for width in widths)


def _reasonable_heights(rects, maximum_ratio):
    heights = [float(rect.get("height", 0) or 0) for rect in rects]
    heights = [height for height in heights if height > 0]
    if len(heights) < 2:
        return True
    return max(heights) / min(heights) <= maximum_ratio


def _row_counts(rects, tolerance=4):
    rows = []
    for rect in sorted(
        rects,
        key=lambda value: (
            float(value.get("top", 0) or 0),
            float(value.get("left", 0) or 0),
        ),
    ):
        top = float(rect.get("top", 0) or 0)
        for row in rows:
            if abs(row["top"] - top) <= tolerance:
                row["count"] += 1
                break
        else:
            rows.append({"top": top, "count": 1})
    return [row["count"] for row in rows]
