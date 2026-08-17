import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright_checks.health.interaction_policy import InteractionPolicy
from playwright_checks.health.models import (
    EvidenceItem,
    EvidenceLevel,
    EvidenceType,
    HealthStatus,
    PageType,
    serialize,
    utc_timestamp,
)
from playwright_checks.health.planner import PlannedCheck
from playwright_checks.health.site_profile import ProfilePage, SiteProfile
from playwright_checks.runtime.evidence import redact_url, sanitize_payload


CHECK_RESULT_SCHEMA_VERSION = "1.0"


class RuntimeMode(str, Enum):
    MONITOR = "MONITOR"
    DIAGNOSE = "DIAGNOSE"
    DISCOVER = "DISCOVER"


class ExecutionStatus(str, Enum):
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    TIMEOUT = "TIMEOUT"
    ERROR = "ERROR"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 0
    retry_delay_ms: int = 0
    retry_on_timeout: bool = True
    retry_on_error: bool = False

    def validate(self):
        if (
            not isinstance(self.max_retries, int)
            or isinstance(self.max_retries, bool)
            or self.max_retries < 0
        ):
            raise ValueError("max_retries must be a non-negative integer")
        if (
            not isinstance(self.retry_delay_ms, int)
            or isinstance(self.retry_delay_ms, bool)
            or not 0 <= self.retry_delay_ms <= 60000
        ):
            raise ValueError("retry_delay_ms must be between 0 and 60000")
        if not isinstance(self.retry_on_timeout, bool):
            raise TypeError("retry_on_timeout must be boolean")
        if not isinstance(self.retry_on_error, bool):
            raise TypeError("retry_on_error must be boolean")
        return self


@dataclass(frozen=True)
class RuntimePolicy:
    mode: RuntimeMode = RuntimeMode.MONITOR
    shadow_only: bool = True
    transactional_safe_enabled: bool = False
    high_risk_enabled: bool = False
    baseline_update_enabled: bool = False
    selector_rewrite_enabled: bool = False
    site_config_rewrite_enabled: bool = False
    profile_overwrite_enabled: bool = False
    destructive_interaction_enabled: bool = False

    @classmethod
    def monitor(cls, transactional_safe_enabled=False):
        return cls(
            mode=RuntimeMode.MONITOR,
            transactional_safe_enabled=bool(transactional_safe_enabled),
        ).validate()

    def validate(self):
        if not isinstance(self.mode, RuntimeMode):
            raise TypeError("runtime mode must be RuntimeMode")
        if self.mode != RuntimeMode.MONITOR:
            return self
        forbidden = {
            "high_risk_enabled": self.high_risk_enabled,
            "baseline_update_enabled": self.baseline_update_enabled,
            "selector_rewrite_enabled": self.selector_rewrite_enabled,
            "site_config_rewrite_enabled": self.site_config_rewrite_enabled,
            "profile_overwrite_enabled": self.profile_overwrite_enabled,
            "destructive_interaction_enabled": (
                self.destructive_interaction_enabled
            ),
        }
        enabled = sorted(name for name, value in forbidden.items() if value)
        if enabled:
            raise ValueError(
                "MONITOR safety guard forbids: " + ", ".join(enabled)
            )
        if not self.shadow_only:
            raise ValueError("Phase 3 MONITOR executor must remain shadow_only")
        return self


