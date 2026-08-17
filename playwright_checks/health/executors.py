from time import perf_counter
from urllib.parse import urlsplit

from playwright_checks.health.execution_models import (
    CheckResult,
    ExecutionStatus,
    check_result_id,
    evidence_item,
)
from playwright_checks.health.models import (
    EvidenceLevel,
    EvidenceType,
    HealthStatus,
    SideEffectLevel,
    utc_timestamp,
)
from playwright_checks.utils.waits import selector_for


EXECUTOR_VERSION = "1.0"


class ExecutorInputError(ValueError):
    pass


def dom_element_presence(context):
    started_at, started = utc_timestamp(), perf_counter()
    selector, locator = _locator(context)
    count = int(locator.count())
    passed = count > 0
    evidence = [
        _selector_evidence(context, selector),
        evidence_item(
            context,
            EvidenceType.DOM,
            EvidenceLevel.MEDIUM,
            "dom.element_presence",
            "Measured matching DOM element count",
            details={"selector": selector, "count": count},
        ),
    ]
    return _completed(
        context,
        "dom.element_presence",
        started_at,
        started,
        expected={"minimum_count": 1},
        actual={"count": count, "present": passed},
        passed=passed,
        evidence=evidence,
    )


def dom_element_visible(context):
    started_at, started = utc_timestamp(), perf_counter()
    selector, locator = _locator(context)
    count = int(locator.count())
    visible_locator = _visible_locator(locator)
    visible_count = int(visible_locator.count()) if count else 0
    visible = visible_count > 0
    evidence = [
        _selector_evidence(context, selector),
        evidence_item(
            context,
            EvidenceType.DOM,
            EvidenceLevel.MEDIUM,
            "dom.element_visible",
            "Measured DOM visibility",
            details={
                "selector": selector,
                "count": count,
                "visible_count": visible_count,
                "visible": visible,
            },
        ),
    ]
    return _completed(
        context,
        "dom.element_visible",
        started_at,
        started,
        expected={"visible": True},
        actual={
            "count": count,
            "visible_count": visible_count,
            "visible": visible,
        },
        passed=visible,
        evidence=evidence,
    )


def dom_element_enabled(context):
    started_at, started = utc_timestamp(), perf_counter()
    selector, locator = _locator(context)
    count = int(locator.count())
    visible_locator = _visible_locator(locator)
    visible_count = int(visible_locator.count()) if count else 0
    enabled = (
        bool(visible_locator.first.is_enabled()) if visible_count else False
    )
    evidence = [
        _selector_evidence(context, selector),
        evidence_item(
            context,
            EvidenceType.DOM,
            EvidenceLevel.MEDIUM,
            "dom.element_enabled",
            "Measured DOM enabled state",
            details={
                "selector": selector,
                "count": count,
                "visible_count": visible_count,
                "enabled": enabled,
            },
        ),
    ]
    return _completed(
        context,
        "dom.element_enabled",
        started_at,
        started,
        expected={"enabled": True},
        actual={
            "count": count,
            "visible_count": visible_count,
            "enabled": enabled,
        },
        passed=enabled,
        evidence=evidence,
    )


def dom_element_count(context):
    started_at, started = utc_timestamp(), perf_counter()
    selector, locator = _locator(context)
    expected_minimum = _positive_int(
        context.metadata.get("expected_minimum_count", 1),
        "expected_minimum_count",
    )
    count = int(locator.count())
    passed = count >= expected_minimum
    evidence = [
        _selector_evidence(context, selector),
        evidence_item(
            context,
            EvidenceType.DOM,
            EvidenceLevel.MEDIUM,
            "dom.element_count",
            "Counted matching DOM elements",
            details={"selector": selector, "count": count},
        ),
        evidence_item(
            context,
            EvidenceType.METRIC,
            EvidenceLevel.MEDIUM,
            "dom.element_count",
            "Compared DOM count with required minimum",
            details={
                "expected_minimum_count": expected_minimum,
                "actual_count": count,
            },
        ),
    ]
    return _completed(
        context,
        "dom.element_count",
        started_at,
        started,
        expected={"minimum_count": expected_minimum},
        actual={"count": count},
        passed=passed,
        evidence=evidence,
    )


