from playwright_checks.runtime.models import RuntimeFinding, SEVERITY_ORDER
from playwright_checks.utils.waits import selector_for


MAX_RUNTIME_ELEMENT_PROBES = 200


DEFAULT_ERROR_PAGE_PATTERNS = {
    "security_challenge": [
        "access denied",
        "attention required",
        "captcha",
        "checking your browser",
        "cloudflare",
        "verify you are human",
        "访问受限",
    ],
    "rate_limited": [
        "local_rate_limited",
        "too many requests",
        "rate limit",
        "请求过于频繁",
    ],
    "application_error": [
        "application error",
        "internal server error",
        "liquid error",
        "service unavailable",
        "something went wrong",
        "temporarily unavailable",
        "please try again later",
        "系统异常",
        "页面错误",
        "加载失败",
        "服务不可用",
        "请稍后重试",
        "应用程序错误",
        "服务暂不可用",
    ],
}


def collect_health_fingerprint(page, page_config, runtime_config):
    loading_selectors = runtime_config.get("loading_selectors", []) or []
    loading_regions = runtime_config.get(
        "loading_critical_selectors",
        [],
    ) or []
    metrics = _collect_page_metrics(
        page,
        loading_selectors,
        loading_regions,
    )
    confirmation_ms = max(
        0,
        int(runtime_config.get("loading_confirmation_ms", 1000) or 0),
    )
    if metrics.get("loading_visible_count", 0) and confirmation_ms:
        page.wait_for_timeout(confirmation_ms)
        metrics = _collect_page_metrics(
            page,
            loading_selectors,
            loading_regions,
        )

    (
        metrics["critical_elements"],
        metrics["optional_elements"],
    ) = _runtime_elements(
        page,
        page_config,
        runtime_config,
        metrics.get("body_text", ""),
    )
    metrics["missing_critical_elements"] = [
        item["name"]
        for item in metrics["critical_elements"]
        if not item["satisfied"]
    ]
    metrics["missing_optional_elements"] = [
        item["name"]
        for item in metrics["optional_elements"]
        if not item["satisfied"]
    ]
    return metrics


def _collect_page_metrics(page, loading_selectors, loading_regions):
    return page.evaluate(
        """
        ({ loadingSelectors, loadingRegions }) => {
          const body = document.body;
          const nodes = body ? body.querySelectorAll("*") : [];
          const visible = (element) => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== "none" &&
                   style.visibility !== "hidden" &&
                   Number(style.opacity || 1) > 0 &&
                   rect.width > 0 && rect.height > 0;
          };
          const inViewport = (rect) =>
            rect.bottom > 0 && rect.right > 0 &&
            rect.top < window.innerHeight && rect.left < window.innerWidth;
          const overlaps = (left, right) =>
            left.left < right.right && left.right > right.left &&
            left.top < right.bottom && left.bottom > right.top;
          const regions = [];
          for (const selector of loadingRegions || []) {
            try {
              regions.push(...document.querySelectorAll(selector));
            } catch (_) {
              // Invalid optional selectors are handled by fail-open validation.
            }
          }
          const loadingNodes = new Set();
          for (const selector of loadingSelectors || []) {
            try {
              for (const element of document.querySelectorAll(selector)) {
                loadingNodes.add(element);
              }
            } catch (_) {
              // Invalid optional selectors are handled by fail-open validation.
            }
          }
          let loadingVisible = 0;
          let loadingCritical = 0;
          for (const element of loadingNodes) {
            if (element.tagName === "IMG" || !visible(element)) {
              continue;
            }
            const rect = element.getBoundingClientRect();
            const inCriticalRegion = regions.some((region) => {
              if (!visible(region)) {
                return false;
              }
              return region.contains(element) ||
                overlaps(rect, region.getBoundingClientRect());
            });
            if (!inViewport(rect) && !inCriticalRegion) {
              continue;
            }
            loadingVisible += 1;
            if (inCriticalRegion) {
              loadingCritical += 1;
            }
          }
          const images = [...document.images].filter(visible);
          return {
            url: location.href,
            title: document.title || "",
            body_text: body ? (body.innerText || "").slice(0, 12000) : "",
            body_text_length: body ? (body.innerText || "").trim().length : 0,
            dom_node_count: nodes.length,
            scroll_height: body ? body.scrollHeight : 0,
            page_height: body ? body.scrollHeight : 0,
            viewport_height: window.innerHeight,
            visible_image_count: images.length,
            broken_visible_image_count: images.filter(
              image => image.complete && image.naturalWidth === 0
            ).length,
            broken_image_count: images.filter(
              image => image.complete && image.naturalWidth === 0
            ).length,
            visible_button_count: [...document.querySelectorAll(
              "button, input[type=button], input[type=submit], [role=button]"
            )].filter(visible).length,
            horizontal_overflow_px: Math.max(
              0,
              (document.documentElement?.scrollWidth || 0) - window.innerWidth
            ),
            horizontal_overflow:
              (document.documentElement?.scrollWidth || 0) > window.innerWidth,
            loading_visible_count: loadingVisible,
            loading_element_count: loadingVisible,
            loading_critical_count: loadingCritical
          };
        }
        """,
        {
            "loadingSelectors": loading_selectors,
            "loadingRegions": loading_regions,
        },
    )