@dataclass(frozen=True)
class ArtifactContext:
    run_root: Path
    site_root: Path
    page_root: Path
    screenshots_path: Path
    trace_path: Path
    evidence_path: Path

    @classmethod
    def for_page(cls, run_root, site_id, viewport, page_id):
        root = Path(run_root).resolve()
        page_root = (
            root
            / "shadow"
            / _safe_segment(site_id)
            / _safe_segment(viewport)
            / _safe_segment(page_id)
        ).resolve()
        _require_within(page_root, root)
        return cls(
            run_root=root,
            site_root=(root / "shadow" / _safe_segment(site_id)).resolve(),
            page_root=page_root,
            screenshots_path=(page_root / "screenshots").resolve(),
            trace_path=(page_root / "traces").resolve(),
            evidence_path=(page_root / "evidence").resolve(),
        ).validate()

    def validate(self):
        root = self.run_root.resolve()
        for path in (
            self.site_root,
            self.page_root,
            self.screenshots_path,
            self.trace_path,
            self.evidence_path,
        ):
            _require_within(path.resolve(), root)
        return self

    def ensure_directories(self):
        for path in (
            self.page_root,
            self.screenshots_path,
            self.trace_path,
            self.evidence_path,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return self

    def to_dict(self):
        root = self.run_root.resolve()

        def relative(path):
            return path.resolve().relative_to(root).as_posix()

        return {
            "run_root": str(root),
            "site_root": relative(self.site_root),
            "page_root": relative(self.page_root),
            "screenshots_path": relative(self.screenshots_path),
            "trace_path": relative(self.trace_path),
            "evidence_path": relative(self.evidence_path),
        }


@dataclass
class ExecutorContext:
    run_id: str
    site_profile: SiteProfile
    page_profile: ProfilePage
    planned_check: PlannedCheck
    page: Any
    browser_context: Any
    runtime_policy: RuntimePolicy
    interaction_policy: InteractionPolicy
    artifact_context: ArtifactContext
    target: str
    selector_hint: Any | None = None
    timeout_ms: int = 10000
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self):
        if not str(self.run_id).strip():
            raise ValueError("run_id must be non-empty")
        if not isinstance(self.site_profile, SiteProfile):
            raise TypeError("site_profile must be SiteProfile")
        if not isinstance(self.page_profile, ProfilePage):
            raise TypeError("page_profile must be ProfilePage")
        if not isinstance(self.planned_check, PlannedCheck):
            raise TypeError("planned_check must be PlannedCheck")
        if self.planned_check.page_id != self.page_profile.page_id:
            raise ValueError("planned_check and page_profile page_id mismatch")
        if self.planned_check.page_type != self.page_profile.page_type:
            raise ValueError("planned_check and page_profile page_type mismatch")
        if not isinstance(self.runtime_policy, RuntimePolicy):
            raise TypeError("runtime_policy must be RuntimePolicy")
        self.runtime_policy.validate()
        if not isinstance(self.interaction_policy, InteractionPolicy):
            raise TypeError("interaction_policy must be InteractionPolicy")
        if not isinstance(self.artifact_context, ArtifactContext):
            raise TypeError("artifact_context must be ArtifactContext")
        self.artifact_context.validate()
        if not str(self.target).strip():
            raise ValueError("target must be non-empty")
        if isinstance(self.timeout_ms, bool) or int(self.timeout_ms) <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        self.retry_policy.validate()
        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a mapping")
        return self


