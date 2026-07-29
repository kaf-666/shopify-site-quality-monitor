from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


RUNTIME_STATUSES = ("passed", "warning", "failed")
SEVERITY_ORDER = {
    "info": 0,
    "warning": 1,
    "error": 2,
    "critical": 3,
}


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class NavigationAttempt:
    attempt: int
    requested_url: str
    state: str
    navigation_sequence: int = 1
    sequence_attempt: int = 1
    final_url: str | None = None
    status: int | None = None
    redirected: bool = False
    redirect_chain: list[dict] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    timestamp: str = field(default_factory=utc_timestamp)

    def to_dict(self):
        return {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }


@dataclass
class NavigationResult:
    requested_url: str
    final_url: str | None = None
    status: int | None = None
    redirected: bool = False
    redirect_chain: list[dict] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self):
        payload = {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }
        payload["main_document_status"] = self.status
        payload["navigation_attempts"] = list(self.attempts)
        payload["navigation_error"] = (
            {
                "error_type": self.error_type,
                "message": self.error_message,
            }
            if self.error_type or self.error_message
            else None
        )
        return payload


@dataclass
class RuntimeEvent:
    event_type: str
    timestamp: str = field(default_factory=utc_timestamp)
    message: str | None = None
    level: str | None = None
    stack: str | None = None
    source_url: str | None = None
    line: int | None = None
    column: int | None = None
    url: str | None = None
    method: str | None = None
    resource_type: str | None = None
    failure: str | None = None
    party: str | None = None
    status: int | None = None
    dialog_type: str | None = None
    noise_reason: str | None = None
    blocking: bool = True
    count: int = 1
    fingerprint: str | None = None

    def to_dict(self):
        payload = {
            key: value
            for key, value in asdict(self).items()
            if value is not None
        }
        payload["line_number"] = self.line
        payload["column_number"] = self.column
        return payload


@dataclass
class RuntimeFinding:
    severity: str
    reason_code: str
    message: str
    category: str | None = None
    source: str = "runtime_health"
    timestamp: str = field(default_factory=utc_timestamp)
    count: int = 1
    evidence: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "category": self.category or self.reason_code,
            "severity": self.severity,
            "reason_code": self.reason_code,
            "message": self.message,
            "source": self.source,
            "timestamp": self.timestamp,
            "count": self.count,
            "evidence": self.evidence,
        }


@dataclass
class RuntimeHealthResult:
    runtime_status: str
    runtime_score: int
    findings: list[RuntimeFinding] = field(default_factory=list)
    primary_failure_type: str | None = None
    primary_failure_reason: str | None = None

    def to_dict(self):
        return {
            "runtime_status": self.runtime_status,
            "runtime_score": self.runtime_score,
            "primary_failure_type": self.primary_failure_type,
            "primary_failure_reason": self.primary_failure_reason,
            "findings": [finding.to_dict() for finding in self.findings],
        }
