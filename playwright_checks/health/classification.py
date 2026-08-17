import hashlib
import json

from playwright_checks.health.evidence import evidence_level
from playwright_checks.health.models import (
    EvidenceLevel,
    FailureClassification,
    HealthDimension,
    HealthFinding,
    HealthStatus,
    PageType,
    Severity,
)


RUNTIME_CLASSIFICATIONS = {
    "access_denied": FailureClassification.WAF_BLOCK,
    "security_challenge": FailureClassification.WAF_BLOCK,
    "rate_limited": FailureClassification.RATE_LIMIT,
    # A single monitor-side network failure is not enough evidence to call the
    # public site down. History or a second vantage point can promote it later.
    "network_error": FailureClassification.NETWORK_TRANSIENT,
    "first_party_server_error": FailureClassification.REAL_SITE_FAILURE,
    "blank_page": FailureClassification.SITE_DOWN,
    "application_error": FailureClassification.REAL_SITE_FAILURE,
    "page_crashed": FailureClassification.REAL_SITE_FAILURE,
    "partial_render_failure": FailureClassification.REAL_UI_BUG,
    "missing_critical_component": FailureClassification.UNKNOWN,
    "infinite_loading": FailureClassification.REAL_UI_BUG,
    "loading_indicator_visible": FailureClassification.UNKNOWN,
    "broken_visible_image": FailureClassification.REAL_UI_BUG,
    "page_error": FailureClassification.UNKNOWN,
    "console_error": FailureClassification.UNKNOWN,
    "console_warning": FailureClassification.UNKNOWN,
    "third_party_error": FailureClassification.THIRD_PARTY_NOISE,
    "first_party_request_failed": FailureClassification.NETWORK_TRANSIENT,
    "navigation_failed": FailureClassification.NETWORK_TRANSIENT,
    "navigation_retry_recovered": FailureClassification.NETWORK_TRANSIENT,
    "unexpected_dialog": FailureClassification.REAL_UI_BUG,
    "runtime_collector_error": FailureClassification.TEST_ENVIRONMENT_ISSUE,
    "runtime_evidence_write_failed": FailureClassification.TEST_ENVIRONMENT_ISSUE,
    "runtime_finalize_failed": FailureClassification.TEST_ENVIRONMENT_ISSUE,
    "automation_runtime_error": FailureClassification.TEST_SCRIPT_ISSUE,
    "missing_optional_component": FailureClassification.EXPECTED_BUSINESS_STATE,
}


RUNTIME_DIMENSIONS = {
    "access_denied": HealthDimension.AVAILABILITY,
    "security_challenge": HealthDimension.AVAILABILITY,
    "rate_limited": HealthDimension.AVAILABILITY,
    "network_error": HealthDimension.AVAILABILITY,
    "blank_page": HealthDimension.AVAILABILITY,
    "application_error": HealthDimension.AVAILABILITY,
    "navigation_failed": HealthDimension.AVAILABILITY,
    "navigation_retry_recovered": HealthDimension.AVAILABILITY,
    "first_party_server_error": HealthDimension.NETWORK,
    "first_party_request_failed": HealthDimension.NETWORK,
    "third_party_error": HealthDimension.NETWORK,
    "partial_render_failure": HealthDimension.DOM_CONTENT,
    "missing_critical_component": HealthDimension.DOM_CONTENT,
    "missing_optional_component": HealthDimension.DOM_CONTENT,
    "infinite_loading": HealthDimension.DOM_CONTENT,
    "loading_indicator_visible": HealthDimension.DOM_CONTENT,
    "broken_visible_image": HealthDimension.DOM_CONTENT,
    "page_crashed": HealthDimension.RUNTIME,
    "page_error": HealthDimension.RUNTIME,
    "console_error": HealthDimension.RUNTIME,
    "console_warning": HealthDimension.RUNTIME,
    "unexpected_dialog": HealthDimension.RUNTIME,
    "runtime_collector_error": HealthDimension.TEST_SYSTEM,
    "runtime_evidence_write_failed": HealthDimension.TEST_SYSTEM,
    "runtime_finalize_failed": HealthDimension.TEST_SYSTEM,
    "automation_runtime_error": HealthDimension.TEST_SYSTEM,
}


