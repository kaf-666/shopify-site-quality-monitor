import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from playwright_checks.runtime.gray_summary import (
    EXPECTED_SCOPES,
    SITE_KEY,
    TOTAL_KEYS,
    main,
    print_summary,
    summarize,
)


RUN_ID = "jenkins-42-mondressy-us-runtime-gray"


class RuntimeGraySummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def base_attempt(self, viewport, page, **overrides):
        attempt = {
            "site": SITE_KEY,
            "page": page,
            "viewport": viewport,
            "runtime_status": "passed",
            "runtime_affects_exit_code": False,
            "runtime_fail_on_failed": True,
            "runtime_fail_on_warning": False,
            "findings": [],
            "events": [],
            "pre_visual_health": {"loading_visible_count": 0},
            "request_header_injection": "route",
            "http_cache_mode": "disabled_by_routing",
            "run_profile": "intercepted_cold_context",
            "automation_errors": [],
            "collector_errors": [],
        }
        attempt.update(overrides)
        return attempt

    def base_page_summary(self, viewport, page, **overrides):
        page_summary = {
            "result_type": "page_summary",
            "site": SITE_KEY,
            "viewport": viewport,
            "page": page,
            "visual_status": "passed",
            "runtime_status": "passed",
            "runtime_exit_status": "passed",
            "runtime_affects_exit_code": False,
            "runtime_fail_on_failed": True,
            "runtime_fail_on_warning": False,
            "findings": [],
        }
        page_summary.update(overrides)
        return page_summary

    def write_attempt(
        self,
        viewport,
        page,
        attempt_number=1,
        payload=None,
        raw=None,
    ):
        runtime_dir = (
            self.root
            / "artifacts"
            / RUN_ID
            / SITE_KEY
            / viewport
            / page
            / "runtime"
        )
        runtime_dir.mkdir(parents=True, exist_ok=True)
        path = runtime_dir / f"attempt-{attempt_number}.json"
        if raw is not None:
            path.write_text(raw, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(
                    payload or self.base_attempt(viewport, page),
                ),
                encoding="utf-8",
            )
        return path

    def write_results(self, results):
        result_path = (
            self.root / "artifacts" / RUN_ID / "visual-results.json"
        )
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps(results), encoding="utf-8")

    def populate_complete_evidence(
        self,
        skip_attempt=None,
        skip_page_summary=None,
        statuses=None,
    ):
        statuses = statuses or {}
        results = [
            self.base_page_summary(
                "desktop",
                "unrelated",
                runtime_status="failed",
            ),
            {
                "result_type": "page_summary",
                "site": "other_site",
                "viewport": "desktop",
                "page": "home",
                "runtime_status": "failed",
            },
        ]
        for viewport, page in EXPECTED_SCOPES:
            scope = (viewport, page)
            status = statuses.get(scope, "passed")
            if scope != skip_attempt:
                self.write_attempt(
                    viewport,
                    page,
                    payload=self.base_attempt(
                        viewport,
                        page,
                        runtime_status=status,
                    ),
                )
            if scope != skip_page_summary:
                results.append(
                    self.base_page_summary(
                        viewport,
                        page,
                        runtime_status=status,
                        runtime_exit_status=status,
                    )
                )
        self.write_results(results)
        return results

    def test_six_scopes_are_summarized_independently(self):
        statuses = {
            scope: status
            for scope, status in zip(
                EXPECTED_SCOPES,
                ("passed", "warning", "failed") * 2,
            )
        }
        self.populate_complete_evidence(statuses=statuses)

        with patch.dict(
            os.environ,
            {"VISUAL_STRICT_WARNINGS": "false"},
            clear=True,
        ):
            summary = summarize(RUN_ID, 0, self.root)

        self.assertEqual(6, summary["expected_scope_count"])
        self.assertEqual(6, summary["completed_scope_count"])
        self.assertEqual(0, summary["missing_scope_count"])
        self.assertEqual([], summary["missing_scopes"])
        self.assertTrue(summary["summary_valid"])
        self.assertEqual("SUCCESS", summary["jenkins_result"])
        self.assertEqual(
            statuses,
            {
                (scope["viewport"], scope["page"]): scope["runtime_status"]
                for scope in summary["scopes"]
            },
        )
        self.assertEqual(
            set(EXPECTED_SCOPES),
            {
                (scope["viewport"], scope["page"])
                for scope in summary["scopes"]
            },
        )
        required_scope_fields = {
            "site",
            "viewport",
            "page",
            "evidence_available",
            "page_summary_available",
            "runtime_status",
            "runtime_affects_exit_code",
            "findings_count",
            "console_event_count",
            "network_anomaly_count",
            "loading_anomaly_count",
            "first_party_event_count",
            "third_party_event_count",
            "collector_error_count",
            "automation_error_count",
        }
        for scope in summary["scopes"]:
            self.assertTrue(required_scope_fields.issubset(scope))

    def test_missing_runtime_attempt_marks_scope_incomplete(self):
        missing_scope = ("mobile", "product")
        self.populate_complete_evidence(skip_attempt=missing_scope)

        summary = summarize(RUN_ID, 0, self.root)

        self.assertEqual(5, summary["completed_scope_count"])
        self.assertEqual(1, summary["missing_scope_count"])
        self.assertEqual(
            [
                {
                    "site": SITE_KEY,
                    "viewport": "mobile",
                    "page": "product",
                    "missing": ["runtime_attempt"],
                }
            ],
            summary["missing_scopes"],
        )
        self.assertFalse(summary["summary_valid"])
        self.assertEqual("FAILURE", summary["jenkins_result"])

    def test_missing_page_summary_marks_scope_incomplete(self):
        missing_scope = ("desktop", "collection")
        self.populate_complete_evidence(skip_page_summary=missing_scope)

        summary = summarize(RUN_ID, 0, self.root)

        self.assertEqual(5, summary["completed_scope_count"])
        self.assertEqual(
            ["page_summary"],
            summary["missing_scopes"][0]["missing"],
        )
        scope = next(
            item
            for item in summary["scopes"]
            if (item["viewport"], item["page"]) == missing_scope
        )
        self.assertTrue(scope["evidence_available"])
        self.assertFalse(scope["page_summary_available"])
        self.assertFalse(summary["summary_valid"])

    def test_python_zero_with_missing_scope_returns_summary_failure(self):
        self.populate_complete_evidence(skip_attempt=("desktop", "product"))
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "--run-id",
                    RUN_ID,
                    "--python-exit-code",
                    "0",
                    "--project-root",
                    str(self.root),
                ]
            )

        self.assertEqual(1, exit_code)
        self.assertIn("summary_valid=false", output.getvalue())
        self.assertIn("missing_scope_count=1", output.getvalue())

    def test_python_failure_is_preserved_while_evidence_is_saved(self):
        self.populate_complete_evidence(skip_attempt=("mobile", "home"))
        output = io.StringIO()

        with redirect_stdout(output):
            summary_exit_code = main(
                [
                    "--run-id",
                    RUN_ID,
                    "--python-exit-code",
                    "2",
                    "--project-root",
                    str(self.root),
                ]
            )

        saved = json.loads(
            (
                self.root
                / "artifacts"
                / RUN_ID
                / "gray-summary.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(1, summary_exit_code)
        self.assertEqual(2, saved["python_exit_code"])
        self.assertFalse(saved["summary_valid"])
        self.assertEqual("FAILURE", saved["jenkins_result"])
        self.assertIn("python_exit_code=2", output.getvalue())
        self.assertEqual(6, len(saved["scopes"]))

    def test_latest_numbered_valid_attempt_is_selected_per_scope(self):
        self.populate_complete_evidence()
        self.write_attempt(
            "desktop",
            "home",
            attempt_number=2,
            payload=self.base_attempt(
                "desktop",
                "home",
                runtime_status="warning",
                findings=[{"reason_code": "latest"}],
            ),
        )
        self.write_attempt(
            "desktop",
            "home",
            attempt_number=10,
            raw="{not valid json",
        )
        self.write_attempt(
            "desktop",
            "home",
            attempt_number=11,
            raw="{}",
        )

        summary = summarize(RUN_ID, 0, self.root)
        scope = summary["scopes"][0]

        self.assertEqual("desktop", scope["viewport"])
        self.assertEqual("home", scope["page"])
        self.assertEqual(2, scope["attempt"])
        self.assertEqual("warning", scope["runtime_status"])
        self.assertEqual(1, scope["findings_count"])

    def test_six_scope_findings_events_and_results_are_aggregated(self):
        results = []
        statuses = ("passed", "warning", "failed") * 2
        for index, ((viewport, page), status) in enumerate(
            zip(EXPECTED_SCOPES, statuses),
            start=1,
        ):
            findings = [
                {"reason_code": f"fixture_{item}"}
                for item in range(index)
            ]
            events = [
                {
                    "event_type": "console",
                    "party": "first_party",
                    "count": index,
                },
                {
                    "event_type": "request_failed",
                    "party": "third_party",
                    "blocking": True,
                    "count": index * 2,
                },
                {
                    "event_type": "http_error",
                    "party": "third_party",
                    "blocking": False,
                    "count": index * 3,
                },
            ]
            self.write_attempt(
                viewport,
                page,
                payload=self.base_attempt(
                    viewport,
                    page,
                    runtime_status=status,
                    findings=findings,
                    events=events,
                    pre_visual_health={"loading_visible_count": index},
                    collector_errors=[{"fixture": index}],
                    automation_errors=[{"fixture": 1}, {"fixture": 2}],
                ),
            )
            results.append(
                self.base_page_summary(
                    viewport,
                    page,
                    runtime_status=status,
                    runtime_exit_status=status,
                    findings=findings,
                )
            )
            if index % 2:
                results.append(
                    {
                        "result_type": "visual",
                        "site": SITE_KEY,
                        "viewport": viewport,
                        "page": page,
                        "case": "hero",
                        "status": "failed",
                        "affects_exit_code": True,
                    }
                )
            else:
                results.append(
                    {
                        "result_type": "visual",
                        "site": SITE_KEY,
                        "viewport": viewport,
                        "page": page,
                        "case": "hero",
                        "status": "content_changed",
                        "affects_exit_code": False,
                    }
                )
            if index == 1:
                results.append(
                    {
                        "result_type": "visual",
                        "site": SITE_KEY,
                        "viewport": viewport,
                        "page": page,
                        "case": "runtime",
                        "status": "failed",
                    }
                )
        self.write_results(results)

        with patch.dict(
            os.environ,
            {"VISUAL_STRICT_WARNINGS": "false"},
            clear=True,
        ):
            totals = summarize(RUN_ID, 1, self.root)["totals"]

        self.assertEqual(2, totals["runtime_passed_scope_count"])
        self.assertEqual(2, totals["runtime_warning_scope_count"])
        self.assertEqual(2, totals["runtime_failed_scope_count"])
        self.assertEqual(21, totals["runtime_findings_count"])
        self.assertEqual(0, totals["runtime_gated_failure_count"])
        self.assertEqual(3, totals["visual_failure_count"])
        self.assertEqual(3, totals["content_changed_count"])
        self.assertEqual(17, totals["execution_error_count"])
        self.assertEqual(21, totals["console_event_count"])
        self.assertEqual(42, totals["network_anomaly_count"])
        self.assertEqual(21, totals["loading_anomaly_count"])
        self.assertEqual(21, totals["first_party_event_count"])
        self.assertEqual(105, totals["third_party_event_count"])

    def test_blocking_and_non_blocking_network_events_are_distinct(self):
        self.populate_complete_evidence()
        self.write_attempt(
            "mobile",
            "collection",
            attempt_number=2,
            payload=self.base_attempt(
                "mobile",
                "collection",
                events=[
                    {
                        "event_type": "request_failed",
                        "party": "third_party",
                        "blocking": True,
                        "count": 2,
                    },
                    {
                        "event_type": "http_error",
                        "party": "third_party",
                        "blocking": False,
                        "count": 7,
                    },
                ],
            ),
        )

        summary = summarize(RUN_ID, 0, self.root)
        scope = next(
            item
            for item in summary["scopes"]
            if item["viewport"] == "mobile"
            and item["page"] == "collection"
        )

        self.assertEqual(2, scope["network_anomaly_count"])
        self.assertEqual(9, scope["third_party_event_count"])

    def test_runtime_gate_in_any_scope_is_rejected(self):
        results = self.populate_complete_evidence()
        results = [
            result
            for result in results
            if not (
                result.get("result_type") == "page_summary"
                and result.get("viewport") == "mobile"
                and result.get("page") == "product"
            )
        ]
        results.append(
            self.base_page_summary(
                "mobile",
                "product",
                runtime_status="failed",
                runtime_exit_status="failed",
                runtime_affects_exit_code=True,
                runtime_fail_on_failed=True,
                findings=[{"reason_code": "fixture_failed"}],
            )
        )
        self.write_results(results)

        summary = summarize(RUN_ID, 0, self.root)
        scope = summary["scopes"][-1]

        self.assertTrue(scope["runtime_affects_exit_code"])
        self.assertEqual(1, scope["runtime_gated_failure_count"])
        self.assertEqual(1, summary["totals"]["runtime_gated_failure_count"])
        self.assertFalse(summary["report_only"])
        self.assertFalse(summary["summary_valid"])

    def test_gray_summary_json_and_console_totals_match(self):
        self.populate_complete_evidence()
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "--run-id",
                    RUN_ID,
                    "--python-exit-code",
                    "0",
                    "--project-root",
                    str(self.root),
                ]
            )

        saved = json.loads(
            (
                self.root
                / "artifacts"
                / RUN_ID
                / "gray-summary.json"
            ).read_text(encoding="utf-8")
        )
        console_values = dict(
            line.split("=", 1)
            for line in output.getvalue().splitlines()
            if "=" in line
        )

        self.assertEqual(0, exit_code)
        for key in TOTAL_KEYS:
            self.assertEqual(str(saved["totals"][key]), console_values[key])
        for required in (
            "schema_version",
            "run_id",
            "site",
            "expected_scopes",
            "completed_scope_count",
            "missing_scopes",
            "totals",
            "scopes",
            "python_exit_code",
            "summary_valid",
            "jenkins_result",
        ):
            self.assertIn(required, saved)

    def test_printed_summary_contains_each_scope_and_totals(self):
        self.populate_complete_evidence()
        summary = summarize(RUN_ID, 0, self.root)
        output = io.StringIO()

        with redirect_stdout(output):
            print_summary(summary)

        rendered = output.getvalue()
        for key in TOTAL_KEYS:
            self.assertIn(f"{key}=", rendered)
        for viewport, page in EXPECTED_SCOPES:
            self.assertIn(
                f"scope.{viewport}.{page}.evidence_available=true",
                rendered,
            )
            self.assertIn(
                f"scope.{viewport}.{page}.page_summary_available=true",
                rendered,
            )
        self.assertIn("report_only=true", rendered)


if __name__ == "__main__":
    unittest.main()