def _runtime_elements(page, page_config, runtime_config, body_text):
    critical = _selector_definitions(
        runtime_config.get("critical_selectors") or _inferred_critical_selectors(
            page_config,
            runtime_config.get("_page_name"),
        ),
        default_prefix="critical",
    )
    optional = _selector_definitions(
        runtime_config.get("optional_selectors") or [],
        default_prefix="optional",
    )
    return (
        _evaluate_selector_definitions(page, critical, body_text),
        _evaluate_selector_definitions(page, optional, body_text),
    )


def _selector_definitions(values, default_prefix):
    definitions = []
    for index, item in enumerate(values or [], 1):
        if isinstance(item, dict):
            selector = item.get("selector")
            name = item.get("name") or f"{default_prefix}_{index}"
            allow_text_patterns = item.get("allow_text_patterns", [])
            requires_visible = item.get("visible", True)
            requires_non_empty_text = bool(
                item.get("requires_non_empty_text", False)
                or str(name) == "product.price"
            )
        else:
            selector = item
            name = f"{default_prefix}_{index}"
            allow_text_patterns = []
            requires_visible = True
            requires_non_empty_text = False
        if selector:
            definitions.append(
                {
                    "name": str(name),
                    "selector": selector,
                    "allow_text_patterns": list(allow_text_patterns or []),
                    "requires_visible": bool(requires_visible),
                    "requires_non_empty_text": requires_non_empty_text,
                }
            )
    return definitions


def _inferred_critical_selectors(page_config, page_name):
    if page_name == "collection" and page_config.get("product_card"):
        return [
            {
                "name": "collection.product_card",
                "selector": page_config["product_card"],
            }
        ]
    if page_name == "product":
        content_checks = page_config.get("content_checks", {})
        title = content_checks.get(
            "title",
            [
                "css",
                (
                    "h1, .product-single__title, .product__title, "
                    "[class*='product-title'], [class*='product__title']"
                ),
            ],
        )
        price = content_checks.get(
            "price",
            [
                "css",
                (
                    ".price, .product__price, .product-single__price, "
                    "[class*='price']"
                ),
            ],
        )
        purchase = page_config.get("modules", {}).get("add_to_cart")
        values = [
            {"name": "product.title", "selector": title},
            {"name": "product.price", "selector": price},
        ]
        if purchase:
            values.append(
                {
                    "name": "product.purchase_state",
                    "selector": purchase,
                    "allow_text_patterns": [
                        "sold out",
                        "out of stock",
                        "unavailable",
                    ],
                }
            )
        return values
    if page_name == "home":
        return [{"name": "home.main", "selector": "main"}]
    return []


