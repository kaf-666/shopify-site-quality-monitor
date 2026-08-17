import hashlib
import json
from collections import Counter, defaultdict

from playwright_checks.core.config_loader import get_page_config, load_site_config
from playwright_checks.core.paths import current_run_id
from playwright_checks.health.ai import analyze_on_demand
from playwright_checks.health.capabilities import ConfigCapabilityDetector
from playwright_checks.health.classification import (
    classify_result,
    classify_runtime_finding,
    recovered_retry_finding,
)
from playwright_checks.health.config import get_health_check_config
from playwright_checks.health.evidence import HealthEvidenceBuilder
from playwright_checks.health.false_positives import (
    FalsePositiveController,
    alert_decision,
)
from playwright_checks.health.models import (
    AIAnalysis,
    HealthDimension,
    HealthRunReport,
    HealthStatus,
    OverallHealth,
    PageHealth,
    Severity,
    STATUS_RANK,
    highest_severity,
    utc_timestamp,
    worst_status,
)


class HealthEngine:
    """Aggregate existing deterministic observations into health semantics."""

    def __init__(
        self,
        results,
        site_config=None,
        config=None,
        ai_analyzer=None,
        project_root=None,
    ):
        self.results = [dict(item) for item in (results or []) if isinstance(item, dict)]
        self.site = _first_value(self.results, "site") or (
            (site_config or {}).get("site") if isinstance(site_config, dict) else None
        ) or "unknown"
        self.site_config = site_config or self._load_site_config(self.site)
        self.config = config or get_health_check_config(self.site_config)
        self.ai_analyzer = ai_analyzer
        self.capability_detector = ConfigCapabilityDetector()
        self.evidence_builder = HealthEvidenceBuilder(
            project_root=project_root
        ) if project_root else HealthEvidenceBuilder()
        self.false_positive_controller = FalsePositiveController(
            self.config.get("false_positive_control", {})
        )

    def build(self):
        run_id = _first_value(self.results, "run_id") or current_run_id()
        grouped = defaultdict(list)
        for result in self.results:
            key = (
                str(result.get("site") or self.site),
                str(result.get("viewport") or "unknown"),
                str(result.get("page") or "unknown"),
            )
            grouped[key].append(result)

        pages = []
        all_findings = []
        for (site, viewport, page_name), scope_results in sorted(grouped.items()):
            page_summary = _latest_page_summary(scope_results)
            page_config = self._page_config(page_name, viewport)
            capability_profile = self.capability_detector.detect(
                page_name,
                page_config,
            )
            attempt = self.evidence_builder.load_runtime_attempt(page_summary)
            findings = self._scope_findings(
                scope_results,
                page_summary,
                attempt,
                capability_profile.page_type,
            )
            findings = _dedupe_findings(findings)
            findings = [
                self.false_positive_controller.apply(finding)
                for finding in findings
            ]
            dimensions = _page_dimensions(
                scope_results,
                page_summary,
                attempt,
                findings,
                capability_profile.commerce_applicable,
            )
            page_status = _page_status(page_summary, findings)
            dimensions[HealthDimension.PAGE.value] = page_status
            navigation = attempt.get("navigation", {}) or {}
            url = (
                navigation.get("final_url")
                or navigation.get("requested_url")
                or page_config.get("url")
            )
            pages.append(
                PageHealth(
                    site=site,
                    page=page_name,
                    page_type=capability_profile.page_type,
                    viewport=viewport,
                    url=url,
                    status=page_status,
                    capabilities=capability_profile,
                    dimensions=dimensions,
                    finding_ids=[
                        finding.finding_id for finding in findings
                    ],
                    finding_count=len(findings),
                    source_result_count=len(scope_results),
                )
            )
            all_findings.extend(findings)

        dimension_statuses = _aggregate_dimensions(pages)
        if not pages:
            overall_status = HealthStatus.UNVERIFIED
        else:
            overall_status = worst_status(
                [page.status for page in pages]
                + list(dimension_statuses.values())
            )
        alert = alert_decision(all_findings)
        overall_health = _overall_health(overall_status, alert)
        report = HealthRunReport(
            run_id=str(run_id),
            site=self.site,
            generated_at=utc_timestamp(),
            overall_health=overall_health,
            status=overall_status,
            pages=pages,
            findings=all_findings,
            alert=alert,
            ai_analysis=AIAnalysis(
                enabled=bool((self.config.get("ai") or {}).get("enabled", False)),
                invoked=False,
                status="PENDING",
                reason="not_evaluated",
            ),
            dimension_statuses=dimension_statuses,
            summary=_summary(pages, all_findings, alert),
            changes_since_previous_run={
                "status": HealthStatus.UNVERIFIED.value,
                "reason": "history_store_not_implemented_in_phase_1",
                "comparisons": [],
            },
        )
        report.ai_analysis = analyze_on_demand(
            report,
            self.config.get("ai", {}),
            analyzer=self.ai_analyzer,
        )
        return report

    def _scope_findings(
        self,
        scope_results,
        page_summary,
        attempt,
        page_type,
    ):
        findings = []
        for index, result in enumerate(scope_results):
            source_id = _result_id(result, index)
            if result.get("result_type") == "page_summary":
                for raw in result.get("findings", []) or []:
                    if not isinstance(raw, dict):
                        continue
                    findings.append(
                        classify_runtime_finding(
                            raw,
                            result,
                            attempt,
                            self.evidence_builder,
                            page_type,
                            source_id,
                        )
                    )
                recovered = recovered_retry_finding(
                    result,
                    page_type,
                    source_id,
                    self.evidence_builder,
                    attempt,
                )
                if recovered:
                    findings.append(recovered)
                continue
            finding = classify_result(
                result,
                attempt,
                self.evidence_builder,
                page_type,
                source_id,
            )
            if finding:
                findings.append(finding)
        return findings

    def _page_config(self, page_name, viewport):
        try:
            return get_page_config(
                page_name,
                self.site_config,
                viewport=viewport,
            )
        except (KeyError, TypeError, ValueError):
            pages = (self.site_config or {}).get("pages", {}) or {}
            raw = pages.get(page_name, {})
            return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _load_site_config(site):
        try:
            return load_site_config(site)
        except (FileNotFoundError, KeyError, ValueError):
            return {"site": site, "pages": {}}


