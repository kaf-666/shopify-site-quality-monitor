from dataclasses import dataclass
from pathlib import Path

from playwright_checks.health.capability_registry import (
    CapabilityCheckRegistry,
)
from playwright_checks.health.file_io import atomic_write_json
from playwright_checks.health.planner import TestPlan, TestPlanner
from playwright_checks.health.profile_adapter import LegacySiteConfigAdapter
from playwright_checks.health.site_profile import SiteProfile


@dataclass
class ProfileArtifactBundle:
    profile: SiteProfile
    plan: TestPlan
    profile_path: Path | None = None
    plan_path: Path | None = None


def build_profile_bundle(
    site_config,
    health_config=None,
    observations=None,
    registry=None,
    generated_at=None,
    explicit_interactions=None,
):
    profile = LegacySiteConfigAdapter(
        site_config,
        health_config=health_config,
        observations=observations,
    ).build(generated_at=generated_at)
    planner = TestPlanner(
        registry=registry or CapabilityCheckRegistry(),
    )
    plan = planner.build_plan(
        profile,
        explicit_interactions=explicit_interactions,
    )
    return ProfileArtifactBundle(profile=profile, plan=plan)


def write_profile_bundle(bundle, run_root):
    if not isinstance(bundle, ProfileArtifactBundle):
        raise TypeError("bundle must be ProfileArtifactBundle")
    root = Path(run_root)
    profile_path = root / "site-profile.json"
    plan_path = root / "test-plan.json"
    atomic_write_json(profile_path, bundle.profile.to_dict())
    atomic_write_json(plan_path, bundle.plan.to_dict())
    bundle.profile_path = profile_path.resolve()
    bundle.plan_path = plan_path.resolve()
    return bundle