RUNTIME_SEVERITIES = {
    "critical": Severity.CRITICAL,
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "info": Severity.LOW,
}


def classify_runtime_finding(
    raw,
    page_summary,
    attempt,
    evidence_builder,
    page_type,
    source_result_id,
):
    reason = str(raw.get("reason_code") or "unknown")
    classification = RUNTIME_CLASSIFICATIONS.get(
        reason,
        FailureClassification.UNKNOWN,
    )
    dimension = RUNTIME_DIMENSIONS.get(reason, HealthDimension.RUNTIME)
    evidence = evidence_builder.for_runtime_finding(raw, page_summary, attempt)
    level = evidence_level(evidence)
    severity = RUNTIME_SEVERITIES.get(
        str(raw.get("severity") or "warning").lower(),
        Severity.MEDIUM,
    )
    status = _runtime_status(classification, severity, reason)
    confidence = _confidence(classification, level)
    page = str(page_summary.get("page") or "unknown")
    viewport = str(page_summary.get("viewport") or "unknown")
    site = str(page_summary.get("site") or "unknown")
    return HealthFinding(
        finding_id=_finding_id(site, viewport, page, reason, classification.value),
        site=site,
        page=page,
        page_type=page_type,
        viewport=viewport,
        dimension=dimension,
        status=status,
        severity=severity,
        classification=classification,
        reason_code=reason,
        title=_title(reason, classification),
        summary=str(raw.get("message") or _title(reason, classification)),
        business_impact=_business_impact(page_type, classification, reason),
        confidence=confidence,
        evidence_level=level,
        evidence=evidence,
        recommendation=_recommendation(classification, reason),
        source_result_ids=[source_result_id],
        timestamp=str(raw.get("timestamp") or page_summary.get("timestamp") or ""),
    )


def classify_result(
    result,
    attempt,
    evidence_builder,
    page_type,
    source_result_id,
):
    status_value = str(result.get("status") or "").lower()
    if status_value not in ("failed", "warning", "content_changed"):
        return None

    result_type = result.get("result_type", "visual")
    case = str(result.get("case") or "unknown")
    evidence = evidence_builder.for_result(result, attempt)
    level = evidence_level(evidence)

    if status_value == "content_changed":
        classification = FailureClassification.CONTENT_CHANGED
        health_status = HealthStatus.EXPECTED_CHANGE
        severity = Severity.INFO
        dimension = HealthDimension.DOM_CONTENT
        reason = "content_changed"
    elif result_type == "deterministic_check":
        classification, dimension, severity = _classify_observation(case, result)
        health_status = (
            HealthStatus.UNVERIFIED
            if classification in (
                FailureClassification.SELECTOR_CHANGED,
                FailureClassification.TEST_SCRIPT_ISSUE,
                FailureClassification.TEST_ENVIRONMENT_ISSUE,
            )
            else HealthStatus.FAIL
        )
        reason = case
    elif case == "runtime":
        classification = FailureClassification.TEST_SCRIPT_ISSUE
        health_status = HealthStatus.UNVERIFIED
        severity = Severity.MEDIUM
        dimension = HealthDimension.TEST_SYSTEM
        reason = "visual_execution_error"
    else:
        classification, dimension = _classify_visual(case, result)
        health_status = HealthStatus.FAIL if status_value == "failed" else HealthStatus.WARN
        severity = Severity.HIGH if status_value == "failed" else Severity.LOW
        if not result.get("affects_exit_code", True):
            severity = Severity.MEDIUM if status_value == "failed" else Severity.LOW
        reason = (
            "visual_structure_failure"
            if result.get("structural_status") == "failed"
            else "visual_difference"
        )

    site = str(result.get("site") or "unknown")
    page = str(result.get("page") or "unknown")
    viewport = str(result.get("viewport") or "unknown")
    messages = result.get("messages") or []
    error = result.get("error")
    summary = (
        str(error)
        if error
        else "; ".join(str(value) for value in messages[:5])
        if messages
        else _title(reason, classification)
    )
    return HealthFinding(
        finding_id=_finding_id(site, viewport, page, case, classification.value),
        site=site,
        page=page,
        page_type=page_type,
        viewport=viewport,
        dimension=dimension,
        status=health_status,
        severity=severity,
        classification=classification,
        reason_code=reason,
        title=_title(case, classification),
        summary=summary,
        business_impact=_business_impact(page_type, classification, case),
        confidence=_confidence(classification, level),
        evidence_level=level,
        evidence=evidence,
        recommendation=_recommendation(classification, case),
        source_result_ids=[source_result_id],
    )