def _page_status(page_summary, findings):
    statuses = [finding.status for finding in findings]
    if statuses:
        return worst_status(statuses)
    if not page_summary:
        return HealthStatus.UNVERIFIED
    status = str(page_summary.get("status") or "passed").lower()
    return {
        "passed": HealthStatus.PASS,
        "initialized": HealthStatus.PASS,
        "content_changed": HealthStatus.EXPECTED_CHANGE,
        "warning": HealthStatus.WARN,
        "failed": HealthStatus.UNVERIFIED,
        "not_run": HealthStatus.UNVERIFIED,
    }.get(status, HealthStatus.UNVERIFIED)


def _page_dimensions(
    results,
    page_summary,
    attempt,
    findings,
    commerce_applicable,
):
    dimensions = {
        dimension.value: HealthStatus.UNVERIFIED
        for dimension in HealthDimension
    }
    by_dimension = defaultdict(list)
    for finding in findings:
        by_dimension[finding.dimension.value].append(finding.status)
    for dimension, statuses in by_dimension.items():
        dimensions[dimension] = worst_status(statuses)

    visual_results = [
        result
        for result in results
        if result.get("result_type", "visual") == "visual"
        and result.get("case") != "runtime"
    ]
    if visual_results and HealthDimension.VISUAL.value not in by_dimension:
        dimensions[HealthDimension.VISUAL.value] = _legacy_status(
            [result.get("status") for result in visual_results]
        )

    observations = [
        result for result in results if result.get("result_type") == "deterministic_check"
    ]
    for dimension in (HealthDimension.FUNCTIONAL, HealthDimension.DOM_CONTENT):
        matching = [
            result
            for result in observations
            if str(result.get("dimension")) == dimension.value
        ]
        if matching and dimension.value not in by_dimension:
            dimensions[dimension.value] = _legacy_status(
                [result.get("status") for result in matching]
            )

    if page_summary:
        runtime_status = str(page_summary.get("runtime_status") or "passed").lower()
        if HealthDimension.RUNTIME.value not in by_dimension:
            dimensions[HealthDimension.RUNTIME.value] = _legacy_status([runtime_status])
        if HealthDimension.TEST_SYSTEM.value not in by_dimension:
            dimensions[HealthDimension.TEST_SYSTEM.value] = HealthStatus.PASS

    navigation = attempt.get("navigation", {}) or {}
    navigation_status = navigation.get("status")
    if HealthDimension.AVAILABILITY.value not in by_dimension:
        if isinstance(navigation_status, int):
            if 200 <= navigation_status < 400:
                availability_status = HealthStatus.PASS
            elif 400 <= navigation_status < 600:
                availability_status = HealthStatus.FAIL
            else:
                availability_status = HealthStatus.UNVERIFIED
            dimensions[HealthDimension.AVAILABILITY.value] = availability_status
    if attempt and HealthDimension.NETWORK.value not in by_dimension:
        dimensions[HealthDimension.NETWORK.value] = HealthStatus.PASS

    if HealthDimension.COMMERCE.value not in by_dimension:
        dimensions[HealthDimension.COMMERCE.value] = (
            HealthStatus.UNVERIFIED
            if commerce_applicable
            else HealthStatus.NOT_APPLICABLE
        )
    return dimensions


