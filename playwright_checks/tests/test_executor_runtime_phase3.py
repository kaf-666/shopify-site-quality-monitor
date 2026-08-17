import copy
import io
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout

import run_all

from playwright_checks.health.capability_registry import CapabilityCheckRegistry
from playwright_checks.health.check_result_adapter import (
    CheckResultObservationAdapter,
)
from playwright_checks.health.engine import HealthEngine
from playwright_checks.health.execution_models import (
    ArtifactContext,
    CheckResult,
    ExecutionStatus,
    ExecutorContext,
    RetryPolicy,
    RuntimeMode,
    RuntimePolicy,
)
from playwright_checks.health.executor_engine import ExecutorEngine
from playwright_checks.health.executor_registry import (
    ExecutorDefinition,
    ExecutorRegistry,
    ExecutorRegistryValidationError,
)
from playwright_checks.health.interaction_policy import InteractionPolicy
from playwright_checks.health.models import HealthStatus
from playwright_checks.health.planner import PlanDisposition, TestPlanner
from playwright_checks.health.profile_adapter import LegacySiteConfigAdapter
from playwright_checks.health.shadow_comparison import (
    MigrationReadiness,
    ShadowComparisonBuilder,
)
from playwright_checks.health.shadow_runtime import ShadowRunArtifacts
from playwright_checks.health.shadow_runtime import run_shadow_pipeline
from playwright_checks.runtime.run_manifest import (
    RunLifecycleStatus,
    RunManifest,
    RunManifestStore,
    SchedulerType,
    TriggerType,
    build_run_manifest,
)
from playwright_checks.runtime.run_summary import (
    build_machine_run_summary,
    stdout_contract,
    write_machine_run_summary,
)
from playwright_checks.tests.test_site_profile_registry import FIXTURE_SITE


class FakeLocator:
    def __init__(
        self,
        count=1,
        visible=True,
        enabled=True,
        text="Observed content",
        error=None,
        visible_locator=None,
    ):
        self.count_value = count
        self.visible = visible
        self.enabled = enabled
        self.text = text
        self.error = error
        self.visible_locator = visible_locator
        self.trial_clicks = 0

    @property
    def first(self):
        return self

    def count(self):
        if self.error:
            raise self.error
        return self.count_value

    def filter(self, visible=None):
        if visible is True:
            if self.visible_locator is not None:
                return self.visible_locator
            if not self.visible:
                return FakeLocator(count=0, visible=False, text="")
        return self

    def is_visible(self):
        if self.error:
            raise self.error
        return self.visible

    def is_enabled(self):
        if self.error:
            raise self.error
        return self.enabled

    def inner_text(self, timeout=None):
        if self.error:
            raise self.error
        return self.text

    def click(self, trial=False, timeout=None):
        if self.error:
            raise self.error
        if trial:
            self.trial_clicks += 1

    def wait_for(self, state=None, timeout=None):
        if self.error:
            raise self.error


class FakePage:
    def __init__(self, locator=None, url="https://fixture.example/products/example"):
        self.value = locator or FakeLocator()
        self.url = url

    def locator(self, selector):
        return self.value

    def goto(self, url, wait_until=None, timeout=None):
        self.url = url
        return SimpleNamespace(status=200)


def build_profile():
    return LegacySiteConfigAdapter(copy.deepcopy(FIXTURE_SITE)).build(
        generated_at="2026-08-14T00:00:00.000+00:00"
    )


def planned(profile, check_id):
    return next(
        check
        for check in TestPlanner().build_plan(profile).checks
        if check.check_id == check_id
    )


class Phase3ContractTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = build_profile()

    def context(
        self,
        check_id="pdp.product_title.presence",
        page=None,
        selector=None,
        planned_override=None,
        runtime_policy=None,
    ):
        check = planned(self.profile, check_id)
        if planned_override:
            check = replace(check, **planned_override)
        page_profile = self.profile.page(check.page_id)
        capability = next(
            value
            for value in self.profile.page_capabilities(check.page_id)
            if value.name == check.capability
        )
        return ExecutorContext(
            run_id="phase3-test-run",
            site_profile=self.profile,
            page_profile=page_profile,
            planned_check=check,
            page=page or FakePage(),
            browser_context=object(),
            runtime_policy=runtime_policy or RuntimePolicy.monitor(),
            interaction_policy=InteractionPolicy(
                self.profile.interaction_policy.to_config()
            ),
            artifact_context=ArtifactContext.for_page(
                self.root,
                self.profile.site_identity.site_id,
                "desktop",
                page_profile.page_id,
            ),
            target=check.capability,
            selector_hint=(
                capability.selector_hint if selector is None else selector
            ),
            timeout_ms=1000,
            retry_policy=RetryPolicy(),
            metadata={"viewport": "desktop", "navigation_status": 200},
        )


