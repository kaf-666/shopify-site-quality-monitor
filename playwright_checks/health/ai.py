from dataclasses import dataclass, field
from typing import Any, Protocol

from playwright_checks.health.models import (
    AIAnalysis,
    FailureClassification,
    HealthStatus,
)


@dataclass
class AIAnalysisRequest:
    schema_version: str
    run_id: str
    site: str
    mode: str
    findings: list[dict[str, Any]]
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class SelfHealSuggestion:
    finding_id: str
    original_selector: str | None
    proposed_selector: str
    rationale: str
    validation_status: str = "UNVERIFIED"
    approval_required: bool = True
    applied: bool = False


class AIAnalyzer(Protocol):
    name: str

    def analyze(self, request: AIAnalysisRequest) -> AIAnalysis:
        ...


class NoOpAIAnalyzer:
    name = "none"

    def analyze(self, request):
        return AIAnalysis(
            enabled=False,
            invoked=False,
            status="SKIPPED",
            reason="no_ai_provider_configured",
            provider=self.name,
            analyzed_finding_ids=[],
        )


def analyze_on_demand(report, config=None, analyzer=None):
    config = dict(config or {})
    if not config.get("enabled", False):
        return AIAnalysis(
            enabled=False,
            invoked=False,
            status="SKIPPED",
            reason="disabled",
            provider=str(config.get("provider") or "none"),
        )

    request = build_analysis_request(report, config)
    if not request.findings:
        return AIAnalysis(
            enabled=True,
            invoked=False,
            status="SKIPPED",
            reason="no_anomalous_findings",
            provider=str(config.get("provider") or "none"),
        )
    if analyzer is None:
        return AIAnalysis(
            enabled=True,
            invoked=False,
            status="UNAVAILABLE",
            reason="provider_adapter_not_configured",
            provider=str(config.get("provider") or "none"),
            analyzed_finding_ids=[],
        )

    try:
        response = analyzer.analyze(request)
    except Exception as error:
        return AIAnalysis(
            enabled=True,
            invoked=True,
            status="ERROR",
            reason=f"provider_error:{type(error).__name__}",
            provider=getattr(analyzer, "name", None)
            or str(config.get("provider") or "unknown"),
            analyzed_finding_ids=[
                item["finding_id"] for item in request.findings
            ],
        )
    if not isinstance(response, AIAnalysis):
        raise TypeError("AI analyzer must return AIAnalysis")
    response.enabled = True
    response.invoked = True
    response.provider = response.provider or getattr(analyzer, "name", None)
    response.analyzed_finding_ids = [
        item["finding_id"] for item in request.findings
    ]
    response.self_heal_suggestions = [
        _safe_self_heal_suggestion(item)
        for item in response.self_heal_suggestions
    ]
    return response


def build_analysis_request(report, config=None):
    config = dict(config or {})
    max_findings = max(1, int(config.get("max_findings", 20)))
    max_evidence = max(1, int(config.get("max_evidence_per_finding", 8)))
    allowed_classifications = {
        FailureClassification.UNKNOWN,
        FailureClassification.SELECTOR_CHANGED,
        FailureClassification.REAL_UI_BUG,
        FailureClassification.REAL_FUNCTIONAL_BUG,
        FailureClassification.REAL_SITE_FAILURE,
        FailureClassification.SITE_DOWN,
        FailureClassification.LAYOUT_CHANGED,
        FailureClassification.TEST_SCRIPT_ISSUE,
    }
    selected = [
        finding
        for finding in report.findings
        if finding.status
        in (
            HealthStatus.FAIL,
            HealthStatus.UNVERIFIED,
        )
        and finding.classification in allowed_classifications
    ][:max_findings]
    findings = []
    for finding in selected:
        findings.append(
            {
                "finding_id": finding.finding_id,
                "page": finding.page,
                "page_type": finding.page_type.value,
                "viewport": finding.viewport,
                "dimension": finding.dimension.value,
                "deterministic_classification": finding.classification.value,
                "status": finding.status.value,
                "severity": finding.severity.value,
                "confidence": finding.confidence,
                "evidence_level": finding.evidence_level.value,
                "summary": finding.summary,
                "business_impact": finding.business_impact,
                "evidence": [
                    {
                        "evidence_id": item.evidence_id,
                        "type": item.evidence_type.value,
                        "summary": item.summary,
                        "reference": item.reference,
                        "details": item.details,
                    }
                    for item in finding.evidence[:max_evidence]
                ],
            }
        )
    return AIAnalysisRequest(
        schema_version="1.0",
        run_id=report.run_id,
        site=report.site,
        mode="failure_analysis",
        findings=findings,
        constraints={
            "evidence_required": True,
            "do_not_mutate_selectors": True,
            "do_not_update_baselines": True,
            "self_healing_suggestions_only": True,
            "human_or_explicit_task_approval_required": True,
        },
    )


def _safe_self_heal_suggestion(value):
    if isinstance(value, SelfHealSuggestion):
        payload = {
            "finding_id": value.finding_id,
            "original_selector": value.original_selector,
            "proposed_selector": value.proposed_selector,
            "rationale": value.rationale,
            "validation_status": value.validation_status,
        }
    elif isinstance(value, dict):
        payload = {
            "finding_id": value.get("finding_id"),
            "original_selector": value.get("original_selector"),
            "proposed_selector": value.get("proposed_selector"),
            "rationale": value.get("rationale"),
            "validation_status": value.get("validation_status", "UNVERIFIED"),
        }
    else:
        raise TypeError("self-heal suggestion must be a mapping or SelfHealSuggestion")
    return {
        **payload,
        "approval_required": True,
        "applied": False,
    }