def _aggregate_dimensions(pages):
    values = defaultdict(list)
    for page in pages:
        for dimension, status in page.dimensions.items():
            values[dimension].append(status)
    return {
        dimension.value: worst_status(
            values.get(dimension.value, []),
            default=HealthStatus.UNVERIFIED,
        )
        for dimension in HealthDimension
    }


def _overall_health(status, alert):
    if alert.should_alert and alert.severity in (Severity.HIGH, Severity.CRITICAL):
        return OverallHealth.CRITICAL
    if status in (HealthStatus.PASS, HealthStatus.EXPECTED_CHANGE):
        return OverallHealth.HEALTHY
    return OverallHealth.DEGRADED


def _summary(pages, findings, alert):
    status_counts = Counter(page.status.value for page in pages)
    severity_counts = Counter(finding.severity.value for finding in findings)
    classification_counts = Counter(
        finding.classification.value for finding in findings
    )
    evidence_counts = Counter(finding.evidence_level.value for finding in findings)
    return {
        "page_count": len(pages),
        "finding_count": len(findings),
        "actionable_finding_count": sum(
            1 for finding in findings if finding.alert_eligible
        ),
        "blocked_scope_count": sum(
            1 for page in pages if page.status == HealthStatus.BLOCKED
        ),
        "unverified_scope_count": sum(
            1 for page in pages if page.status == HealthStatus.UNVERIFIED
        ),
        "page_status_counts": dict(status_counts),
        "severity_counts": dict(severity_counts),
        "classification_counts": dict(classification_counts),
        "evidence_level_counts": dict(evidence_counts),
        "alert_type": alert.alert_type,
        "health_score_state": "DEFERRED",
    }


def _legacy_status(statuses):
    mapped = []
    for value in statuses:
        mapped.append(
            {
                "passed": HealthStatus.PASS,
                "initialized": HealthStatus.PASS,
                "skipped": HealthStatus.UNVERIFIED,
                "warning": HealthStatus.WARN,
                "failed": HealthStatus.FAIL,
                "content_changed": HealthStatus.EXPECTED_CHANGE,
                "disabled": HealthStatus.NOT_APPLICABLE,
            }.get(str(value or "").lower(), HealthStatus.UNVERIFIED)
        )
    return worst_status(mapped, default=HealthStatus.UNVERIFIED)


def _latest_page_summary(results):
    for result in reversed(results):
        if result.get("result_type") == "page_summary":
            return result
    return {}


def _dedupe_findings(findings):
    selected = {}
    for finding in findings:
        existing = selected.get(finding.finding_id)
        if existing is None:
            selected[finding.finding_id] = finding
            continue
        existing.source_result_ids = list(
            dict.fromkeys(existing.source_result_ids + finding.source_result_ids)
        )
        evidence = {
            item.evidence_id: item
            for item in existing.evidence + finding.evidence
        }
        existing.evidence = list(evidence.values())
        if STATUS_RANK[finding.status] > STATUS_RANK[existing.status]:
            existing.status = finding.status
        existing.severity = highest_severity(
            [existing.severity, finding.severity]
        )
        existing.confidence = max(existing.confidence, finding.confidence)
    return list(selected.values())


def _result_id(result, index):
    stable = {
        "run_id": result.get("run_id"),
        "site": result.get("site"),
        "viewport": result.get("viewport"),
        "page": result.get("page"),
        "case": result.get("case"),
        "result_type": result.get("result_type", "visual"),
        "status": result.get("status"),
        "index": index,
    }
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return "result-" + hashlib.sha256(encoded).hexdigest()[:12]


def _first_value(results, key):
    for result in results:
        if result.get(key) not in (None, ""):
            return result[key]
    return None
