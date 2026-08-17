from playwright_checks.health.models import (
    EVIDENCE_RANK,
    AlertDecision,
    EvidenceLevel,
    FailureClassification,
    HealthStatus,
    Severity,
    highest_severity,
)


class FalsePositiveController:
    """Apply deterministic suppression and escalation policy to findings."""

    def __init__(self, config=None):
        self.config = dict(config or {})
        configured_level = str(
            self.config.get("minimum_critical_alert_evidence", "HIGH")
        ).upper()
        try:
            self.minimum_critical_evidence = EvidenceLevel(configured_level)
        except ValueError:
            self.minimum_critical_evidence = EvidenceLevel.HIGH

    def apply(self, finding):
        classification = finding.classification

        if classification == FailureClassification.THIRD_PARTY_NOISE and self.config.get(
            "suppress_third_party_noise",
            True,
        ):
            finding.alert_eligible = False
            finding.severity = min_severity(finding.severity, Severity.LOW)
            finding.status = HealthStatus.WARN
            finding.suppression_reason = "third_party_without_core_impact"
            return finding

        if classification in (
            FailureClassification.CONTENT_CHANGED,
            FailureClassification.EXPECTED_BUSINESS_STATE,
        ) and self.config.get("suppress_expected_change", True):
            finding.alert_eligible = False
            finding.status = HealthStatus.EXPECTED_CHANGE
            finding.severity = min_severity(finding.severity, Severity.INFO)
            finding.suppression_reason = "expected_or_operational_content_change"
            return finding

        if classification == FailureClassification.SELECTOR_CHANGED and self.config.get(
            "suppress_selector_change",
            True,
        ):
            finding.alert_eligible = False
            finding.status = HealthStatus.UNVERIFIED
            finding.severity = min_severity(finding.severity, Severity.MEDIUM)
            finding.suppression_reason = "selector_change_requires_review"
            return finding

        if classification in (
            FailureClassification.TEST_SCRIPT_ISSUE,
            FailureClassification.TEST_ENVIRONMENT_ISSUE,
        ):
            finding.alert_eligible = False
            finding.status = HealthStatus.UNVERIFIED
            finding.severity = min_severity(finding.severity, Severity.MEDIUM)
            finding.suppression_reason = "test_system_issue_not_site_incident"
            return finding

        if classification == FailureClassification.NETWORK_TRANSIENT:
            finding.alert_eligible = False
            if finding.reason_code in (
                "recovered_after_retry",
                "navigation_retry_recovered",
            ):
                configured_status = str(
                    self.config.get("recovered_retry_status", "FLAKY")
                ).upper()
                try:
                    finding.status = HealthStatus(configured_status)
                except ValueError:
                    finding.status = HealthStatus.FLAKY
            else:
                finding.status = HealthStatus.FLAKY
            finding.severity = min_severity(finding.severity, Severity.MEDIUM)
            finding.suppression_reason = "transient_requires_recurrence_or_history"
            return finding

        if classification in (
            FailureClassification.WAF_BLOCK,
            FailureClassification.RATE_LIMIT,
        ):
            finding.alert_eligible = False
            finding.status = HealthStatus.BLOCKED
            finding.severity = min_severity(finding.severity, Severity.MEDIUM)
            finding.suppression_reason = "monitor_blocked_not_confirmed_public_failure"
            return finding

        if classification == FailureClassification.LAYOUT_CHANGED:
            finding.severity = min_severity(finding.severity, Severity.MEDIUM)

        if finding.evidence_level == EvidenceLevel.NONE:
            finding.alert_eligible = False
            finding.suppression_reason = "no_deterministic_evidence"
            finding.severity = min_severity(finding.severity, Severity.MEDIUM)
            return finding

        if (
            finding.severity == Severity.CRITICAL
            and EVIDENCE_RANK[finding.evidence_level]
            < EVIDENCE_RANK[self.minimum_critical_evidence]
        ):
            finding.alert_eligible = False
            finding.suppression_reason = "critical_alert_requires_high_evidence"
            finding.severity = Severity.HIGH
            return finding

        finding.alert_eligible = bool(
            finding.status == HealthStatus.FAIL
            and finding.severity in (Severity.HIGH, Severity.CRITICAL)
            and finding.classification
            in (
                FailureClassification.SITE_DOWN,
                FailureClassification.REAL_SITE_FAILURE,
                FailureClassification.REAL_FUNCTIONAL_BUG,
                FailureClassification.REAL_UI_BUG,
            )
            and EVIDENCE_RANK[finding.evidence_level]
            >= EVIDENCE_RANK[EvidenceLevel.MEDIUM]
        )
        if not finding.alert_eligible and not finding.suppression_reason:
            finding.suppression_reason = "insufficient_severity_confidence_or_business_impact"
        return finding


def alert_decision(findings):
    actionable = [finding for finding in findings if finding.alert_eligible]
    if actionable:
        severity = highest_severity(finding.severity for finding in actionable)
        return AlertDecision(
            should_alert=True,
            alert_type="SITE_INCIDENT",
            severity=severity,
            finding_ids=[finding.finding_id for finding in actionable],
            reason="actionable_findings_with_deterministic_evidence",
        )

    blocked = [finding for finding in findings if finding.status == HealthStatus.BLOCKED]
    if blocked:
        return AlertDecision(
            should_alert=False,
            alert_type="MONITORING_BLOCKED",
            severity=Severity.MEDIUM,
            finding_ids=[finding.finding_id for finding in blocked],
            reason="blocked_scope_requires_retry_or_repeated_confirmation",
        )

    return AlertDecision(
        should_alert=False,
        alert_type="NONE",
        severity=Severity.NONE,
        finding_ids=[],
        reason="no_actionable_findings",
    )


def min_severity(left, right):
    order = {
        Severity.NONE: 0,
        Severity.INFO: 1,
        Severity.LOW: 2,
        Severity.MEDIUM: 3,
        Severity.HIGH: 4,
        Severity.CRITICAL: 5,
    }
    return left if order[left] <= order[right] else right