@dataclass
class CheckResult:
    result_id: str
    check_id: str
    site_id: str
    page_id: str
    page_type: PageType
    page_url: str
    capability: str
    executor_key: str
    executor_version: str
    execution_status: ExecutionStatus
    health_status: HealthStatus
    expected: Any
    actual: Any
    observations: list[dict[str, Any]]
    evidence: list[EvidenceItem]
    started_at: str
    duration_ms: float
    retry_count: int
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = CHECK_RESULT_SCHEMA_VERSION

    def validate(self):
        if self.schema_version != CHECK_RESULT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported CheckResult schema_version={self.schema_version!r}"
            )
        for name, value in (
            ("result_id", self.result_id),
            ("check_id", self.check_id),
            ("site_id", self.site_id),
            ("page_id", self.page_id),
            ("capability", self.capability),
            ("executor_key", self.executor_key),
            ("executor_version", self.executor_version),
            ("started_at", self.started_at),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.page_type, PageType):
            raise TypeError("page_type must be PageType")
        parsed = urlsplit(str(self.page_url))
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("page_url must be an absolute HTTP(S) URL")
        if not isinstance(self.execution_status, ExecutionStatus):
            raise TypeError("execution_status must be ExecutionStatus")
        if not isinstance(self.health_status, HealthStatus):
            raise TypeError("health_status must be HealthStatus")
        if not isinstance(self.observations, list) or any(
            not isinstance(value, dict) for value in self.observations
        ):
            raise TypeError("observations must be a list of mappings")
        if not isinstance(self.evidence, list) or any(
            not isinstance(value, EvidenceItem) for value in self.evidence
        ):
            raise TypeError("evidence must be a list of EvidenceItem")
        if isinstance(self.duration_ms, bool) or float(self.duration_ms) < 0:
            raise ValueError("duration_ms must be non-negative")
        if isinstance(self.retry_count, bool) or int(self.retry_count) < 0:
            raise ValueError("retry_count must be non-negative")
        if self.error is not None and not isinstance(self.error, dict):
            raise TypeError("error must be a mapping or null")
        if not isinstance(self.metadata, dict):
            raise TypeError("metadata must be a mapping")
        forced_unverified = {
            ExecutionStatus.POLICY_BLOCKED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.ERROR,
            ExecutionStatus.UNSUPPORTED,
            ExecutionStatus.READY,
            ExecutionStatus.RUNNING,
        }
        if (
            self.execution_status in forced_unverified
            and self.health_status != HealthStatus.UNVERIFIED
        ):
            raise ValueError(
                f"{self.execution_status.value} requires UNVERIFIED health status"
            )
        if (
            self.execution_status == ExecutionStatus.SKIPPED
            and self.health_status
            not in (HealthStatus.UNVERIFIED, HealthStatus.NOT_APPLICABLE)
        ):
            raise ValueError(
                "SKIPPED requires UNVERIFIED or NOT_APPLICABLE health status"
            )
        return self

    def to_dict(self):
        self.validate()
        payload = serialize(self)
        payload["page_url"] = redact_url(payload["page_url"])
        return sanitize_payload(payload)

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise TypeError("CheckResult payload must be a mapping")
        evidence = []
        for index, value in enumerate(payload.get("evidence") or []):
            if not isinstance(value, dict):
                raise TypeError(f"evidence[{index}] must be a mapping")
            evidence.append(
                EvidenceItem(
                    evidence_id=str(value.get("evidence_id") or ""),
                    evidence_type=_enum(
                        EvidenceType,
                        value.get("evidence_type"),
                        f"evidence[{index}].evidence_type",
                    ),
                    level=_enum(
                        EvidenceLevel,
                        value.get("level"),
                        f"evidence[{index}].level",
                    ),
                    source=str(value.get("source") or ""),
                    summary=str(value.get("summary") or ""),
                    reference=(
                        str(value["reference"])
                        if value.get("reference") is not None
                        else None
                    ),
                    details=dict(value.get("details") or {}),
                    timestamp=str(value.get("timestamp") or utc_timestamp()),
                )
            )
        result = cls(
            result_id=str(payload.get("result_id") or ""),
            check_id=str(payload.get("check_id") or ""),
            site_id=str(payload.get("site_id") or ""),
            page_id=str(payload.get("page_id") or ""),
            page_type=_enum(PageType, payload.get("page_type"), "page_type"),
            page_url=str(payload.get("page_url") or ""),
            capability=str(payload.get("capability") or ""),
            executor_key=str(payload.get("executor_key") or ""),
            executor_version=str(payload.get("executor_version") or ""),
            execution_status=_enum(
                ExecutionStatus,
                payload.get("execution_status"),
                "execution_status",
            ),
            health_status=_enum(
                HealthStatus,
                payload.get("health_status"),
                "health_status",
            ),
            expected=payload.get("expected"),
            actual=payload.get("actual"),
            observations=[dict(value) for value in payload.get("observations") or []],
            evidence=evidence,
            started_at=str(payload.get("started_at") or ""),
            duration_ms=float(payload.get("duration_ms", 0)),
            retry_count=int(payload.get("retry_count", 0)),
            error=(
                dict(payload["error"])
                if isinstance(payload.get("error"), dict)
                else None
            ),
            metadata=dict(payload.get("metadata") or {}),
            schema_version=str(
                payload.get("schema_version") or CHECK_RESULT_SCHEMA_VERSION
            ),
        )
        return result.validate()


def check_result_id(context, executor_key):
    stable = {
        "run_id": context.run_id,
        "site_id": context.site_profile.site_identity.site_id,
        "page_id": context.page_profile.page_id,
        "check_id": context.planned_check.check_id,
        "executor_key": executor_key,
        "viewport": context.metadata.get("viewport"),
    }
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    )
    return "check-result-" + hashlib.sha256(encoded).hexdigest()[:16]


def evidence_item(
    context,
    evidence_type,
    level,
    source,
    summary,
    reference=None,
    details=None,
):
    safe_details = sanitize_payload(dict(details or {}))
    stable = json.dumps(
        {
            "run_id": context.run_id,
            "check_id": context.planned_check.check_id,
            "type": evidence_type.value,
            "source": source,
            "summary": summary,
            "reference": reference,
            "details": safe_details,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return EvidenceItem(
        evidence_id="ev-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:12],
        evidence_type=evidence_type,
        level=level,
        source=str(source),
        summary=str(summary),
        reference=str(reference) if reference else None,
        details=safe_details,
    )


def _safe_segment(value):
    text = str(value or "").strip()
    if not text or text in (".", "..") or any(
        character in text for character in ("/", "\\", ":")
    ):
        raise ValueError(f"Unsafe artifact path segment: {value!r}")
    return text


def _require_within(path, root):
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Artifact path escapes run root: {path}") from error


def _enum(enum_type, value, path):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value).strip().upper())
    except (TypeError, ValueError) as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{path} must be one of {allowed}") from error
