import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from run_all import validate_health_check
from playwright_checks.core.test_results import clear_results, get_results
from playwright_checks.health.ai import AIAnalysis
from playwright_checks.health.capabilities import ConfigCapabilityDetector
from playwright_checks.health.config import DEFAULT_HEALTH_CHECK
from playwright_checks.health.engine import HealthEngine
from playwright_checks.health.interaction_policy import InteractionPolicy
from playwright_checks.health.models import (
    FailureClassification,
    HealthStatus,
    PageType,
    SideEffectLevel,
)
from playwright_checks.health.observations import record_check_observation
from playwright_checks.health.reporting import write_health_reports


SITE_CONFIG = {
    "site": "fixture",
    "pages": {
        "home": {
            "url": "https://fixture.example/",
            "modules": {
                "header_1": ["css", "header"],
                "banner": ["css", ".hero"],
                "cart_btn": ["css", ".cart"],
            },
        },
        "collection": {
            "url": "https://fixture.example/collections/all",
            "modules": {
                "product_grid": ["css", ".grid"],
                "filter": ["css", ".filter"],
                "pagination": ["css", ".pagination"],
            },
            "product_card": ["css", ".card"],
        },
        "product": {
            "url": "https://fixture.example/products/example",
            "modules": {
                "gallery": ["css", ".gallery"],
                "info": ["css", ".info"],
                "add_to_cart": ["css", "button[name='add']"],
            },
            "variant_inputs": ["css", "input[name='Color']"],
            "variant_check": {
                "enabled": True,
                "option_name": "Color",
                "option_value": "Blue",
            },
        },
    },
}


def health_config(ai_enabled=False):
    config = copy.deepcopy(DEFAULT_HEALTH_CHECK)
    config["ai"]["enabled"] = ai_enabled
    config["ai"]["provider"] = "fixture-ai" if ai_enabled else "none"
    return config


def page_summary(**overrides):
    result = {
        "schema_version": "1.1",
        "result_type": "page_summary",
        "site": "fixture",
        "suite": "page_health",
        "run_id": "fixture-run",
        "viewport": "desktop",
        "page": "product",
        "case": "page_summary",
        "status": "passed",
        "overall_status": "passed",
        "runtime_status": "passed",
        "findings": [],
        "recovered_after_retry": False,
        "retry_count": 0,
    }
    result.update(overrides)
    return result


def deterministic_result(status="passed", **overrides):
    result = {
        "schema_version": "1.0",
        "result_type": "deterministic_check",
        "site": "fixture",
        "suite": "health_observation",
        "run_id": "fixture-run",
        "viewport": "desktop",
        "page": "product",
        "case": "product_content",
        "dimension": "dom_content",
        "status": status,
        "affects_exit_code": status == "failed",
        "messages": [],
    }
    result.update(overrides)
    return result


class HealthEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_attempt(self, payload):
        path = self.root / "runtime" / "attempt-1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path.relative_to(self.root).as_posix()

    def build(self, results, config=None, analyzer=None):
        return HealthEngine(
            results,
            site_config=SITE_CONFIG,
            config=config or health_config(),
            ai_analyzer=analyzer,
            project_root=self.root,
        ).build()

    def test_first_party_http_500_is_evidence_backed_and_alertable(self):
        attempt_reference = self.write_attempt(
            {
                "navigation": {
                    "requested_url": SITE_CONFIG["pages"]["product"]["url"],
                    "final_url": SITE_CONFIG["pages"]["product"]["url"],
                    "status": 500,
                },
                "events": [
                    {
                        "event_type": "http_error",
                        "party": "first_party",
                        "status": 500,
                        "url": SITE_CONFIG["pages"]["product"]["url"],
                    }
                ],
            }
        )
        summary = page_summary(
            status="failed",
            overall_status="failed",
            runtime_status="failed",
            runtime_attempt_evidence=attempt_reference,
            findings=[
                {
                    "severity": "error",
                    "reason_code": "first_party_server_error",
                    "message": "A first-party request returned a server error.",
                    "evidence": {"status": 500},
                }
            ],
        )

        report = self.build([summary])

        self.assertEqual(HealthStatus.FAIL, report.status)
        self.assertTrue(report.alert.should_alert)
        finding = report.findings[0]
        self.assertEqual(
            FailureClassification.REAL_SITE_FAILURE,
            finding.classification,
        )
        self.assertEqual("HIGH", finding.evidence_level.value)
        self.assertIn(
            "HTTP",
            {item.evidence_type.value for item in finding.evidence},
        )
        self.assertEqual(
            HealthStatus.FAIL,
            report.pages[0].dimensions["availability"],
        )

    def test_selector_failure_is_unverified_and_not_a_site_alert(self):
        report = self.build(
            [
                deterministic_result(
                    "failed",
                    case="dom_modules",
                    messages=["DOM [gallery] selector not found"],
                ),
                page_summary(),
            ]
        )

        finding = report.findings[0]
        self.assertEqual(
            FailureClassification.SELECTOR_CHANGED,
            finding.classification,
        )
        self.assertEqual(HealthStatus.UNVERIFIED, finding.status)
        self.assertFalse(report.alert.should_alert)
        self.assertEqual(
            "selector_change_requires_review",
            finding.suppression_reason,
        )

    def test_network_error_is_flaky_not_a_claim_that_site_is_down(self):
        summary = page_summary(
            status="failed",
            runtime_status="failed",
            findings=[
                {
                    "severity": "error",
                    "reason_code": "network_error",
                    "message": "Main document request failed.",
                    "evidence": {"error_type": "net::ERR_CONNECTION_RESET"},
                }
            ],
        )

        report = self.build([summary])

        finding = report.findings[0]
        self.assertEqual(
            FailureClassification.NETWORK_TRANSIENT,
            finding.classification,
        )
        self.assertEqual(HealthStatus.FLAKY, finding.status)
        self.assertFalse(report.alert.should_alert)

    def test_recovered_retry_has_typed_evidence_and_flaky_status(self):
        report = self.build(
            [
                page_summary(
                    status="warning",
                    runtime_status="passed",
                    recovered_after_retry=True,
                    retry_count=1,
                    attempts=[
                        {"attempt": 1, "status": "failed"},
                        {"attempt": 2, "status": "passed"},
                    ],
                )
            ]
        )

        finding = report.findings[0]
        self.assertEqual(HealthStatus.FLAKY, finding.status)
        self.assertTrue(finding.evidence)
        self.assertEqual("MEDIUM", finding.evidence_level.value)

    def test_unimplemented_dimensions_are_not_reported_as_passed(self):
        report = self.build([page_summary()])

        self.assertEqual(HealthStatus.UNVERIFIED, report.status)
        self.assertEqual("DEGRADED", report.overall_health.value)
        for dimension in ("performance", "accessibility", "seo", "responsive"):
            self.assertEqual(
                HealthStatus.UNVERIFIED,
                report.dimension_statuses[dimension],
            )
        self.assertIsNone(report.health_score)
        self.assertEqual(
            "UNVERIFIED",
            report.changes_since_previous_run["status"],
        )


class CapabilityAndInteractionTests(unittest.TestCase):
    def test_product_capabilities_include_risk_levels(self):
        profile = ConfigCapabilityDetector().detect(
            "product",
            SITE_CONFIG["pages"]["product"],
        )
        risks = {
            capability.name: capability.side_effect_level
            for capability in profile.capabilities
        }

        self.assertEqual(PageType.PDP, profile.page_type)
        self.assertEqual(SideEffectLevel.SAFE, risks["variant"])
        self.assertEqual(
            SideEffectLevel.TRANSACTIONAL_SAFE,
            risks["add_to_cart"],
        )

    def test_interaction_policy_defaults_to_safe_only(self):
        policy = InteractionPolicy(DEFAULT_HEALTH_CHECK["interaction_policy"])

        self.assertTrue(policy.decide("filter").allowed)
        self.assertFalse(policy.decide("add_to_cart").allowed)
        self.assertTrue(
            policy.decide("add_to_cart", explicit_opt_in=True).allowed
        )
        self.assertFalse(policy.decide("checkout", explicit_opt_in=True).allowed)