class ExecutorRegistryTests(Phase3ContractTestCase):
    def test_executor_keys_are_unique(self):
        registry = ExecutorRegistry()
        keys = [entry.executor_key for entry in registry.entries]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertGreaterEqual(len(keys), 5)

    def test_duplicate_executor_key_is_rejected(self):
        entry = ExecutorRegistry().entries[0]
        with self.assertRaisesRegex(
            ExecutorRegistryValidationError,
            "Duplicate executor_key",
        ):
            ExecutorRegistry([entry, entry])

    def test_missing_executor_callable_is_rejected(self):
        entry = ExecutorRegistry().entries[0]
        with self.assertRaisesRegex(
            ExecutorRegistryValidationError,
            "missing callable",
        ):
            ExecutorRegistry([replace(entry, executor=None)])

    def test_unknown_executor_returns_unsupported_without_crash(self):
        context = self.context(
            planned_override={"executor": "future.unknown_executor"}
        )
        result = ExecutorEngine().execute(context)
        self.assertEqual(ExecutionStatus.UNSUPPORTED, result.execution_status)
        self.assertEqual(HealthStatus.UNVERIFIED, result.health_status)

    def test_multiple_checks_reuse_one_generic_executor(self):
        registry = CapabilityCheckRegistry()
        entries = {
            entry.check_id: entry for entry in registry.entries
        }
        self.assertEqual(
            "content.text_present",
            entries["pdp.product_title.presence"].executor,
        )
        self.assertEqual(
            entries["pdp.product_title.presence"].executor,
            entries["pdp.product_price.presence"].executor,
        )


class ExecutorContractTests(Phase3ContractTestCase):
    def test_executor_context_is_constructible_and_valid(self):
        context = self.context()
        self.assertIs(context, context.validate())
        self.assertEqual("product_title", context.target)

    def test_executor_returns_standard_check_result(self):
        result = ExecutorEngine().execute(self.context())
        self.assertIsInstance(result, CheckResult)
        self.assertEqual(ExecutionStatus.COMPLETED, result.execution_status)
        self.assertEqual(HealthStatus.PASS, result.health_status)

    def test_content_executor_uses_visible_match_from_selector_union(self):
        visible_price = FakeLocator(count=2, visible=True, text="$99.00")
        selector_union = FakeLocator(
            count=849,
            visible=False,
            text="",
            visible_locator=visible_price,
        )
        result = ExecutorEngine().execute(
            self.context(page=FakePage(selector_union))
        )
        self.assertEqual(ExecutionStatus.COMPLETED, result.execution_status)
        self.assertEqual(HealthStatus.PASS, result.health_status)
        self.assertEqual(849, result.actual["count"])
        self.assertEqual(2, result.actual["visible_count"])
        self.assertTrue(result.evidence)

    def test_completed_fail_represents_confirmed_measurement(self):
        result = ExecutorEngine().execute(
            self.context(page=FakePage(FakeLocator(text="")))
        )
        self.assertEqual(ExecutionStatus.COMPLETED, result.execution_status)
        self.assertEqual(HealthStatus.FAIL, result.health_status)

    def test_timeout_converts_to_timeout_unverified(self):
        def timeout_executor(context):
            raise TimeoutError("fixture timeout")

        definition = ExecutorDefinition(
            "test.timeout",
            "1.0",
            timeout_executor,
            "Fixture timeout executor.",
        )
        context = self.context(planned_override={"executor": "test.timeout"})
        result = ExecutorEngine(ExecutorRegistry([definition])).execute(context)
        self.assertEqual(ExecutionStatus.TIMEOUT, result.execution_status)
        self.assertEqual(HealthStatus.UNVERIFIED, result.health_status)

    def test_exception_converts_to_error_unverified(self):
        def broken_executor(context):
            raise RuntimeError("fixture executor failure")

        definition = ExecutorDefinition(
            "test.error",
            "1.0",
            broken_executor,
            "Fixture error executor.",
        )
        context = self.context(planned_override={"executor": "test.error"})
        result = ExecutorEngine(ExecutorRegistry([definition])).execute(context)
        self.assertEqual(ExecutionStatus.ERROR, result.execution_status)
        self.assertEqual(HealthStatus.UNVERIFIED, result.health_status)

    def test_missing_selector_is_error_not_site_fail(self):
        context = self.context(selector=[])
        result = ExecutorEngine().execute(context)
        self.assertEqual(ExecutionStatus.ERROR, result.execution_status)
        self.assertEqual(HealthStatus.UNVERIFIED, result.health_status)
        self.assertEqual("executor_input_error", result.error["code"])

    def test_policy_blocked_is_unverified(self):
        context = self.context(
            planned_override={
                "disposition": PlanDisposition.POLICY_BLOCKED,
                "should_execute": False,
                "interaction_allowed": False,
                "reason": "fixture_policy_block",
            }
        )
        result = ExecutorEngine().execute(context)
        self.assertEqual(ExecutionStatus.POLICY_BLOCKED, result.execution_status)
        self.assertEqual(HealthStatus.UNVERIFIED, result.health_status)

    def test_not_applicable_is_skipped(self):
        context = self.context(
            planned_override={
                "disposition": PlanDisposition.NOT_APPLICABLE,
                "should_execute": False,
                "status": HealthStatus.NOT_APPLICABLE,
                "reason": "capability_not_applicable",
            }
        )
        result = ExecutorEngine().execute(context)
        self.assertEqual(ExecutionStatus.SKIPPED, result.execution_status)
        self.assertEqual(HealthStatus.NOT_APPLICABLE, result.health_status)

    def test_high_risk_monitor_policy_cannot_be_enabled(self):
        with self.assertRaisesRegex(ValueError, "MONITOR safety guard"):
            RuntimePolicy(high_risk_enabled=True).validate()

    def test_check_result_json_round_trip(self):
        result = ExecutorEngine().execute(self.context())
        restored = CheckResult.from_dict(
            json.loads(json.dumps(result.to_dict()))
        )
        self.assertEqual(result.to_dict(), restored.to_dict())