def _evaluate_selector_definitions(page, definitions, body_text):
    lowered_body = str(body_text or "").lower()
    results = []
    for definition in definitions:
        name = definition["name"]
        locator_value = definition["selector"]
        requires_visible = definition["requires_visible"]
        requires_non_empty_text = definition["requires_non_empty_text"]
        selector = None
        try:
            selector = (
                selector_for(tuple(locator_value))
                if isinstance(locator_value, (list, tuple))
                else str(locator_value)
            )
            locator = page.locator(selector)
            count = locator.count()
            probe = locator.evaluate_all(
                """
                (elements, options) => {
                  const limit = Math.min(elements.length, options.limit);
                  let checkedCount = 0;
                  let matchedIndex = null;
                  let visible = false;

                  for (let index = 0; index < limit; index += 1) {
                    checkedCount += 1;
                    const element = elements[index];
                    if (!(element instanceof Element)) {
                      continue;
                    }

                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    const nodeVisible =
                      style.display !== "none" &&
                      style.visibility !== "hidden" &&
                      Number(style.opacity || 1) > 0 &&
                      rect.width > 0 &&
                      rect.height > 0;
                    visible = visible || nodeVisible;

                    const text = typeof element.innerText === "string"
                      ? element.innerText
                      : (element.textContent || "");
                    const hasText = text.trim().length > 0;
                    const satisfiesVisibility =
                      !options.requiresVisible || nodeVisible;
                    const satisfiesText =
                      !options.requiresNonEmptyText || hasText;
                    if (satisfiesVisibility && satisfiesText) {
                      matchedIndex = index;
                      break;
                    }
                  }

                  return {
                    checkedCount,
                    matchedIndex,
                    visible,
                  };
                }
                """,
                {
                    "limit": MAX_RUNTIME_ELEMENT_PROBES,
                    "requiresVisible": requires_visible,
                    "requiresNonEmptyText": requires_non_empty_text,
                },
            )
            visible = bool(probe.get("visible"))
            matched_index = probe.get("matchedIndex")
            checked_count = int(probe.get("checkedCount", 0) or 0)
            satisfied = matched_index is not None
            satisfied_by_text = False
            if not satisfied and any(
                str(pattern).lower() in lowered_body
                for pattern in definition["allow_text_patterns"]
            ):
                satisfied = True
                satisfied_by_text = True
            results.append(
                {
                    "name": name,
                    "selector": selector,
                    "count": count,
                    "match_count": count,
                    "visible": visible,
                    "satisfied": satisfied,
                    "satisfied_by_text": satisfied_by_text,
                    "matched_index": matched_index,
                    "checked_count": checked_count,
                    "probe_limit": MAX_RUNTIME_ELEMENT_PROBES,
                    "probe_truncated": count > MAX_RUNTIME_ELEMENT_PROBES,
                    "requires_non_empty_text": requires_non_empty_text,
                }
            )
        except Exception as error:
            results.append(
                {
                    "name": name,
                    "selector": selector,
                    "count": 0,
                    "match_count": 0,
                    "visible": False,
                    "satisfied": False,
                    "satisfied_by_text": False,
                    "matched_index": None,
                    "checked_count": 0,
                    "probe_limit": MAX_RUNTIME_ELEMENT_PROBES,
                    "probe_truncated": False,
                    "requires_non_empty_text": requires_non_empty_text,
                    "probe_error": f"{type(error).__name__}: {error}",
                }
            )
    return results