class AIContractTests(unittest.TestCase):
    class Analyzer:
        name = "fixture-ai"

        def __init__(self):
            self.calls = 0

        def analyze(self, request):
            self.calls += 1
            return AIAnalysis(
                enabled=True,
                invoked=True,
                status="COMPLETE",
                summary="Review the selector candidate.",
                self_heal_suggestions=[
                    {
                        "finding_id": request.findings[0]["finding_id"],
                        "original_selector": ".old",
                        "proposed_selector": ".new",
                        "rationale": "Candidate only",
                        "approval_required": False,
                        "applied": True,
                    }
                ],
            )

    def build(self, results, config, analyzer):
        return HealthEngine(
            results,
            site_config=SITE_CONFIG,
            config=config,
            ai_analyzer=analyzer,
        ).build()

    def test_ai_runs_only_for_anomaly_and_cannot_apply_self_heal(self):
        analyzer = self.Analyzer()
        report = self.build(
            [
                deterministic_result(
                    "failed",
                    case="add_to_cart_state",
                    dimension="functional",
                    messages=["button stayed disabled"],
                ),
                page_summary(),
            ],
            health_config(ai_enabled=True),
            analyzer,
        )

        self.assertEqual(1, analyzer.calls)
        suggestion = report.ai_analysis.self_heal_suggestions[0]
        self.assertTrue(suggestion["approval_required"])
        self.assertFalse(suggestion["applied"])

    def test_ai_is_not_called_on_normal_run(self):
        analyzer = self.Analyzer()
        report = self.build(
            [page_summary()],
            health_config(ai_enabled=True),
            analyzer,
        )

        self.assertEqual(0, analyzer.calls)
        self.assertFalse(report.ai_analysis.invoked)
        self.assertEqual("no_anomalous_findings", report.ai_analysis.reason)

    def test_provider_failure_is_contained(self):
        class BrokenAnalyzer:
            name = "broken"

            def analyze(self, _request):
                raise RuntimeError("provider unavailable")

        report = self.build(
            [
                deterministic_result(
                    "failed",
                    case="add_to_cart_state",
                    dimension="functional",
                    messages=["button stayed disabled"],
                ),
                page_summary(),
            ],
            health_config(ai_enabled=True),
            BrokenAnalyzer(),
        )

        self.assertEqual("ERROR", report.ai_analysis.status)
        self.assertEqual("provider_error:RuntimeError", report.ai_analysis.reason)


class ReportingAndValidationTests(unittest.TestCase):
    def test_report_writes_json_html_and_run_scoped_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports = root / "reports"
            artifacts = root / "artifacts"
            config = health_config()
            with patch(
                "playwright_checks.health.reporting.artifact_root",
                return_value=artifacts,
            ):
                paths = write_health_reports(
                    [page_summary()],
                    site_config=SITE_CONFIG,
                    config=config,
                    output_dir=reports,
                    project_root=root,
                )

            payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
            html = Path(paths["html"]).read_text(encoding="utf-8")
            self.assertIsNone(payload["health_score"])
            self.assertEqual("DEFERRED", payload["summary"]["health_score_state"])
            self.assertIn("Website Health Dashboard", html)
            self.assertIn("score deferred", html)
            self.assertIn("Site Profile &amp; Deterministic Plan", html)
            self.assertTrue((artifacts / "fixture-run" / "health-report.json").is_file())
            self.assertTrue((artifacts / "fixture-run" / "health-report.html").is_file())
            self.assertTrue((artifacts / "fixture-run" / "site-profile.json").is_file())
            self.assertTrue((artifacts / "fixture-run" / "test-plan.json").is_file())
            self.assertEqual(
                "artifacts/fixture-run/site-profile.json",
                payload["site_profile_reference"],
            )
            self.assertEqual("AVAILABLE", payload["site_profile_summary"]["status"])
            self.assertEqual("AVAILABLE", payload["test_plan_summary"]["status"])

    def test_observation_is_added_to_legacy_result_stream(self):
        clear_results()
        try:
            record_check_observation(
                SimpleNamespace(site="fixture", page_name="product"),
                "product_content",
                "dom_content",
                ["price selector not found"],
                capability="product_price",
            )
            result = get_results()[0]
        finally:
            clear_results()

        self.assertEqual("deterministic_check", result["result_type"])
        self.assertEqual("failed", result["status"])
        self.assertEqual("product_price", result["capability"])

    def test_health_config_validator_rejects_automatic_self_healing(self):
        valid = health_config()
        valid_errors = []
        validate_health_check(valid_errors, "health_check", valid, [])
        self.assertEqual([], valid_errors)

        invalid = health_config()
        invalid["ai"]["self_healing"]["suggestions_only"] = False
        invalid["ai"]["self_healing"]["approval_required"] = False
        invalid["ai"]["self_healing"]["auto_apply"] = True
        errors = []
        validate_health_check(errors, "health_check", invalid, [])

        self.assertEqual(3, len(errors))
        self.assertTrue(any("auto_apply must remain false" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