def recovered_retry_finding(
    page_summary,
    page_type,
    source_result_id,
    evidence_builder=None,
    attempt=None,
):
    if not page_summary.get("recovered_after_retry"):
        return None
    site = str(page_summary.get("site") or "unknown")
    page = str(page_summary.get("page") or "unknown")
    viewport = str(page_summary.get("viewport") or "unknown")
    reason = "recovered_after_retry"
    evidence = (
        evidence_builder.for_recovered_retry(page_summary, attempt)
        if evidence_builder is not None
        else []
    )
    return HealthFinding(
        finding_id=_finding_id(site, viewport, page, reason, "NETWORK_TRANSIENT"),
        site=site,
        page=page,
        page_type=page_type,
        viewport=viewport,
        dimension=HealthDimension.AVAILABILITY,
        status=HealthStatus.FLAKY,
        severity=Severity.MEDIUM,
        classification=FailureClassification.NETWORK_TRANSIENT,
        reason_code=reason,
        title="Check recovered after retry",
        summary=(
            f"The scope recovered after {page_summary.get('retry_count', 1)} retry attempt(s)."
        ),
        business_impact="No confirmed user impact; monitor recurrence.",
        confidence=0.9,
        evidence_level=evidence_level(evidence),
        evidence=evidence,
        recommendation="Observe recurrence and compare the failed attempt evidence before escalating.",
        source_result_ids=[source_result_id],
    )


def _runtime_status(classification, severity, reason):
    if classification in (
        FailureClassification.WAF_BLOCK,
        FailureClassification.RATE_LIMIT,
    ):
        return HealthStatus.BLOCKED
    if classification in (
        FailureClassification.TEST_SCRIPT_ISSUE,
        FailureClassification.TEST_ENVIRONMENT_ISSUE,
    ):
        return HealthStatus.UNVERIFIED
    if classification == FailureClassification.EXPECTED_BUSINESS_STATE:
        return HealthStatus.EXPECTED_CHANGE
    if reason == "navigation_retry_recovered":
        return HealthStatus.FLAKY
    if severity in (Severity.CRITICAL, Severity.HIGH):
        return HealthStatus.FAIL
    return HealthStatus.WARN


def _classify_observation(case, result):
    normalized = case.lower()
    messages = " ".join(result.get("messages") or []).lower()
    if "plugin" in normalized:
        return (
            FailureClassification.THIRD_PARTY_NOISE,
            HealthDimension.FUNCTIONAL,
            Severity.LOW,
        )
    if any(value in normalized for value in ("add_to_cart", "variant", "pagination", "interaction")):
        return (
            FailureClassification.REAL_FUNCTIONAL_BUG,
            HealthDimension.FUNCTIONAL,
            Severity.HIGH,
        )
    if "product_content" in normalized or "product_count" in normalized:
        return (
            FailureClassification.REAL_UI_BUG,
            HealthDimension.DOM_CONTENT,
            Severity.HIGH,
        )
    if "dom" in normalized and any(
        marker in messages for marker in ("not found", "timeout", "selector", "dom error")
    ):
        return (
            FailureClassification.SELECTOR_CHANGED,
            HealthDimension.TEST_SYSTEM,
            Severity.MEDIUM,
        )
    return (
        FailureClassification.UNKNOWN,
        HealthDimension.PAGE,
        Severity.MEDIUM,
    )


def _classify_visual(case, result):
    issues = set(result.get("structural_issues") or [])
    interaction_markers = (
        "add_to_cart",
        "variant",
        "filter",
        "menu",
        "pagination",
        "purchase",
    )
    if issues and any(
        any(marker in str(issue) for marker in interaction_markers)
        for issue in issues
    ):
        return FailureClassification.REAL_FUNCTIONAL_BUG, HealthDimension.FUNCTIONAL
    if issues:
        return FailureClassification.REAL_UI_BUG, HealthDimension.DOM_CONTENT
    if result.get("error") and "capture failed" in str(result.get("error")).lower():
        return FailureClassification.TEST_SCRIPT_ISSUE, HealthDimension.TEST_SYSTEM
    return FailureClassification.LAYOUT_CHANGED, HealthDimension.VISUAL