def build_findings(navigation, collector_snapshot, health, config):
    findings = []
    status = navigation.get("status")
    missing = health.get("missing_critical_elements", [])
    missing_optional = health.get("missing_optional_elements", [])
    critical = health.get("critical_elements", [])
    all_missing = bool(critical) and len(missing) == len(critical)
    body_text = (
        f"{health.get('title', '')}\n{health.get('body_text', '')}"
    ).lower()
    blank_text = int(config.get("blank_page_text_threshold", 30))
    blank_nodes = int(config.get("blank_page_node_threshold", 20))
    legitimate_empty = any(
        str(pattern).lower() in body_text
        for pattern in config.get("legitimate_empty_patterns", [])
    )
    page_height = health.get("page_height", health.get("scroll_height", 0))
    viewport_height = health.get("viewport_height", 0)
    blank = (
        not health.get("probe_error")
        and health.get("body_text_length", 0) < blank_text
        and health.get("dom_node_count", 0) < blank_nodes
        and health.get("visible_image_count", 0) == 0
        and health.get("loading_visible_count", 0) == 0
        and page_height <= max(viewport_height * 1.5, viewport_height + 200)
        and (all_missing or not critical)
        and not legitimate_empty
    )

    matched_error = _matched_error_page(body_text, config)
    strong_error_page = (
        status in (401, 403, 429)
        or (status is not None and status >= 500)
        or bool(matched_error and (missing or blank))
    )

    if collector_snapshot.get("page_crashed"):
        findings.append(
            RuntimeFinding(
                "critical",
                "page_crashed",
                "The Playwright page crashed during the check.",
                category="page_crash",
            )
        )

    if status is not None and status >= 500:
        findings.append(
            RuntimeFinding(
                "critical",
                "network_error",
                f"Main document returned HTTP {status}.",
                category="network_error",
                evidence={"status": status},
            )
        )
    elif status in (401, 403):
        findings.append(
            RuntimeFinding(
                "critical",
                "access_denied",
                f"Main document returned HTTP {status}.",
                category="access_denied",
                evidence={"status": status},
            )
        )
    elif status == 429:
        findings.append(
            RuntimeFinding(
                "critical",
                "rate_limited",
                "Main document returned HTTP 429.",
                category="rate_limited",
                evidence={"status": status},
            )
        )

    if matched_error and strong_error_page:
        findings.append(
            RuntimeFinding(
                "critical",
                matched_error["reason_code"],
                f"Rendered page matches {matched_error['pattern']!r}.",
                category="error_page",
                evidence=matched_error,
            )
        )
    if blank:
        findings.append(
            RuntimeFinding(
                "critical",
                "blank_page",
                "Rendered page has no meaningful text, DOM, image, or critical module.",
                category="blank_page",
                evidence={
                    "body_text_length": health.get("body_text_length"),
                    "dom_node_count": health.get("dom_node_count"),
                },
            )
        )

    page_errors = _events(collector_snapshot, "page_error")
    first_party_failures = [
        event
        for event in collector_snapshot.get("events", [])
        if event.get("party") == "first_party"
        and event.get("event_type") in ("request_failed", "http_error")
        and event.get("blocking", True)
        and (
            event.get("event_type") == "request_failed"
            or event.get("status", 0) >= 400
        )
    ]
    first_party_server_errors = [
        event
        for event in first_party_failures
        if event.get("event_type") == "http_error"
        and event.get("status", 0) >= 500
        and event.get("resource_type")
        in ("document", "script", "xhr", "fetch")
    ]

    if missing and (page_errors or first_party_failures):
        findings.append(
            RuntimeFinding(
                "error",
                "partial_render_failure",
                "Critical components are missing while first-party runtime errors are present.",
                category="partial_render_failure",
                count=len(missing),
                evidence={"missing": missing},
            )
        )
    elif missing:
        findings.append(
            RuntimeFinding(
                "warning",
                "missing_critical_component",
                "One or more configured critical components are missing.",
                category="missing_critical_component",
                count=len(missing),
                evidence={"missing": missing},
            )
        )

    if missing_optional:
        findings.append(
            RuntimeFinding(
                "info",
                "missing_optional_component",
                "One or more optional Runtime Health components are absent.",
                category="optional_component",
                count=len(missing_optional),
                evidence={"missing": missing_optional},
            )
        )

    if first_party_server_errors:
        findings.append(
            RuntimeFinding(
                "error",
                "first_party_server_error",
                "A first-party document, script, XHR, or fetch returned HTTP 5xx.",
                category="network_error",
                count=sum(event.get("count", 1) for event in first_party_server_errors),
            )
        )

    if page_errors and not missing:
        findings.append(
            RuntimeFinding(
                "warning",
                "page_error",
                "The page emitted an uncaught JavaScript error.",
                category="javascript_error",
                count=sum(event.get("count", 1) for event in page_errors),
            )
        )

    console_errors = [
        event
        for event in collector_snapshot.get("events", [])
        if event.get("event_type") == "console"
        and event.get("level") == "error"
        and event.get("party") != "third_party"
    ]
    if console_errors:
        findings.append(
            RuntimeFinding(
                "warning",
                "console_error",
                "The page emitted one or more console errors.",
                category="javascript_error",
                count=sum(event.get("count", 1) for event in console_errors),
            )
        )

    console_warnings = [
        event
        for event in collector_snapshot.get("events", [])
        if event.get("event_type") == "console"
        and event.get("level") == "warning"
    ]
    if console_warnings:
        findings.append(
            RuntimeFinding(
                "warning",
                "console_warning",
                "The page emitted one or more console warnings.",
                category="console_warning",
                count=sum(event.get("count", 1) for event in console_warnings),
            )
        )

    third_party_errors = [
        event
        for event in collector_snapshot.get("events", [])
        if event.get("party") == "third_party"
        and event.get("blocking", True)
        and (
            event.get("event_type") == "request_failed"
            or (
                event.get("event_type") == "http_error"
                and event.get("status", 0) >= 400
            )
            or (
                event.get("event_type") == "console"
                and event.get("level") == "error"
            )
        )
    ]
    if third_party_errors:
        findings.append(
            RuntimeFinding(
                "warning",
                "third_party_error",
                "Third-party resources emitted errors or failed requests.",
                category="third_party_error",
                count=sum(event.get("count", 1) for event in third_party_errors),
            )
        )

    first_party_request_failures = [
        event
        for event in first_party_failures
        if event.get("event_type") == "request_failed"
    ]
    if first_party_request_failures and not missing:
        findings.append(
            RuntimeFinding(
                "warning",
                "first_party_request_failed",
                "A first-party request failed, but critical content remained available.",
                category="network_error",
                count=sum(
                    event.get("count", 1)
                    for event in first_party_request_failures
                ),
            )
        )

    dialogs = _events(collector_snapshot, "dialog")
    if dialogs:
        findings.append(
            RuntimeFinding(
                "warning",
                "unexpected_dialog",
                "The page opened a browser dialog; it was dismissed.",
                category="unexpected_dialog",
                count=sum(event.get("count", 1) for event in dialogs),
            )
        )

    if health.get("loading_visible_count", 0):
        loading_blocks_critical = bool(
            missing and health.get("loading_critical_count", 0)
        )
        findings.append(
            RuntimeFinding(
                "error" if loading_blocks_critical else "warning",
                (
                    "infinite_loading"
                    if loading_blocks_critical
                    else "loading_indicator_visible"
                ),
                "A configured loading indicator remained visible after readiness.",
                category=(
                    "infinite_loading"
                    if loading_blocks_critical
                    else "loading_indicator_visible"
                ),
                count=health["loading_visible_count"],
            )
        )

    if health.get("broken_visible_image_count", 0):
        findings.append(
            RuntimeFinding(
                "warning",
                "broken_visible_image",
                "One or more visible images are broken.",
                category="broken_image",
                count=health["broken_visible_image_count"],
            )
        )

    attempts = navigation.get("attempts", [])
    failed_attempts = [item for item in attempts if item.get("error_type")]
    if navigation.get("error_type"):
        findings.append(
            RuntimeFinding(
                "error",
                "navigation_failed",
                "Navigation did not reach the expected ready state.",
                category="navigation_error",
                evidence={
                    "error_type": navigation.get("error_type"),
                    "error_message": navigation.get("error_message"),
                },
            )
        )
    elif failed_attempts:
        findings.append(
            RuntimeFinding(
                "warning",
                "navigation_retry_recovered",
                "Navigation recovered after one or more failed attempts.",
                category="navigation_error",
                count=len(failed_attempts),
            )
        )

    if collector_snapshot.get("collector_errors"):
        findings.append(
            RuntimeFinding(
                "warning",
                "runtime_collector_error",
                "Runtime collection degraded without interrupting the page check.",
                category="test_environment_error",
                count=len(collector_snapshot["collector_errors"]),
            )
        )

    findings = _dedupe_findings(findings)
    return findings


