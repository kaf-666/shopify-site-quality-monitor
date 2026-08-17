import copy
import inspect
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright_checks.health.capability_registry import CapabilityCheckRegistry
from playwright_checks.health.execution_models import ExecutionStatus, RuntimePolicy
from playwright_checks.health.executor_engine import ExecutorEngine
from playwright_checks.health.interaction_policy import InteractionPolicy
from playwright_checks.health.models import SideEffectLevel
from playwright_checks.health.planner import PlanDisposition, TestPlanner
from playwright_checks.health.profile_adapter import LegacySiteConfigAdapter
from playwright_checks.health.shadow_comparison import (
    MappingMatch,
    ShadowComparisonBuilder,
)
from playwright_checks.health.shadow_history import (
    ShadowHistoryStore,
    record_shadow_history,
)
from playwright_checks.health.shadow_maturity import ShadowMaturityPolicy
from playwright_checks.health.shadow_runtime import (
    ShadowRunArtifacts,
    run_shadow_pipeline_fail_open,
    shadow_executor_enabled,
)
from playwright_checks.tests.test_executor_runtime_phase3 import (
    FakeLocator,
    FakePage,
    Phase3ContractTestCase,
)
from playwright_checks.tests.test_site_profile_registry import FIXTURE_SITE


class AddToCartSplitTests(Phase3ContractTestCase):
    # 1
    def test_add_to_cart_control_health_is_safe(self):
        entry = _registry_entry("commerce.add_to_cart.control_health")
        self.assertEqual(SideEffectLevel.SAFE, entry.interaction_policy)

    # 2
    def test_control_health_never_clicks_real_add_to_cart(self):
        locator = FakeLocator(text="Add to cart")
        result = ExecutorEngine().execute(
            self.context(
                check_id="commerce.add_to_cart.control_health",
                page=FakePage(locator),
            )
        )
        self.assertEqual(ExecutionStatus.COMPLETED, result.execution_status)
        self.assertEqual(0, locator.trial_clicks)
        self.assertFalse(result.actual["click_dispatched"])

    # 3
    def test_add_to_cart_action_health_is_transactional_safe(self):
        entry = _registry_entry("commerce.add_to_cart.action_health")
        self.assertEqual(
            SideEffectLevel.TRANSACTIONAL_SAFE,
            entry.interaction_policy,
        )

    # 4
    def test_transactional_false_runtime_blocks_action(self):
        result = ExecutorEngine().execute(
            self.context(
                check_id="commerce.add_to_cart.action_health",
                planned_override={
                    "disposition": PlanDisposition.READY,
                    "should_execute": True,
                    "interaction_allowed": True,
                    "reason": "explicit_fixture_opt_in",
                },
                runtime_policy=RuntimePolicy.monitor(
                    transactional_safe_enabled=False
                ),
            )
        )
        self.assertEqual(ExecutionStatus.POLICY_BLOCKED, result.execution_status)

    # 5
    def test_control_check_still_executes_when_actions_are_blocked(self):
        result = ExecutorEngine().execute(
            self.context(check_id="commerce.add_to_cart.control_health")
        )
        self.assertEqual(ExecutionStatus.COMPLETED, result.execution_status)

    # 6
    def test_high_risk_capability_cannot_be_downgraded_by_override(self):
        policy = InteractionPolicy(
            {"capability_overrides": {"buy_now": "SAFE"}}
        )
        self.assertEqual(
            SideEffectLevel.HIGH_RISK,
            policy.level_for("buy_now"),
        )


class CoverageMatrixTests(Phase3ContractTestCase):
    def _comparison(self, legacy, results):
        return ShadowComparisonBuilder().build(
            "coverage-run",
            self.profile.site_identity.site_id,
            legacy,
            TestPlanner().build_plan(self.profile),
            results,
        )

    # 7
    def test_exact_mapping_counts_in_coverage(self):
        result = ExecutorEngine().execute(
            self.context(check_id="home.core_modules.health")
        )
        comparison = self._comparison(
            [_legacy("home", "dom_modules")],
            [result],
        )
        self.assertEqual(100.0, comparison.overall_coverage_percent)
        self.assertEqual(1, comparison.exact_mapping_count)
        self.assertEqual(
            MappingMatch.EXACT.value,
            comparison.coverage_matrix[0]["mapping_status"],
        )

    # 8
    def test_partial_mapping_is_explicit(self):
        results = [
            ExecutorEngine().execute(
                self.context(check_id="pdp.product_title.presence")
            ),
            ExecutorEngine().execute(
                self.context(check_id="pdp.product_price.presence")
            ),
        ]
        comparison = self._comparison(
            [_legacy("product", "product_content")],
            results,
        )
        self.assertGreaterEqual(comparison.partial_mapping_count, 2)
        self.assertTrue(
            all(
                row["mapping_status"] == MappingMatch.PARTIAL.value
                for row in comparison.coverage_matrix
            )
        )

    # 9
    def test_missing_mapping_is_not_disguised_as_mapped(self):
        comparison = self._comparison(
            [_legacy("home", "future_missing_case")],
            [],
        )
        self.assertEqual(0, comparison.mapped_legacy_check_count)
        self.assertEqual(1, comparison.missing_in_new_count)
        self.assertEqual(
            MappingMatch.MISSING.value,
            comparison.coverage_matrix[0]["mapping_status"],
        )

    # 10
    def test_unsupported_is_not_successful_executable_coverage(self):
        unsupported = ExecutorEngine().execute(
            self.context(
                check_id="pdp.product_title.presence",
                planned_override={"executor": "future.unsupported"},
            )
        )
        price = ExecutorEngine().execute(
            self.context(check_id="pdp.product_price.presence")
        )
        comparison = self._comparison(
            [_legacy("product", "product_content")],
            [unsupported, price],
        )
        self.assertEqual(100.0, comparison.overall_coverage_percent)
        self.assertEqual(0.0, comparison.executable_coverage_percent)
        self.assertEqual(1, comparison.unsupported_executor_count)

    # 11
    def test_critical_coverage_is_independent(self):
        result = ExecutorEngine().execute(
            self.context(check_id="home.core_modules.health")
        )
        comparison = self._comparison(
            [_legacy("home", "dom_modules")],
            [result],
        )
        self.assertEqual(100.0, comparison.overall_coverage_percent)
        self.assertLess(comparison.critical_coverage_percent, 100.0)
        self.assertLess(
            comparison.critical_executable_coverage_percent,
            100.0,
        )