def dom_multiple_signal_presence(context):
    """Measure a configured set of DOM signals without site-specific code."""
    started_at, started = utc_timestamp(), perf_counter()
    context.validate()
    hint = _structured_hint(context.selector_hint, "multiple_signals")
    signals = hint.get("signals")
    if not isinstance(signals, dict) or not signals:
        raise ExecutorInputError("multiple_signals requires non-empty signals")
    mode = str(hint.get("mode") or "visible").strip().lower()
    if mode not in ("attached", "visible"):
        raise ExecutorInputError(
            "multiple_signals mode must be attached or visible"
        )

    measurements = []
    resolved = {}
    for name, raw_selector in signals.items():
        selector = _selector_text(raw_selector)
        resolved[str(name)] = selector
        locator = context.page.locator(selector)
        count = int(locator.count())
        visible_count = None
        layout_box = None
        signal_passed = count > 0
        if mode == "visible":
            visible_locator = _visible_locator(locator)
            visible_count = int(visible_locator.count()) if count else 0
            signal_passed = visible_count > 0
            bounding_box = getattr(visible_locator.first, "bounding_box", None)
            if signal_passed and callable(bounding_box):
                layout_box = bounding_box(timeout=context.timeout_ms)
                signal_passed = bool(
                    layout_box
                    and float(layout_box.get("width", 0)) > 0
                    and float(layout_box.get("height", 0)) > 0
                )
        measurements.append(
            {
                "name": str(name),
                "selector": selector,
                "count": count,
                "visible_count": visible_count,
                "layout_box": layout_box,
                "passed": signal_passed,
            }
        )

    passed_count = sum(1 for value in measurements if value["passed"])
    passed = passed_count == len(measurements)
    evidence = [
        evidence_item(
            context,
            EvidenceType.SELECTOR,
            EvidenceLevel.MEDIUM,
            "dom.multiple_signal_presence",
            "Resolved configured DOM signal targets",
            details={"mode": mode, "selectors": resolved},
        ),
        evidence_item(
            context,
            EvidenceType.DOM,
            EvidenceLevel.MEDIUM,
            "dom.multiple_signal_presence",
            "Measured every configured DOM signal",
            details={"mode": mode, "signals": measurements},
        ),
        evidence_item(
            context,
            EvidenceType.METRIC,
            EvidenceLevel.MEDIUM,
            "dom.multiple_signal_presence",
            "Compared healthy signal count with configured total",
            details={
                "passed_count": passed_count,
                "signal_count": len(measurements),
            },
        ),
    ]
    return _completed(
        context,
        "dom.multiple_signal_presence",
        started_at,
        started,
        expected={"all_signals_healthy": True, "mode": mode},
        actual={
            "passed_count": passed_count,
            "signal_count": len(measurements),
            "signals": measurements,
        },
        passed=passed,
        evidence=evidence,
    )


def dom_descendant_presence(context):
    """Sample repeated roots and require a descendant in a configured ratio."""
    started_at, started = utc_timestamp(), perf_counter()
    context.validate()
    hint = _structured_hint(context.selector_hint, "descendant_presence")
    root_selector = _selector_text(hint.get("root"))
    descendant = str(hint.get("descendant") or "").strip()
    if not descendant:
        raise ExecutorInputError(
            "descendant_presence requires descendant selector text"
        )
    sample_limit = _positive_int(hint.get("sample_limit", 12), "sample_limit")
    try:
        required_ratio = float(hint.get("required_ratio", 1.0))
    except (TypeError, ValueError) as error:
        raise ExecutorInputError(
            "required_ratio must be between 0 and 1"
        ) from error
    if not 0 < required_ratio <= 1:
        raise ExecutorInputError("required_ratio must be between 0 and 1")

    roots = _visible_locator(context.page.locator(root_selector))
    root_count = int(roots.count())
    sample_count = min(root_count, sample_limit)
    matches = []
    for index in range(sample_count):
        descendants = _visible_locator(roots.nth(index).locator(descendant))
        count = int(descendants.count())
        matches.append({"index": index, "visible_descendant_count": count})
    matched_count = sum(
        1 for measurement in matches
        if measurement["visible_descendant_count"] > 0
    )
    actual_ratio = matched_count / sample_count if sample_count else 0.0
    passed = bool(sample_count and actual_ratio >= required_ratio)
    evidence = [
        evidence_item(
            context,
            EvidenceType.SELECTOR,
            EvidenceLevel.MEDIUM,
            "dom.descendant_presence",
            "Resolved repeated root and descendant selectors",
            details={"root": root_selector, "descendant": descendant},
        ),
        evidence_item(
            context,
            EvidenceType.DOM,
            EvidenceLevel.MEDIUM,
            "dom.descendant_presence",
            "Sampled visible descendants inside repeated DOM roots",
            details={"roots": root_count, "samples": matches},
        ),
        evidence_item(
            context,
            EvidenceType.METRIC,
            EvidenceLevel.MEDIUM,
            "dom.descendant_presence",
            "Compared descendant match ratio with required ratio",
            details={
                "sample_count": sample_count,
                "matched_count": matched_count,
                "actual_ratio": round(actual_ratio, 4),
                "required_ratio": required_ratio,
            },
        ),
    ]
    return _completed(
        context,
        "dom.descendant_presence",
        started_at,
        started,
        expected={
            "minimum_root_count": 1,
            "required_ratio": required_ratio,
        },
        actual={
            "root_count": root_count,
            "sample_count": sample_count,
            "matched_count": matched_count,
            "match_ratio": round(actual_ratio, 4),
        },
        passed=passed,
        evidence=evidence,
    )


