"""Health contracts, deterministic aggregation, and shadow execution.

The primary health engine consumes observations and remains independent of
browser lifecycle, screenshots, and baselines. Phase 3 adds an optional
executor layer whose browser/page dependencies are injected by its caller.
"""

from playwright_checks.health.engine import HealthEngine
from playwright_checks.health.execution_models import (
    ArtifactContext,
    CheckResult,
    ExecutionStatus,
    ExecutorContext,
    RuntimeMode,
    RuntimePolicy,
)
from playwright_checks.health.executor_registry import (
    ExecutorDefinition,
    ExecutorRegistry,
)
from playwright_checks.health.capability_registry import (
    CapabilityCheck,
    CapabilityCheckRegistry,
)
from playwright_checks.health.models import (
    EvidenceLevel,
    FailureClassification,
    HealthDimension,
    HealthStatus,
    PageType,
    Severity,
    SideEffectLevel,
)
from playwright_checks.health.planner import PlannedCheck, TestPlan, TestPlanner
from playwright_checks.health.site_profile import (
    CapabilityStatus,
    ProfileCapability,
    ProfilePage,
    ProfileSource,
    SiteIdentity,
    SitePlatform,
    SiteProfile,
    SiteType,
)

__all__ = [
    "ArtifactContext",
    "CapabilityCheck",
    "CapabilityCheckRegistry",
    "CapabilityStatus",
    "CheckResult",
    "EvidenceLevel",
    "ExecutionStatus",
    "ExecutorContext",
    "ExecutorDefinition",
    "ExecutorRegistry",
    "FailureClassification",
    "HealthDimension",
    "HealthEngine",
    "HealthStatus",
    "PageType",
    "PlannedCheck",
    "ProfileCapability",
    "ProfilePage",
    "ProfileSource",
    "RuntimeMode",
    "RuntimePolicy",
    "Severity",
    "SideEffectLevel",
    "SiteIdentity",
    "SitePlatform",
    "SiteProfile",
    "SiteType",
    "TestPlan",
    "TestPlanner",
]
