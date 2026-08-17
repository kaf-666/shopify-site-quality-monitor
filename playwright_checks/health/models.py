from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


HEALTH_SCHEMA_VERSION = "2.0"


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class HealthStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    FLAKY = "FLAKY"
    EXPECTED_CHANGE = "EXPECTED_CHANGE"
    UNVERIFIED = "UNVERIFIED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class OverallHealth(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    CRITICAL = "CRITICAL"


class Severity(str, Enum):
    NONE = "NONE"
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


SEVERITY_RANK = {
    Severity.NONE: 0,
    Severity.INFO: 1,
    Severity.LOW: 2,
    Severity.MEDIUM: 3,
    Severity.HIGH: 4,
    Severity.CRITICAL: 5,
}


STATUS_RANK = {
    HealthStatus.NOT_APPLICABLE: 0,
    HealthStatus.PASS: 1,
    HealthStatus.EXPECTED_CHANGE: 2,
    HealthStatus.WARN: 3,
    HealthStatus.FLAKY: 4,
    HealthStatus.UNVERIFIED: 5,
    HealthStatus.BLOCKED: 6,
    HealthStatus.FAIL: 7,
}


class HealthDimension(str, Enum):
    AVAILABILITY = "availability"
    PAGE = "page_health"
    FUNCTIONAL = "functional"
    VISUAL = "visual"
    DOM_CONTENT = "dom_content"
    RUNTIME = "runtime"
    NETWORK = "network"
    PERFORMANCE = "performance"
    RESPONSIVE = "responsive"
    ACCESSIBILITY = "accessibility"
    SEO = "seo"
    COMMERCE = "commerce"
    TEST_SYSTEM = "test_system"


class PageType(str, Enum):
    HOME = "HOME"
    PLP = "PLP"
    PDP = "PDP"
    SEARCH = "SEARCH"
    CART = "CART"
    LOGIN = "LOGIN"
    ACCOUNT = "ACCOUNT"
    CHECKOUT_ENTRY = "CHECKOUT_ENTRY"
    CONTENT = "CONTENT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class SideEffectLevel(str, Enum):
    SAFE = "SAFE"
    TRANSACTIONAL_SAFE = "TRANSACTIONAL_SAFE"
    HIGH_RISK = "HIGH_RISK"


class FailureClassification(str, Enum):
    REAL_UI_BUG = "REAL_UI_BUG"
    REAL_FUNCTIONAL_BUG = "REAL_FUNCTIONAL_BUG"
    REAL_SITE_FAILURE = "REAL_SITE_FAILURE"
    SITE_DOWN = "SITE_DOWN"
    WAF_BLOCK = "WAF_BLOCK"
    RATE_LIMIT = "RATE_LIMIT"
    SELECTOR_CHANGED = "SELECTOR_CHANGED"
    CONTENT_CHANGED = "CONTENT_CHANGED"
    LAYOUT_CHANGED = "LAYOUT_CHANGED"
    THIRD_PARTY_NOISE = "THIRD_PARTY_NOISE"
    NETWORK_TRANSIENT = "NETWORK_TRANSIENT"
    PERFORMANCE_REGRESSION = "PERFORMANCE_REGRESSION"
    TEST_SCRIPT_ISSUE = "TEST_SCRIPT_ISSUE"
    TEST_ENVIRONMENT_ISSUE = "TEST_ENVIRONMENT_ISSUE"
    EXPECTED_BUSINESS_STATE = "EXPECTED_BUSINESS_STATE"
    UNKNOWN = "UNKNOWN"


