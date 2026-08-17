import copy
import os
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from playwright_checks.core.driver import close_browser, init_browser
from playwright_checks.core.paths import artifact_root, current_run_id
from playwright_checks.core.viewport import set_current_viewport
from playwright_checks.health.check_result_adapter import (
    CheckResultObservationAdapter,
)
from playwright_checks.health.config import get_health_check_config
from playwright_checks.health.engine import HealthEngine
from playwright_checks.health.execution_models import (
    ArtifactContext,
    ExecutionStatus,
    ExecutorContext,
    RetryPolicy,
    RuntimePolicy,
)
from playwright_checks.health.executor_engine import ExecutorEngine, terminal_result
from playwright_checks.health.executor_registry import ExecutorRegistry
from playwright_checks.health.file_io import atomic_write_json
from playwright_checks.health.interaction_policy import InteractionPolicy
from playwright_checks.health.models import HealthStatus
from playwright_checks.health.profile_artifacts import build_profile_bundle
from playwright_checks.health.shadow_comparison import ShadowComparisonBuilder
from playwright_checks.health.shadow_history import record_shadow_history
from playwright_checks.health.shadow_maturity import ShadowMaturityPolicy
from playwright_checks.runtime.evidence import redact_text


SHADOW_EXECUTOR_ENV = "HEALTH_SHADOW_EXECUTOR_ENABLED"
SHADOW_RESULT_SCHEMA_VERSION = "1.0"


@dataclass
class ShadowRunArtifacts:
    enabled: bool
    check_results_path: str | None = None
    observations_path: str | None = None
    comparison_path: str | None = None
    history_path: str | None = None
    history_summary_path: str | None = None
    check_results: list | None = None
    observations: list | None = None
    comparison: object | None = None
    history_summary: dict | None = None
    history_error: str | None = None
    error: str | None = None


def shadow_executor_enabled(site_config=None):
    configured = bool(
        (
            get_health_check_config(site_config).get("shadow_executor")
            or {}
        ).get("enabled", False)
    )
    value = os.environ.get(SHADOW_EXECUTOR_ENV)
    if value is None:
        return configured
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def run_shadow_pipeline_fail_open(*args, **kwargs):
    try:
        return run_shadow_pipeline(*args, **kwargs)
    except Exception as error:
        return ShadowRunArtifacts(
            enabled=True,
            error=redact_text(f"{type(error).__name__}: {error}"),
        )