def _confidence(classification, level):
    base = {
        EvidenceLevel.HIGH: 0.95,
        EvidenceLevel.MEDIUM: 0.78,
        EvidenceLevel.LOW: 0.55,
        EvidenceLevel.NONE: 0.25,
    }[level]
    if classification in (
        FailureClassification.UNKNOWN,
        FailureClassification.SELECTOR_CHANGED,
    ):
        return min(base, 0.65)
    return base


def _title(reason, classification):
    readable = str(reason or classification.value).replace("_", " ").strip()
    return readable[:1].upper() + readable[1:]


def _business_impact(page_type, classification, reason):
    if classification in (
        FailureClassification.SITE_DOWN,
        FailureClassification.REAL_SITE_FAILURE,
    ):
        if page_type == PageType.HOME:
            return "Potentially affects all storefront visitors."
        if page_type == PageType.PDP:
            return "Potentially blocks product evaluation and purchase entry."
        return "Potentially blocks users of this page type."
    if classification == FailureClassification.REAL_FUNCTIONAL_BUG:
        if "add_to_cart" in str(reason) or "variant" in str(reason):
            return "May block or degrade the core purchase flow."
        return "May block a configured user interaction."
    if classification in (
        FailureClassification.WAF_BLOCK,
        FailureClassification.RATE_LIMIT,
    ):
        return "The monitor could not verify user health; this is not proof of a public outage."
    if classification == FailureClassification.THIRD_PARTY_NOISE:
        return "No core-flow impact has been demonstrated."
    if classification in (
        FailureClassification.TEST_SCRIPT_ISSUE,
        FailureClassification.TEST_ENVIRONMENT_ISSUE,
        FailureClassification.SELECTOR_CHANGED,
    ):
        return "Health is unverified until the monitoring system is corrected or reviewed."
    return "User impact is not yet confirmed by deterministic evidence."


def _recommendation(classification, reason):
    recommendations = {
        FailureClassification.SITE_DOWN: "Verify from a second network vantage point, then escalate to site operations if confirmed.",
        FailureClassification.REAL_SITE_FAILURE: "Ask development or operations to reproduce using the attached HTTP and runtime evidence.",
        FailureClassification.WAF_BLOCK: "Review WAF allowlisting and monitor identity; do not label this as a public outage.",
        FailureClassification.RATE_LIMIT: "Reduce concurrency or request rate and retry after the server-provided interval.",
        FailureClassification.SELECTOR_CHANGED: "Review the live DOM and approve a selector update only after confirming the user feature still works.",
        FailureClassification.CONTENT_CHANGED: "Treat as expected business content unless a reviewed rule says the change is abnormal.",
        FailureClassification.LAYOUT_CHANGED: "QA should compare the current image and diff against the reviewed baseline.",
        FailureClassification.THIRD_PARTY_NOISE: "Observe or suppress the third-party source unless core capability evidence shows impact.",
        FailureClassification.NETWORK_TRANSIENT: "Retry briefly and use recurrence/history before escalating.",
        FailureClassification.TEST_SCRIPT_ISSUE: "Repair the deterministic test or evidence collector before judging site health.",
        FailureClassification.TEST_ENVIRONMENT_ISSUE: "Restore the monitoring environment and rerun this scope.",
        FailureClassification.REAL_FUNCTIONAL_BUG: "QA should reproduce the affected capability and escalate with the attached evidence.",
        FailureClassification.REAL_UI_BUG: "QA should verify the affected page and viewport, then route to the owning frontend team.",
    }
    return recommendations.get(
        classification,
        "Review the evidence and obtain a second deterministic signal before escalating.",
    )


def _finding_id(site, viewport, page, reason, classification):
    encoded = json.dumps(
        [site, viewport, page, reason, classification],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "finding-" + hashlib.sha256(encoded).hexdigest()[:12]