class ObservationAdapterTests(Phase3ContractTestCase):
    def test_check_result_converts_to_existing_observation(self):
        result = ExecutorEngine().execute(self.context())
        observation = CheckResultObservationAdapter().adapt(result)
        self.assertEqual("deterministic_check", observation["result_type"])
        self.assertEqual("passed", observation["status"])
        self.assertFalse(observation["affects_exit_code"])

    def test_structured_evidence_is_preserved(self):
        result = ExecutorEngine().execute(self.context())
        observation = CheckResultObservationAdapter().adapt(result)
        self.assertEqual(
            {item.evidence_id for item in result.evidence},
            {item["evidence_id"] for item in observation["evidence"]},
        )

    def test_health_engine_consumes_adapted_observation(self):
        result = ExecutorEngine().execute(
            self.context(page=FakePage(FakeLocator(text="")))
        )
        observation = CheckResultObservationAdapter().adapt(result)
        report = HealthEngine(
            [observation],
            site_config=copy.deepcopy(FIXTURE_SITE),
        ).build()
        self.assertTrue(report.pages)
        self.assertTrue(report.findings)
        evidence_ids = {
            item.evidence_id
            for finding in report.findings
            for item in finding.evidence
        }
        self.assertTrue(
            {item.evidence_id for item in result.evidence}.issubset(evidence_ids)
        )