class ShadowHistoryStabilityTests(Phase3ContractTestCase):
    def setUp(self):
        super().setUp()
        self.history_root = self.root / "history"
        self.policy = ShadowMaturityPolicy()

    def _mature_comparison(self, run_id):
        results = [
            ExecutorEngine().execute(
                self.context(check_id="pdp.product_title.presence")
            ),
            ExecutorEngine().execute(
                self.context(check_id="pdp.product_price.presence")
            ),
        ]
        comparison = ShadowComparisonBuilder().build(
            run_id,
            self.profile.site_identity.site_id,
            [_legacy("product", "product_content")],
            TestPlanner().build_plan(self.profile),
            results,
        )
        comparison.overall_coverage_percent = 100.0
        comparison.mapping_coverage_percent = 100.0
        comparison.executable_coverage_percent = 100.0
        comparison.critical_coverage_percent = 100.0
        comparison.critical_executable_coverage_percent = 100.0
        comparison.result_parity_percent = 100.0
        comparison.result_parity_sample_count = 1
        comparison.evidence_parity_percent = 100.0
        comparison.evidence_parity_sample_count = 1
        comparison.policy_regression_count = 0
        comparison.executor_error_count = 0
        comparison.executor_timeout_count = 0
        comparison.mapping_fingerprint = "stable-fixture-map"
        return comparison

    def _record(self, run_id, **kwargs):
        comparison = self._mature_comparison(run_id)
        for name, value in kwargs.pop("comparison_values", {}).items():
            setattr(comparison, name, value)
        path, record, summary = record_shadow_history(
            comparison,
            ["product"],
            ["desktop"],
            policy=self.policy,
            history_root=self.history_root,
            scheduler=kwargs.pop("scheduler", "MANUAL"),
            legacy_gate_failed=kwargs.pop("legacy_gate_failed", False),
            **kwargs,
        )
        return comparison, path, record, summary

    # 12
    def test_shadow_history_append_and_round_trip(self):
        _, path, record, _ = self._record("history-1")
        values = ShadowHistoryStore(self.history_root).read("fixture_US")
        self.assertTrue(path.is_file())
        self.assertEqual(record, values[-1])
        encoded = path.read_text(encoding="utf-8")
        self.assertNotIn("screenshot", encoded)
        self.assertNotIn("trace", encoded)

    # 13
    def test_last_five_window_is_correct(self):
        summary = None
        for index in range(7):
            _, _, _, summary = self._record(f"last5-{index}")
        self.assertEqual(5, len(summary["last_5_runs"]))
        self.assertEqual("last5-2", summary["last_5_runs"][0]["run_id"])

    # 14
    def test_last_ten_window_is_correct(self):
        summary = None
        for index in range(12):
            _, _, _, summary = self._record(f"last10-{index}")
        self.assertEqual(10, len(summary["last_10_runs"]))
        self.assertEqual("last10-2", summary["last_10_runs"][0]["run_id"])

    # 15
    def test_consecutive_stable_runs_are_counted(self):
        summary = None
        for index in range(3):
            _, _, _, summary = self._record(f"stable-{index}")
        self.assertEqual(3, summary["consecutive_stable_runs"])

    # 16
    def test_policy_regression_breaks_stable_streak(self):
        self._record("policy-stable")
        _, _, record, summary = self._record(
            "policy-break",
            comparison_values={"policy_regression_count": 1},
        )
        self.assertFalse(record["stable"])
        self.assertEqual(0, summary["consecutive_stable_runs"])

    # 17
    def test_executor_error_breaks_stable_streak(self):
        self._record("error-stable")
        _, _, record, summary = self._record(
            "error-break",
            comparison_values={"executor_error_count": 1},
        )
        self.assertFalse(record["stable"])
        self.assertEqual(0, summary["consecutive_stable_runs"])

    def test_mapping_change_breaks_stable_streak(self):
        self._record("mapping-stable")
        comparison, _, record, summary = self._record(
            "mapping-break",
            comparison_values={"mapping_fingerprint": "changed-map"},
        )
        self.assertFalse(record["mapping_consistent"])
        self.assertFalse(record["stable"])
        self.assertEqual(0, summary["consecutive_stable_runs"])
        self.assertEqual("SHADOW_NOT_READY", comparison.maturity_stage.value)

    # 18
    def test_legacy_failure_does_not_break_shadow_stability(self):
        _, _, record, summary = self._record(
            "legacy-failed",
            legacy_gate_failed=True,
        )
        self.assertTrue(record["legacy_gate_failed"])
        self.assertTrue(record["stable"])
        self.assertEqual(1, summary["consecutive_stable_runs"])