def dom_control_state(context):
    """Observe control readiness. This executor never dispatches a click."""
    started_at, started = utc_timestamp(), perf_counter()
    selector, locator = _locator(context)
    count = int(locator.count())
    visible_locator = _visible_locator(locator)
    visible_count = int(visible_locator.count()) if count else 0
    enabled = False
    text_value = ""
    aria_busy = None
    aria_disabled = None
    disabled_attribute = None
    class_value = ""
    if visible_count:
        control = visible_locator.first
        enabled = bool(control.is_enabled())
        text_value = str(control.inner_text(timeout=context.timeout_ms) or "").strip()
        get_attribute = getattr(control, "get_attribute", None)
        if callable(get_attribute):
            aria_busy = get_attribute("aria-busy")
            aria_disabled = get_attribute("aria-disabled")
            disabled_attribute = get_attribute("disabled")
            class_value = str(get_attribute("class") or "").lower()
            if not text_value:
                text_value = str(
                    get_attribute("aria-label")
                    or get_attribute("value")
                    or ""
                ).strip()
    loading = any(
        marker in class_value
        for marker in ("loading", "is-loading", "loader", "processing")
    )
    busy = str(aria_busy or "").lower() == "true"
    disabled = bool(
        not enabled
        or disabled_attribute is not None
        or str(aria_disabled or "").lower() == "true"
    )
    passed = bool(
        visible_count
        and enabled
        and text_value
        and not busy
        and not loading
        and not disabled
    )
    state = {
        "count": count,
        "visible_count": visible_count,
        "visible": visible_count > 0,
        "enabled": enabled,
        "disabled": disabled,
        "text_present": bool(text_value),
        "text_length": len(text_value),
        "busy": busy,
        "loading": loading,
        "click_dispatched": False,
    }
    evidence = [
        _selector_evidence(context, selector),
        evidence_item(
            context,
            EvidenceType.DOM,
            EvidenceLevel.MEDIUM,
            "dom.control_state",
            "Observed control presence, visibility, text and readiness",
            details={"selector": selector, **state},
        ),
        evidence_item(
            context,
            EvidenceType.METRIC,
            EvidenceLevel.MEDIUM,
            "dom.control_state",
            "Recorded observation-only control health",
            details={"ready": passed, "click_dispatched": False},
        ),
    ]
    return _completed(
        context,
        "dom.control_state",
        started_at,
        started,
        expected={
            "visible": True,
            "enabled": True,
            "text_present": True,
            "busy": False,
            "loading": False,
            "click_dispatched": False,
        },
        actual=state,
        passed=passed,
        evidence=evidence,
    )


def content_text_present(context):
    started_at, started = utc_timestamp(), perf_counter()
    selector, locator = _locator(context)
    count = int(locator.count())
    visible_locator = _visible_locator(locator)
    visible_count = int(visible_locator.count()) if count else 0
    text = ""
    visible = visible_count > 0
    if visible:
        text = str(
            visible_locator.first.inner_text(timeout=context.timeout_ms) or ""
        ).strip()
    expected_text = str(context.metadata.get("expected_text") or "").strip()
    passed = bool(visible and text)
    if expected_text:
        passed = passed and expected_text.casefold() in text.casefold()
    evidence = [
        _selector_evidence(context, selector),
        evidence_item(
            context,
            EvidenceType.DOM,
            EvidenceLevel.MEDIUM,
            "content.text_present",
            "Read visible text content",
            details={
                "selector": selector,
                "count": count,
                "visible_count": visible_count,
                "visible": visible,
                "text_present": bool(text),
                "text_length": len(text),
            },
        ),
    ]
    return _completed(
        context,
        "content.text_present",
        started_at,
        started,
        expected={
            "visible": True,
            "non_empty": True,
            "contains": expected_text or None,
        },
        actual={
            "count": count,
            "visible_count": visible_count,
            "visible": visible,
            "text_present": bool(text),
            "text_length": len(text),
        },
        passed=passed,
        evidence=evidence,
    )


