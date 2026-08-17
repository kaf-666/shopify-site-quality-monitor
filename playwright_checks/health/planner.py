from collections import Counter
from dataclasses import dataclass, field
from enum import Enum

from playwright_checks.health.capability_registry import (
    CapabilityCheckRegistry,
)
from playwright_checks.health.interaction_policy import InteractionPolicy
from playwright_checks.health.models import (
    EvidenceType,
    HealthStatus,
    PageType,
    Severity,
    SideEffectLevel,
    serialize,
)
from playwright_checks.health.site_profile import (
    CapabilityStatus,
    SiteProfile,
)


TEST_PLAN_SCHEMA_VERSION = "0.1"


class PlanDisposition(str, Enum):
    READY = "READY"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    DISABLED = "DISABLED"


@dataclass(frozen=True)
class PlannedCheck:
    plan_id: str
    check_id: str
    capability: str
    page_id: str
    page_type: PageType
    url: str
    disposition: PlanDisposition
    status: HealthStatus
    should_execute: bool
    reason: str
    interaction_policy: SideEffectLevel
    interaction_allowed: bool
    severity: Severity
    executor: str
    evidence_requirements: tuple[EvidenceType, ...]
    enabled_by_default: bool
    capability_status: CapabilityStatus

    def to_dict(self):
        return serialize(self)


@dataclass
class TestPlan:
    site_id: str
    profile_id: str
    checks: list[PlannedCheck]
    generated_at: str
    unmapped_capabilities: list[dict] = field(default_factory=list)
    schema_version: str = TEST_PLAN_SCHEMA_VERSION

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "site_id": self.site_id,
            "profile_id": self.profile_id,
            "generated_at": self.generated_at,
            "summary": self.summary(),
            "checks": [check.to_dict() for check in self.checks],
            "unmapped_capabilities": list(self.unmapped_capabilities),
        }

    def summary(self):
        dispositions = Counter(check.disposition.value for check in self.checks)
        return {
            "check_count": len(self.checks),
            "ready_count": sum(
                1 for check in self.checks if check.should_execute
            ),
            "non_executable_count": sum(
                1 for check in self.checks if not check.should_execute
            ),
            "disposition_counts": dict(dispositions),
            "unmapped_capability_count": len(self.unmapped_capabilities),
        }


class TestPlanner:
    """Create a deterministic plan without executing any browser action."""

    def __init__(self, registry=None, interaction_policy=None):
        self.registry = registry or CapabilityCheckRegistry()
        self.interaction_policy = interaction_policy

    def build_plan(
        self,
        profile,
        page_id=None,
        explicit_interactions=None,
    ):
        if not isinstance(profile, SiteProfile):
            raise TypeError("profile must be SiteProfile")
        profile.validate()
        explicit = {
            str(value).strip().lower()
            for value in (explicit_interactions or [])
            if str(value).strip()
        }
        policy = self.interaction_policy or InteractionPolicy(
            profile.interaction_policy.to_config()
        )
        planned = []
        unmapped = []
        for page in profile.pages:
            if page_id is not None and page.page_id != page_id:
                continue
            for capability in profile.page_capabilities(page.page_id):
                checks = self.registry.checks_for(
                    capability.name,
                    page.page_type,
                )
                if not checks:
                    if capability.status == CapabilityStatus.PRESENT:
                        unmapped.append(
                            {
                                "page_id": page.page_id,
                                "page_type": page.page_type.value,
                                "capability": capability.name,
                                "status": capability.status.value,
                                "reason": "no_registry_entry",
                            }
                        )
                    continue
                if capability.status == CapabilityStatus.ABSENT:
                    continue
                for check in checks:
                    plan_id = f"{page.page_id}:{check.check_id}"
                    is_explicit = bool(
                        capability.name in explicit
                        or check.check_id.lower() in explicit
                        or plan_id.lower() in explicit
                    )
                    decision = policy.decide(
                        capability.name,
                        explicit_opt_in=is_explicit,
                        level=capability.interaction_policy,
                    )
                    disposition, status, should_execute, reason = (
                        _disposition(
                            page,
                            capability.status,
                            check.enabled_by_default,
                            decision.allowed,
                            decision.reason,
                        )
                    )
                    planned.append(
                        PlannedCheck(
                            plan_id=plan_id,
                            check_id=check.check_id,
                            capability=capability.name,
                            page_id=page.page_id,
                            page_type=page.page_type,
                            url=page.url,
                            disposition=disposition,
                            status=status,
                            should_execute=should_execute,
                            reason=reason,
                            interaction_policy=decision.level,
                            interaction_allowed=decision.allowed,
                            severity=check.severity,
                            executor=check.executor,
                            evidence_requirements=(
                                check.evidence_requirements
                            ),
                            enabled_by_default=check.enabled_by_default,
                            capability_status=capability.status,
                        )
                    )
        planned.sort(key=lambda value: (value.page_id, value.check_id))
        unmapped.sort(
            key=lambda value: (value["page_id"], value["capability"])
        )
        return TestPlan(
            site_id=profile.site_identity.site_id,
            profile_id=profile.profile_id,
            checks=planned,
            generated_at=profile.generated_at,
            unmapped_capabilities=unmapped,
        )

    def plan(
        self,
        profile,
        page_id=None,
        explicit_interactions=None,
    ):
        return self.build_plan(
            profile,
            page_id=page_id,
            explicit_interactions=explicit_interactions,
        ).checks


def _disposition(
    page,
    capability_status,
    enabled_by_default,
    interaction_allowed,
    interaction_reason,
):
    if capability_status == CapabilityStatus.NOT_APPLICABLE:
        return (
            PlanDisposition.NOT_APPLICABLE,
            HealthStatus.NOT_APPLICABLE,
            False,
            "capability_not_applicable",
        )
    if capability_status == CapabilityStatus.UNKNOWN:
        return (
            PlanDisposition.CAPABILITY_UNKNOWN,
            HealthStatus.UNVERIFIED,
            False,
            "capability_presence_unknown",
        )
    if not page.enabled:
        return (
            PlanDisposition.DISABLED,
            HealthStatus.UNVERIFIED,
            False,
            "page_disabled",
        )
    if not page.representative:
        return (
            PlanDisposition.DISABLED,
            HealthStatus.UNVERIFIED,
            False,
            "page_not_representative",
        )
    if not enabled_by_default:
        return (
            PlanDisposition.DISABLED,
            HealthStatus.UNVERIFIED,
            False,
            "check_disabled_by_default",
        )
    if not interaction_allowed:
        return (
            PlanDisposition.POLICY_BLOCKED,
            HealthStatus.UNVERIFIED,
            False,
            f"interaction_policy_blocked:{interaction_reason}",
        )
    return (
        PlanDisposition.READY,
        HealthStatus.UNVERIFIED,
        True,
        "planned_not_yet_executed",
    )