class ShadowComparisonTests(Phase3ContractTestCase):
    def _result(self, check_id):
        return ExecutorEngine().execute(self.context(check_id=check_id))

    def test_explicit_legacy_mapping_and_coverage_are_computed(self):
        plan = TestPlanner().build_plan(self.profile)
        results = [
            self._result("pdp.product_title.presence"),
            self._result("pdp.product_price.presence"),
            self._result("plp.pagination.health"),
        ]
        legacy = [
            {
                "result_type": "deterministic_check",
                "site": "fixture_US",
                "viewport": "desktop",
                "page": "product",
                "case": "product_content",
                "status": "passed",
            },
            {
                "result_type": "deterministic_check",
                "site": "fixture_US",
                "viewport": "desktop",
                "page": "collection",
                "case": "pagination",
                "status": "passed",
            },
        ]
        comparison = ShadowComparisonBuilder().build(
            "run",
            "fixture_US",
            legacy,
            plan,
            results,
        )
        self.assertEqual(100.0, comparison.overall_coverage_percent)
        self.assertEqual(2, comparison.mapped_legacy_check_count)
        self.assertEqual(0, comparison.exact_mapping_count)
        self.assertGreaterEqual(comparison.partial_mapping_count, 2)

    def test_result_and_evidence_parity_are_computed(self):
        plan = TestPlanner().build_plan(self.profile)
        results = [
            self._result("pdp.product_title.presence"),
            self._result("pdp.product_price.presence"),
        ]
        legacy = [
            {
                "result_type": "deterministic_check",
                "viewport": "desktop",
                "page": "product",
                "case": "product_content",
                "status": "passed",
            }
        ]
        comparison = ShadowComparisonBuilder().build(
            "run", "fixture_US", legacy, plan, results
        )
        self.assertEqual(100.0, comparison.result_parity_percent)
        self.assertEqual(100.0, comparison.evidence_parity_percent)
        self.assertEqual(MigrationReadiness.SHADOW, comparison.migration_status)

    def test_missing_mapping_does_not_crash(self):
        comparison = ShadowComparisonBuilder().build(
            "run",
            "fixture_US",
            [
                {
                    "result_type": "deterministic_check",
                    "viewport": "desktop",
                    "page": "home",
                    "case": "future_legacy_check",
                    "status": "passed",
                }
            ],
            TestPlanner().build_plan(self.profile),
            [],
        )
        self.assertEqual(1, comparison.missing_in_new_count)
        self.assertEqual(0.0, comparison.overall_coverage_percent)

    def test_shadow_failure_does_not_change_legacy_gate(self):
        fake_page = ("Home", "home", lambda: [])
        from playwright_checks.runner import main as runner

        with (
            patch.object(runner, "clear_results"),
            patch.object(runner, "get_run_viewport_names", return_value=["desktop"]),
            patch.object(runner, "get_run_pages", return_value=(fake_page,)),
            patch.object(runner, "set_current_viewport"),
            patch.object(runner, "load_site_config", return_value=FIXTURE_SITE),
            patch.object(
                runner,
                "run_shadow_pipeline_fail_open",
                return_value=ShadowRunArtifacts(
                    enabled=True,
                    error="fixture shadow failure",
                ),
            ),
            patch.object(runner, "write_results", return_value="<memory>"),
            patch.object(runner, "get_results", return_value=[]),
            patch.object(
                runner,
                "write_health_reports_fail_open",
                return_value={"json": None},
            ),
        ):
            self.assertEqual(0, runner.run_all())

    def test_shadow_pipeline_smoke_writes_sidecar_contracts(self):
        page = FakePage()
        closed = []

        def browser_factory(site_config):
            return object(), object(), object(), page

        def browser_closer(playwright, browser, context):
            closed.append(True)

        legacy = [
            {
                "result_type": "deterministic_check",
                "site": "fixture_US",
                "run_id": "shadow-smoke",
                "viewport": "desktop",
                "page": "product",
                "case": "product_content",
                "status": "passed",
            }
        ]
        with patch.dict(
            os.environ,
            {"HEALTH_SHADOW_EXECUTOR_ENABLED": "true"},
            clear=False,
        ):
            artifacts = run_shadow_pipeline(
                legacy,
                copy.deepcopy(FIXTURE_SITE),
                ["desktop"],
                selected_page_ids=["product"],
                run_id="shadow-smoke",
                run_artifact_root=self.root / "artifacts",
                browser_factory=browser_factory,
                browser_closer=browser_closer,
            )
        self.assertTrue(artifacts.enabled)
        self.assertTrue(Path(artifacts.check_results_path).is_file())
        self.assertTrue(Path(artifacts.observations_path).is_file())
        self.assertTrue(Path(artifacts.comparison_path).is_file())
        self.assertTrue(Path(artifacts.history_summary_path).is_file())
        self.assertTrue(Path(artifacts.history_path).is_file())
        self.assertEqual(MigrationReadiness.SHADOW, artifacts.comparison.migration_status)
        self.assertEqual(
            len(artifacts.check_results),
            artifacts.comparison.planned_check_count,
        )
        self.assertEqual(
            {"product"},
            {result.page_id for result in artifacts.check_results},
        )
        self.assertTrue(artifacts.observations)
        self.assertEqual([True], closed)