def runtime_status(findings):
    highest = max(
        (SEVERITY_ORDER.get(finding.severity, 0) for finding in findings),
        default=0,
    )
    if highest >= SEVERITY_ORDER["error"]:
        return "failed"
    if highest >= SEVERITY_ORDER["warning"]:
        return "warning"
    return "passed"


def runtime_score(findings):
    penalties = {
        "info": 0,
        "warning": 10,
        "error": 30,
        "critical": 50,
    }
    return max(
        0,
        100 - sum(penalties.get(finding.severity, 0) for finding in findings),
    )


def primary_finding(findings):
    if not findings:
        return None
    return max(
        enumerate(findings),
        key=lambda item: (SEVERITY_ORDER.get(item[1].severity, 0), -item[0]),
    )[1]


def _events(snapshot, event_type):
    return [
        event
        for event in snapshot.get("events", [])
        if event.get("event_type") == event_type
    ]


def _matched_error_page(text, config):
    configured = config.get("error_page_patterns", {})
    groups = {}
    for reason_code, patterns in DEFAULT_ERROR_PAGE_PATTERNS.items():
        groups[reason_code] = list(patterns)
        groups[reason_code].extend(configured.get(reason_code, []))

    for reason_code, patterns in groups.items():
        for pattern in patterns:
            if str(pattern).lower() in text:
                return {
                    "reason_code": reason_code,
                    "pattern": str(pattern),
                }
    return None


def _dedupe_findings(findings):
    selected = {}
    for finding in findings:
        existing = selected.get(finding.reason_code)
        if existing is None:
            selected[finding.reason_code] = finding
            continue
        existing.count += finding.count
        existing.evidence.update(finding.evidence)
    return list(selected.values())