def navigation_url_reachable(context):
    started_at, started = utc_timestamp(), perf_counter()
    current_url = str(getattr(context.page, "url", "") or "")
    parsed = urlsplit(current_url)
    valid_url = parsed.scheme in ("http", "https") and bool(parsed.netloc)
    status = context.metadata.get("navigation_status")
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    reachable = bool(valid_url and (status is None or 200 <= status < 400))
    evidence = [
        evidence_item(
            context,
            EvidenceType.URL,
            EvidenceLevel.MEDIUM,
            "navigation.url_reachable",
            "Observed final page URL",
            details={"url": current_url, "valid_http_url": valid_url},
        )
    ]
    if status is not None:
        evidence.append(
            evidence_item(
                context,
                EvidenceType.HTTP,
                EvidenceLevel.HIGH if status >= 400 else EvidenceLevel.MEDIUM,
                "navigation.url_reachable",
                f"Observed main document HTTP {status}",
                details={"status": status, "url": current_url},
            )
        )
    return _completed(
        context,
        "navigation.url_reachable",
        started_at,
        started,
        expected={"http_status": "200-399", "valid_http_url": True},
        actual={
            "url": current_url,
            "http_status": status,
            "valid_http_url": valid_url,
        },
        passed=reachable,
        evidence=evidence,
    )


def interaction_safe_click(context):
    started_at, started = utc_timestamp(), perf_counter()
    if context.planned_check.interaction_policy != SideEffectLevel.SAFE:
        raise ExecutorInputError(
            "interaction.safe_click only accepts SAFE planned checks"
        )
    selector, locator = _locator(context)
    count = int(locator.count())
    visible_locator = _visible_locator(locator)
    visible_count = int(visible_locator.count()) if count else 0
    actionable = False
    if visible_count:
        visible_locator.first.click(trial=True, timeout=context.timeout_ms)
        actionable = True
    evidence = [
        _selector_evidence(context, selector),
        evidence_item(
            context,
            EvidenceType.DOM,
            EvidenceLevel.MEDIUM,
            "interaction.safe_click",
            "Performed Playwright trial click without dispatching a click",
            details={
                "selector": selector,
                "count": count,
                "visible_count": visible_count,
                "trial": True,
                "actionable": actionable,
            },
        ),
    ]
    return _completed(
        context,
        "interaction.safe_click",
        started_at,
        started,
        expected={"actionable": True, "trial_only": True},
        actual={
            "count": count,
            "visible_count": visible_count,
            "actionable": actionable,
            "trial_only": True,
        },
        passed=actionable,
        evidence=evidence,
    )


def _locator(context):
    context.validate()
    selector = _selector_text(context.selector_hint)
    return selector, context.page.locator(selector)


def _visible_locator(locator):
    """Return the visible subset so selector unions do not prefer hidden nodes."""
    return locator.filter(visible=True)


def _selector_text(value):
    if isinstance(value, dict) and "selector" in value:
        return _selector_text(value.get("selector"))
    if isinstance(value, str) and value.strip():
        return value.strip()
    if (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(item, str) and item.strip() for item in value)
    ):
        return selector_for(tuple(value))
    raise ExecutorInputError("selector_hint is missing or invalid")


def _structured_hint(value, expected_kind):
    if not isinstance(value, dict):
        raise ExecutorInputError(
            f"selector_hint must be a {expected_kind} mapping"
        )
    kind = str(value.get("kind") or "").strip()
    if kind != expected_kind:
        raise ExecutorInputError(
            f"selector_hint kind must be {expected_kind}"
        )
    return value


def _selector_evidence(context, selector):
    return evidence_item(
        context,
        EvidenceType.SELECTOR,
        EvidenceLevel.MEDIUM,
        "executor_context",
        "Resolved configured selector target",
        details={"target": context.target, "selector": selector},
    )


def _completed(
    context,
    executor_key,
    started_at,
    started,
    expected,
    actual,
    passed,
    evidence,
):
    health_status = HealthStatus.PASS if passed else HealthStatus.FAIL
    result = CheckResult(
        result_id=check_result_id(context, executor_key),
        check_id=context.planned_check.check_id,
        site_id=context.site_profile.site_identity.site_id,
        page_id=context.page_profile.page_id,
        page_type=context.page_profile.page_type,
        page_url=context.page_profile.url,
        capability=context.planned_check.capability,
        executor_key=executor_key,
        executor_version=EXECUTOR_VERSION,
        execution_status=ExecutionStatus.COMPLETED,
        health_status=health_status,
        expected=expected,
        actual=actual,
        observations=[
            {
                "kind": "executor_measurement",
                "target": context.target,
                "passed": bool(passed),
            }
        ],
        evidence=list(evidence),
        started_at=started_at,
        duration_ms=round((perf_counter() - started) * 1000, 3),
        retry_count=0,
        metadata={
            "run_id": context.run_id,
            "shadow_only": context.runtime_policy.shadow_only,
            "runtime_mode": context.runtime_policy.mode.value,
            "viewport": context.metadata.get("viewport", "unknown"),
            "target": context.target,
        },
    )
    return result.validate()


def _positive_int(value, name):
    if isinstance(value, bool):
        raise ExecutorInputError(f"{name} must be a positive integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ExecutorInputError(f"{name} must be a positive integer") from error
    if normalized <= 0:
        raise ExecutorInputError(f"{name} must be a positive integer")
    return normalized