class SchedulerNeutralRuntimeTests(Phase3ContractTestCase):
    def _manifest(self, scheduler, metadata=None):
        config_path = self.root / "site.yaml"
        config_path.write_text("site: fixture_US\n", encoding="utf-8")
        return build_run_manifest(
            run_id="phase3-manifest",
            site="fixture_US",
            scheduler=scheduler,
            trigger=TriggerType.SCHEDULED,
            mode=RuntimeMode.MONITOR,
            shadow_executor_enabled=True,
            config_path=config_path,
            runtime_metadata=metadata,
        )

    def test_manifest_builds_without_jenkins_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            manifest = self._manifest(SchedulerType.MANUAL)
        self.assertEqual(SchedulerType.MANUAL, manifest.scheduler)
        self.assertFalse(manifest.baseline_update_enabled)

    def test_codex_scheduler_manifest(self):
        manifest = self._manifest(SchedulerType.CODEX)
        self.assertEqual(SchedulerType.CODEX, manifest.scheduler)
        self.assertTrue(manifest.shadow_executor_enabled)

    def test_hermes_scheduler_manifest(self):
        manifest = self._manifest(SchedulerType.HERMES)
        self.assertEqual(SchedulerType.HERMES, manifest.scheduler)
        self.assertEqual(RuntimeMode.MONITOR, manifest.mode)

    def test_scheduler_change_does_not_change_health_policy(self):
        codex = self._manifest(SchedulerType.CODEX)
        hermes = self._manifest(SchedulerType.HERMES)
        fields = (
            "mode",
            "ai_enabled",
            "transactional_safe_enabled",
            "baseline_update_enabled",
            "shadow_executor_enabled",
        )
        self.assertEqual(
            tuple(getattr(codex, name) for name in fields),
            tuple(getattr(hermes, name) for name in fields),
        )

    def test_manifest_serializes_and_deserializes(self):
        manifest = self._manifest(SchedulerType.CODEX)
        restored = RunManifest.from_dict(manifest.to_dict())
        self.assertEqual(manifest.to_dict(), restored.to_dict())

    def test_manifest_drops_secret_metadata(self):
        manifest = self._manifest(
            SchedulerType.CODEX,
            metadata={
                "api_key": "should-not-be-written",
                "nested": {
                    "token": "also-secret",
                    "shopify_signature_agent": "third-secret",
                    "safe": "retained",
                },
            },
        )
        payload = manifest.to_dict()
        encoded = json.dumps(payload)
        self.assertNotIn("should-not-be-written", encoded)
        self.assertNotIn("also-secret", encoded)
        self.assertNotIn("third-secret", encoded)
        self.assertEqual("retained", payload["runtime_metadata"]["nested"]["safe"])

    def test_manifest_store_and_machine_summary_smoke(self):
        manifest = self._manifest(SchedulerType.CODEX)
        store = RunManifestStore(self.root / "artifacts" / manifest.run_id)
        manifest_path = store.write(manifest)
        manifest.finish(0)
        store.write(manifest)
        self.assertEqual(RunLifecycleStatus.COMPLETED, manifest.run_status)
        health_path = self.root / "health-report.json"
        health_path.write_text(
            json.dumps(
                {
                    "overall_health": "HEALTHY",
                    "status": "PASS",
                    "summary": {
                        "blocked_scope_count": 0,
                        "unverified_scope_count": 0,
                        "severity_counts": {},
                    },
                }
            ),
            encoding="utf-8",
        )
        summary = build_machine_run_summary(
            manifest.run_id,
            0,
            health_path,
            manifest_path,
        )
        summary_path = write_machine_run_summary(summary, store.run_root)
        lines = stdout_contract(summary, summary_path)
        self.assertTrue(summary_path.is_file())
        self.assertEqual("HEALTH_RUN_COMPLETE", lines[0])
        self.assertIn("status=HEALTHY", lines)

    def test_reserved_modes_are_modeled_but_not_monitor_policy(self):
        policy = RuntimePolicy(mode=RuntimeMode.DIAGNOSE)
        self.assertEqual(RuntimeMode.DIAGNOSE, policy.validate().mode)

    def test_scheduler_neutral_cli_smoke_without_jenkins_env(self):
        args = run_all.parse_args(
            [
                "--site",
                "mondressy_US",
                "--scheduler",
                "CODEX",
                "--trigger",
                "SCHEDULED",
                "--no-shadow-executor",
            ]
        )
        output = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch(
                "playwright_checks.core.paths.artifact_root",
                return_value=self.root,
            ),
            patch(
                "playwright_checks.core.paths.current_run_id",
                return_value="scheduler-neutral-smoke",
            ),
            patch.object(run_all, "run_stage", return_value=True) as run_stage,
            redirect_stdout(output),
        ):
            run_all.apply_cli_args(args)
            exit_code = run_all.run_scheduler_neutral_runtime(args)
        self.assertEqual(0, exit_code)
        run_stage.assert_called_once_with(
            "Playwright visual regression",
            str(run_all.PROJECT_ROOT),
            "playwright_checks.runner.main",
        )
        self.assertNotIn("JENKINS_URL", os.environ)
        manifest = json.loads(
            (self.root / "scheduler-neutral-smoke" / "run-manifest.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual("CODEX", manifest["scheduler"])
        self.assertIn("HEALTH_RUN_COMPLETE", output.getvalue())


if __name__ == "__main__":
    unittest.main()