def run_shadow_pipeline(
    legacy_results,
    site_config,
    viewport_names,
    selected_page_ids=None,
    run_id=None,
    run_artifact_root=None,
    executor_registry=None,
    browser_factory=init_browser,
    browser_closer=close_browser,
    history_root=None,
    scheduler=None,
    scheduler_metadata=None,
    legacy_gate_failed=False,
):
    if not shadow_executor_enabled(site_config):
        return ShadowRunArtifacts(enabled=False)
    run_id = str(run_id or current_run_id())
    artifact_base = Path(run_artifact_root or artifact_root()).resolve()
    root = artifact_base / run_id
    root.mkdir(parents=True, exist_ok=True)
    health_config = get_health_check_config(site_config)
    shadow_config = health_config.get("shadow_executor") or {}
    maturity_policy = ShadowMaturityPolicy.from_config(health_config)
    timeout_ms = _positive_int(shadow_config.get("timeout_ms", 10000), 10000)
    retry_policy = RetryPolicy(
        max_retries=_non_negative_int(shadow_config.get("max_retries", 0), 0),
        retry_delay_ms=_non_negative_int(
            shadow_config.get("retry_delay_ms", 0),
            0,
        ),
        retry_on_timeout=bool(shadow_config.get("retry_on_timeout", True)),
        retry_on_error=bool(shadow_config.get("retry_on_error", False)),
    ).validate()
    profile_bundle = build_profile_bundle(site_config)
    profile = profile_bundle.profile
    plan = profile_bundle.plan
    supported_pages = {"home", "collection", "product"}
    if selected_page_ids is not None:
        supported_pages &= {str(value) for value in selected_page_ids}
    pages = [
        page
        for page in profile.pages
        if page.page_id in supported_pages
    ]
    selected_page_ids = {page.page_id for page in pages}
    plan = replace(
        plan,
        checks=[
            check for check in plan.checks
            if check.page_id in selected_page_ids
        ],
        unmapped_capabilities=[
            capability for capability in plan.unmapped_capabilities
            if capability.get("page_id") in selected_page_ids
        ],
    )
    execution_registry = executor_registry or ExecutorRegistry()
    engine = ExecutorEngine(execution_registry)
    runtime_policy = RuntimePolicy.monitor(transactional_safe_enabled=False)
    interaction_policy = InteractionPolicy(
        profile.interaction_policy.to_config()
    )
    capability_index = {
        (capability.page_id, capability.name): capability
        for capability in profile.capabilities
        if capability.page_id is not None
    }
    checks_by_page = {
        page.page_id: [
            check for check in plan.checks if check.page_id == page.page_id
        ]
        for page in pages
    }
    check_results = []

    for viewport in viewport_names:
        set_current_viewport(viewport)
        playwright = browser = browser_context = browser_page = None
        browser_error = None
        has_supported_ready = any(
            check.should_execute and execution_registry.supports(check.executor)
            for checks in checks_by_page.values()
            for check in checks
        )
        if has_supported_ready:
            try:
                playwright, browser, browser_context, browser_page = (
                    browser_factory(site_config)
                )
            except Exception as error:
                browser_error = error

        try:
            for page_profile in pages:
                page_checks = checks_by_page[page_profile.page_id]
                navigation_error = browser_error
                navigation_status = None
                if browser_page is not None and any(
                    check.should_execute
                    and execution_registry.supports(check.executor)
                    for check in page_checks
                ):
                    try:
                        response = browser_page.goto(
                            page_profile.url,
                            wait_until="domcontentloaded",
                            timeout=45000,
                        )
                        navigation_status = (
                            int(response.status) if response is not None else None
                        )
                        browser_page.locator("body").first.wait_for(
                            state="attached",
                            timeout=timeout_ms,
                        )
                    except Exception as error:
                        navigation_error = error

                for planned_check in page_checks:
                    capability = capability_index.get(
                        (page_profile.page_id, planned_check.capability)
                    )
                    artifacts = ArtifactContext.for_page(
                        root,
                        profile.site_identity.site_id,
                        viewport,
                        page_profile.page_id,
                    )
                    context = ExecutorContext(
                        run_id=run_id,
                        site_profile=profile,
                        page_profile=page_profile,
                        planned_check=planned_check,
                        page=browser_page,
                        browser_context=browser_context,
                        runtime_policy=runtime_policy,
                        interaction_policy=interaction_policy,
                        artifact_context=artifacts,
                        target=planned_check.capability,
                        selector_hint=(
                            capability.selector_hint if capability else None
                        ),
                        timeout_ms=timeout_ms,
                        retry_policy=retry_policy,
                        metadata={
                            "viewport": viewport,
                            "navigation_status": navigation_status,
                            "expected_minimum_count": _expected_minimum_count(
                                site_config,
                                page_profile,
                            ),
                        },
                    )
                    if (
                        navigation_error is not None
                        and planned_check.should_execute
                        and execution_registry.supports(planned_check.executor)
                    ):
                        check_results.append(
                            terminal_result(
                                context,
                                ExecutionStatus.ERROR,
                                HealthStatus.UNVERIFIED,
                                executor_version="navigation-adapter",
                                reason="shadow_navigation_error",
                                error={
                                    "code": "navigation_error",
                                    "message": redact_text(
                                        f"{type(navigation_error).__name__}: "
                                        f"{navigation_error}"
                                    ),
                                },
                            )
                        )
                    else:
                        check_results.append(engine.execute(context))
        finally:
            browser_closer(playwright, browser, browser_context)

    adapter = CheckResultObservationAdapter()
    observations = adapter.adapt_many(check_results)
    shadow_health_config = copy.deepcopy(health_config)
    shadow_health_config.setdefault("ai", {})["enabled"] = False
    shadow_health = HealthEngine(
        observations,
        site_config=site_config,
        config=shadow_health_config,
    ).build()
    shadow_health_summary = {
        "status": shadow_health.status.value,
        "overall_health": shadow_health.overall_health.value,
        "page_count": len(shadow_health.pages),
        "finding_count": len(shadow_health.findings),
        "alert": shadow_health.alert.alert_type,
        "ai_invoked": shadow_health.ai_analysis.invoked,
    }
    comparison = ShadowComparisonBuilder(
        maturity_policy=maturity_policy
    ).build(
        run_id,
        profile.site_identity.site_id,
        legacy_results,
        plan,
        check_results,
        shadow_health_summary=shadow_health_summary,
    )

    result_path = root / "shadow-check-results.json"
    observation_path = root / "shadow-observations.json"
    comparison_path = root / "shadow-comparison.json"
    history_summary_path = root / "shadow-history-summary.json"
    history_path = None
    history_summary = None
    history_error = None
    history_config = shadow_config.get("history") or {}
    if bool(history_config.get("enabled", True)):
        configured_history_root = history_config.get("root", "history")
        selected_history_root = history_root
        if selected_history_root is None:
            configured_path = Path(str(configured_history_root))
            selected_history_root = (
                configured_path
                if configured_path.is_absolute()
                else artifact_base.parent / configured_path
            )
        try:
            history_path, _history_record, history_summary = (
                record_shadow_history(
                    comparison,
                    selected_page_ids,
                    viewport_names,
                    policy=maturity_policy,
                    history_root=selected_history_root,
                    scheduler=(
                        scheduler
                        or os.environ.get("HEALTH_SCHEDULER")
                        or "MANUAL"
                    ),
                    scheduler_metadata=(
                        scheduler_metadata
                        or {
                            "trigger": os.environ.get("HEALTH_TRIGGER"),
                            "mode": os.environ.get(
                                "HEALTH_RUNTIME_MODE",
                                "MONITOR",
                            ),
                        }
                    ),
                    legacy_gate_failed=legacy_gate_failed,
                )
            )
            atomic_write_json(history_summary_path, history_summary)
        except Exception as error:
            history_error = redact_text(
                f"{type(error).__name__}: {error}"
            )
            history_summary = {
                "schema_version": "1.0",
                "site_id": profile.site_identity.site_id,
                "error": history_error,
                "fail_open": True,
            }
            atomic_write_json(history_summary_path, history_summary)
    else:
        history_summary = {
            "schema_version": "1.0",
            "site_id": profile.site_identity.site_id,
            "enabled": False,
        }
        atomic_write_json(history_summary_path, history_summary)
    statuses = Counter(
        result.execution_status.value for result in check_results
    )
    atomic_write_json(
        result_path,
        {
            "schema_version": SHADOW_RESULT_SCHEMA_VERSION,
            "run_id": run_id,
            "site_id": profile.site_identity.site_id,
            "shadow_only": True,
            "summary": {
                "result_count": len(check_results),
                "execution_status_counts": dict(statuses),
            },
            "results": [result.to_dict() for result in check_results],
        },
    )
    atomic_write_json(
        observation_path,
        {
            "schema_version": SHADOW_RESULT_SCHEMA_VERSION,
            "run_id": run_id,
            "site_id": profile.site_identity.site_id,
            "shadow_only": True,
            "health_engine_summary": shadow_health_summary,
            "observations": observations,
        },
    )
    atomic_write_json(comparison_path, comparison.to_dict())
    return ShadowRunArtifacts(
        enabled=True,
        check_results_path=str(result_path.resolve()),
        observations_path=str(observation_path.resolve()),
        comparison_path=str(comparison_path.resolve()),
        history_path=(
            str(Path(history_path).resolve()) if history_path else None
        ),
        history_summary_path=str(history_summary_path.resolve()),
        check_results=check_results,
        observations=observations,
        comparison=comparison,
        history_summary=history_summary,
        history_error=history_error,
    )


def _positive_int(value, default):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return int(default)
    return normalized if normalized > 0 else int(default)


def _non_negative_int(value, default):
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return int(default)
    return normalized if normalized >= 0 else int(default)


def _expected_minimum_count(site_config, page_profile):
    config_key = page_profile.metadata.get("config_key") or page_profile.page_id
    page_config = ((site_config.get("pages") or {}).get(config_key) or {})
    layout = ((page_config.get("layout_checks") or {}).get("product_grid") or {})
    return _positive_int(layout.get("minimum_count", 1), 1)