class EvidenceLevel(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


EVIDENCE_RANK = {
    EvidenceLevel.NONE: 0,
    EvidenceLevel.LOW: 1,
    EvidenceLevel.MEDIUM: 2,
    EvidenceLevel.HIGH: 3,
}


class EvidenceType(str, Enum):
    SCREENSHOT = "SCREENSHOT"
    VISUAL_DIFF = "VISUAL_DIFF"
    URL = "URL"
    HTTP = "HTTP"
    SELECTOR = "SELECTOR"
    DOM = "DOM"
    CONSOLE = "CONSOLE"
    NETWORK = "NETWORK"
    METRIC = "METRIC"
    TRACE = "TRACE"
    LOG = "LOG"
    RUNTIME_ARTIFACT = "RUNTIME_ARTIFACT"


@dataclass
class CapabilitySignal:
    name: str
    side_effect_level: SideEffectLevel
    detected: bool = True
    source: str = "site_config"
    confidence: float = 1.0
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageCapabilityProfile:
    page_type: PageType
    capabilities: list[CapabilitySignal] = field(default_factory=list)
    detector: str = "config_capability_detector"
    detector_version: str = "1.0"
    commerce_applicable: bool = False


@dataclass
class EvidenceItem:
    evidence_id: str
    evidence_type: EvidenceType
    level: EvidenceLevel
    source: str
    summary: str
    reference: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_timestamp)


@dataclass
class HealthFinding:
    finding_id: str
    site: str
    page: str
    page_type: PageType
    viewport: str
    dimension: HealthDimension
    status: HealthStatus
    severity: Severity
    classification: FailureClassification
    reason_code: str
    title: str
    summary: str
    business_impact: str
    confidence: float
    evidence_level: EvidenceLevel
    evidence: list[EvidenceItem] = field(default_factory=list)
    recommendation: str | None = None
    alert_eligible: bool = False
    suppression_reason: str | None = None
    source_result_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=utc_timestamp)


@dataclass
class PageHealth:
    site: str
    page: str
    page_type: PageType
    viewport: str
    url: str | None
    status: HealthStatus
    capabilities: PageCapabilityProfile
    dimensions: dict[str, HealthStatus] = field(default_factory=dict)
    finding_ids: list[str] = field(default_factory=list)
    finding_count: int = 0
    source_result_count: int = 0


@dataclass
class AlertDecision:
    should_alert: bool
    alert_type: str
    severity: Severity
    finding_ids: list[str] = field(default_factory=list)
    reason: str = "no_actionable_findings"


@dataclass
class AIAnalysis:
    enabled: bool = False
    invoked: bool = False
    status: str = "SKIPPED"
    reason: str = "disabled"
    provider: str | None = None
    summary: str | None = None
    recommendations: list[str] = field(default_factory=list)
    self_heal_suggestions: list[dict[str, Any]] = field(default_factory=list)
    analyzed_finding_ids: list[str] = field(default_factory=list)


@dataclass
class HealthRunReport:
    run_id: str
    site: str
    generated_at: str
    overall_health: OverallHealth
    status: HealthStatus
    pages: list[PageHealth]
    findings: list[HealthFinding]
    alert: AlertDecision
    ai_analysis: AIAnalysis
    dimension_statuses: dict[str, HealthStatus]
    summary: dict[str, Any]
    changes_since_previous_run: dict[str, Any]
    site_profile_reference: str | None = None
    site_profile_summary: dict[str, Any] = field(default_factory=dict)
    test_plan_reference: str | None = None
    test_plan_summary: dict[str, Any] = field(default_factory=dict)
    health_score: int | None = None
    health_score_reason: str = "deferred_until_history_and_calibration_exist"
    schema_version: str = HEALTH_SCHEMA_VERSION

    def to_dict(self):
        return serialize(self)


def worst_status(values, default=HealthStatus.PASS):
    statuses = [coerce_health_status(value) for value in values if value]
    if not statuses:
        return default
    return max(statuses, key=lambda value: STATUS_RANK[value])


def highest_severity(values, default=Severity.NONE):
    severities = [coerce_severity(value) for value in values if value]
    if not severities:
        return default
    return max(severities, key=lambda value: SEVERITY_RANK[value])


def coerce_health_status(value):
    if isinstance(value, HealthStatus):
        return value
    return HealthStatus(str(value).upper())


def coerce_severity(value):
    if isinstance(value, Severity):
        return value
    return Severity(str(value).upper())


def serialize(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return serialize(asdict(value))
    if isinstance(value, dict):
        return {str(key): serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize(item) for item in value]
    return value
