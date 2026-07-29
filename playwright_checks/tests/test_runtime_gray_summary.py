import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from playwright_checks.runtime.gray_summary import print_summary, summarize


RUN_ID = "jenkins-42-mondressy-us-runtime-gray"


class RuntimeGraySummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_evidence(self, attempt, results):
        runtime_dir = (
            self.root
            / "artifacts"
            / RUN_ID
            / "mondressy_US"
            / "desktop"
            / "home"
            / "runtime"
        )
        runtime_dir.mkdir(parents=True)
        (runtime_dir / "attempt-1.json").write_text(
            json.dumps(attempt),
            encoding="utf-8",
        )
        result_path = (
            self.root
            / "artifacts"
            / RUN_ID
            / "visual-results.json"
        )
        result_path.write_text(json.dumps(results), encoding="utf-8")

    def test_report_only_keeps_findings_without_runtime_gate_failure(self):
        attempt = {
            "site": "mondressy_US",
            "page": "home",
            "viewport": "desktop",
            "runtime_status": "failed",
            "runtime_affects_exit_code": False,
            "findings": [
                {"reason_code": "console_error", "count": 2},
                {"reason_code": "third_party_error", "count": 1},
            ],
            "events": [
                {
                    "event_type": "console",
                    "party": "first_party",
                    "count": 2,
                },
                {
                    "event_type": "request_failed",
                    "party": "third_party",
                    "blocking": True,
                    "count": 3,
                },
                {
                    "event_type": "request_failed",
                    "party": "third_party",
                    "blocking": False,
                    "count": 4,
                },
            ],
            "pre_visual_health": {"loading_visible_count": 2},
            "request_header_injection": "route",
            "http_cache_mode": "disabled_by_routing",
            "run_profile": "intercepted_cold_context",
            "automation_errors": [],
            "collector_errors": [],
        }
        page_summary = {
            "result_type": "page_summary",
            "site": "mondressy_US",
            "viewport": "desktop",
            "page": "home",
            "runtime_status": "failed",
            "runtime_exit_status": "failed",
            "runtime_affects_exit_code": False,
            "runtime_fail_on_failed": True,
            "runtime_fail_on_warning": False,
            "findings": attempt["findings"],
        }
        self.write_evidence(attempt, [page_summary])

        with patch.dict(
            os.environ,
            {"VISUAL_STRICT_WARNINGS": "true"},
            clear=True,
        ):
            summary = summarize(RUN_ID, 0, self.root)

        self.assertEqual(2, summary["runtime_findings_count"])
        self.assertEqual(0, summary["runtime_gated_failure_count"])
        self.assertEqual(0, summary["visual_failure_count"])
        self.assertEqual(0, summary["execution_error_count"])
        self.assertFalse(summary["runtime_exit_gate"])
        self.assertEqual(2, summary["console_event_count"])
        self.assertEqual(3, summary["network_anomaly_count"])
        self.assertEqual(2, summary["loading_anomaly_count"])
        self.assertEqual(2, summary["first_party_event_count"])
        self.assertEqual(7, summary["third_party_event_count"])
        self.assertEqual("route", summary["request_header_injection"])
        self.assertEqual("SUCCESS", summary["jenkins_result"])

    def test_visual_and_execution_failures_are_separate(self):
        attempt = {
            "site": "mondressy_US",
            "page": "home",
            "viewport": "desktop",
            "findings": [],
            "events": [],
            "automation_errors": [{"phase": "fixture"}],
            "collector_errors": [{"event": "fixture"}],
        }
        results = [
            {
                "result_type": "visual",
                "site": "mondressy_US",
                "viewport": "desktop",
                "page": "home",
                "case": "banner",
                "status": "failed",
            },
            {
                "result_type": "visual",
                "site": "mondressy_US",
                "viewport": "desktop",
                "page": "home",
                "case": "header",
                "status": "warning",
            },
            {
                "result_type": "visual",
                "site": "mondressy_US",
                "viewport": "desktop",
                "page": "home",
                "case": "runtime",
                "status": "failed",
            },
        ]
        self.write_evidence(attempt, results)

        with patch.dict(
            os.environ,
            {"VISUAL_STRICT_WARNINGS": "true"},
            clear=True,
        ):
            summary = summarize(RUN_ID, 1, self.root)

        self.assertEqual(2, summary["visual_failure_count"])
        self.assertEqual(2, summary["execution_error_count"])
        self.assertEqual(1, summary["python_exit_code"])
        self.assertEqual("FAILURE", summary["jenkins_result"])

    def test_runtime_gate_is_counted_and_rejected_independently(self):
        attempt = {
            "site": "mondressy_US",
            "page": "home",
            "viewport": "desktop",
            "runtime_status": "failed",
            "runtime_affects_exit_code": True,
            "runtime_fail_on_failed": True,
            "runtime_fail_on_warning": False,
            "findings": [{"reason_code": "fixture_failed", "count": 1}],
            "events": [],
            "request_header_injection": "route",
            "http_cache_mode": "disabled_by_routing",
            "run_profile": "intercepted_cold_context",
        }
        page_summary = {
            "result_type": "page_summary",
            "site": "mondressy_US",
            "viewport": "desktop",
            "page": "home",
            "runtime_status": "failed",
            "runtime_exit_status": "failed",
            "runtime_affects_exit_code": True,
            "runtime_fail_on_failed": True,
            "runtime_fail_on_warning": False,
            "findings": attempt["findings"],
        }
        self.write_evidence(attempt, [page_summary])

        summary = summarize(RUN_ID, 0, self.root)

        self.assertEqual(1, summary["runtime_findings_count"])
        self.assertEqual(1, summary["runtime_gated_failure_count"])
        self.assertTrue(summary["runtime_exit_gate"])
        self.assertFalse(summary["_summary_valid"])
        self.assertEqual("FAILURE", summary["jenkins_result"])

    def test_missing_evidence_is_an_execution_error_after_python_failure(self):
        summary = summarize(RUN_ID, 1, self.root)

        self.assertEqual(1, summary["execution_error_count"])
        self.assertTrue(summary["_summary_valid"])
        self.assertEqual("FAILURE", summary["jenkins_result"])

    def test_content_changed_is_reported_without_blocking_gray_result(self):
        attempt = {
            "site": "mondressy_US",
            "page": "home",
            "viewport": "desktop",
            "runtime_status": "passed",
            "runtime_affects_exit_code": False,
            "findings": [],
            "events": [],
            "request_header_injection": "route",
            "http_cache_mode": "disabled_by_routing",
            "run_profile": "intercepted_cold_context",
        }
        results = [
            {
                "result_type": "visual",
                "site": "mondressy_US",
                "viewport": "desktop",
                "page": "home",
                "case": "collections",
                "status": "content_changed",
                "affects_exit_code": False,
            },
            {
                "result_type": "page_summary",
                "site": "mondressy_US",
                "viewport": "desktop",
                "page": "home",
                "visual_status": "content_changed",
                "runtime_status": "passed",
                "runtime_exit_status": "passed",
                "runtime_affects_exit_code": False,
                "findings": [],
            },
        ]
        self.write_evidence(attempt, results)

        with patch.dict(
            os.environ,
            {"VISUAL_STRICT_WARNINGS": "false"},
            clear=True,
        ):
            summary = summarize(RUN_ID, 0, self.root)

        self.assertEqual(1, summary["content_changed_count"])
        self.assertEqual("content_changed", summary["visual_result"])
        self.assertEqual(0, summary["visual_failure_count"])
        self.assertEqual("SUCCESS", summary["jenkins_result"])

    def test_python_exit_codes_zero_one_and_two_are_preserved(self):
        attempt = {
            "site": "mondressy_US",
            "page": "home",
            "viewport": "desktop",
            "runtime_affects_exit_code": False,
            "findings": [],
            "events": [],
            "request_header_injection": "route",
            "http_cache_mode": "disabled_by_routing",
            "run_profile": "intercepted_cold_context",
        }
        self.write_evidence(
            attempt,
            [
                {
                    "result_type": "page_summary",
                    "site": "mondressy_US",
                    "viewport": "desktop",
                    "page": "home",
                    "visual_status": "passed",
                    "runtime_affects_exit_code": False,
                    "findings": [],
                }
            ],
        )

        for exit_code, expected in (
            (0, "SUCCESS"),
            (1, "FAILURE"),
            (2, "FAILURE"),
        ):
            with self.subTest(exit_code=exit_code):
                summary = summarize(RUN_ID, exit_code, self.root)
                self.assertEqual(exit_code, summary["python_exit_code"])
                self.assertEqual(expected, summary["jenkins_result"])

    def test_printed_summary_contains_required_gray_fields(self):
        summary = {
            "site_key": "mondressy_US",
            "page": "Home",
            "viewport": "desktop",
            "report_only": True,
            "runtime_findings_count": 2,
            "runtime_gated_failure_count": 0,
            "visual_failure_count": 0,
            "content_changed_count": 0,
            "visual_result": "passed",
            "execution_error_count": 0,
            "runtime_exit_gate": False,
            "console_event_count": 1,
            "network_anomaly_count": 2,
            "loading_anomaly_count": 0,
            "first_party_event_count": 2,
            "third_party_event_count": 1,
            "request_header_injection": "route",
            "http_cache_mode": "disabled_by_routing",
            "run_profile": "intercepted_cold_context",
            "python_exit_code": 0,
            "jenkins_result": "SUCCESS",
        }
        output = io.StringIO()
        with redirect_stdout(output):
            print_summary(summary)

        rendered = output.getvalue()
        for key in (
            "runtime_findings_count",
            "runtime_gated_failure_count",
            "visual_failure_count",
            "content_changed_count",
            "visual_result",
            "execution_error_count",
            "runtime_exit_gate",
            "python_exit_code",
            "jenkins_result",
        ):
            self.assertIn(f"{key}=", rendered)
        self.assertIn("runtime_exit_gate=false", rendered)


if __name__ == "__main__":
    unittest.main()