class SchedulerNeutralHistoryTests(ShadowHistoryStabilityTests):
    # 19
    def test_manual_history_result_is_normal(self):
        _, _, record, _ = self._record("manual", scheduler="MANUAL")
        self.assertEqual("MANUAL", record["scheduler"])
        self.assertTrue(record["stable"])

    # 20
    def test_codex_history_result_is_normal(self):
        _, _, record, _ = self._record("codex", scheduler="CODEX")
        self.assertEqual("CODEX", record["scheduler"])
        self.assertTrue(record["stable"])

    # 21
    def test_hermes_history_result_is_normal(self):
        _, _, record, _ = self._record("hermes", scheduler="HERMES")
        self.assertEqual("HERMES", record["scheduler"])
        self.assertTrue(record["stable"])

    # 22
    def test_scheduler_does_not_change_stability_algorithm(self):
        summary = None
        for scheduler in ("MANUAL", "CODEX", "HERMES"):
            _, _, _, summary = self._record(
                f"scheduler-{scheduler.lower()}",
                scheduler=scheduler,
            )
        self.assertEqual(3, summary["consecutive_stable_runs"])
        self.assertEqual(3, summary["last_5"]["stable_count"])


class BackwardsCompatibilityTests(unittest.TestCase):
    # 23
    def test_legacy_runner_page_order_remains_before_shadow(self):
        from playwright_checks.runner import main as runner

        source = inspect.getsource(runner.run_all)
        self.assertLess(source.index("run_page("), source.index("run_shadow_pipeline"))

    # 24
    def test_legacy_exit_code_is_not_changed_by_shadow_success(self):
        from playwright_checks.runner import main as runner

        fake_page = ("Home", "home", lambda: ["legacy failure"])
        with (
            patch.object(runner, "clear_results"),
            patch.object(runner, "get_run_viewport_names", return_value=["desktop"]),
            patch.object(runner, "get_run_pages", return_value=(fake_page,)),
            patch.object(runner, "set_current_viewport"),
            patch.object(runner, "load_site_config", return_value=FIXTURE_SITE),
            patch.object(
                runner,
                "run_shadow_pipeline_fail_open",
                return_value=ShadowRunArtifacts(enabled=False),
            ),
            patch.object(runner, "write_results", return_value="<memory>"),
            patch.object(runner, "get_results", return_value=[]),
            patch.object(
                runner,
                "write_health_reports_fail_open",
                return_value={"json": None},
            ),
        ):
            self.assertEqual(1, runner.run_all())

    # 25
    def test_visual_baseline_mutation_remains_disabled_in_monitor(self):
        policy = RuntimePolicy.monitor()
        self.assertFalse(policy.baseline_update_enabled)
        self.assertFalse(policy.selector_rewrite_enabled)

    # 26
    def test_all_six_site_configs_remain_compatible(self):
        from playwright_checks.core.config_loader import load_site_config

        names = sorted(path.stem for path in Path("configs/sites").glob("*.yaml"))
        self.assertEqual(6, len(names))
        for name in names:
            profile = LegacySiteConfigAdapter(load_site_config(name)).build()
            self.assertTrue(profile.validate())

    # 27
    def test_shadow_executor_remains_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(shadow_executor_enabled(copy.deepcopy(FIXTURE_SITE)))

    # 28
    def test_shadow_pipeline_remains_fail_open(self):
        with patch(
            "playwright_checks.health.shadow_runtime.run_shadow_pipeline",
            side_effect=RuntimeError("fixture failure"),
        ):
            artifacts = run_shadow_pipeline_fail_open([], FIXTURE_SITE, [])
        self.assertTrue(artifacts.enabled)
        self.assertIn("fixture failure", artifacts.error)


def _registry_entry(check_id):
    return next(
        entry
        for entry in CapabilityCheckRegistry().entries
        if entry.check_id == check_id
    )


def _legacy(page, case, status="passed", result_type="deterministic_check"):
    return {
        "result_type": result_type,
        "site": "fixture_US",
        "viewport": "desktop",
        "page": page,
        "case": case,
        "status": status,
    }


if __name__ == "__main__":
    unittest.main()
