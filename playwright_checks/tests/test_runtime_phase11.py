import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from playwright_checks.core.test_results import (
    clear_results,
    get_results,
)
from playwright_checks.core.config_loader import get_runtime_health_config
from playwright_checks.core.viewport import set_current_viewport
from playwright_checks.runner import main as runner
from playwright_checks.runtime.checks import (
    build_findings,
    collect_health_fingerprint,
    runtime_status,
)
from playwright_checks.runtime.collector import (
    RuntimeEventCollector,
    _root_domain,
)
from playwright_checks.runtime.evidence import (
    redact_text,
    redact_url,
    sanitize_payload,
)
from playwright_checks.runtime.session import (
    RuntimeHealthSession,
    finalize_runtime_health_fail_open,
    runtime_failure_messages,
)
from playwright_checks.utils import visual
from playwright_checks.utils.waits import open_page_with_retry
import run_all


def _policy_summary(
    status,
    *,
    report_only=False,
    affect_exit_code=True,
    fail_on_failed=True,
    fail_on_warning=False,
):
    affects_exit = not report_only and affect_exit_code
    return {
        "runtime_status": status,
        "runtime_exit_status": status,
        "runtime_mode": "enforced" if affects_exit else "report_only",
        "runtime_affects_exit_code": affects_exit,
        "runtime_fail_on_failed": fail_on_failed,
        "runtime_fail_on_warning": fail_on_warning,
        "primary_failure_reason": f"fixture_{status}",
        "findings": [
            {
                "reason_code": f"fixture_{status}",
                "severity": status,
            }
        ],
    }


class RuntimeExitPolicyTests(unittest.TestCase):
    def tearDown(self):
        clear_results()

    def test_report_only_failed_does_not_inject_failure(self):
        summary = _policy_summary("failed", report_only=True)
        self.assertEqual([], runtime_failure_messages(summary))

    def test_enforced_failed_injects_failure(self):
        summary = _policy_summary("failed")
        self.assertEqual(1, len(runtime_failure_messages(summary)))

    def test_warning_uses_runtime_policy_not_jenkins_or_visual_policy(self):
        non_blocking = _policy_summary("warning", fail_on_warning=False)
        blocking = _policy_summary("warning", fail_on_warning=True)
        with patch.dict(
            os.environ,
            {
                "JENKINS_URL": "https://jenkins.test",
                "VISUAL_STRICT_WARNINGS": "false",
            },
            clear=True,
        ):
            self.assertEqual([], runtime_failure_messages(non_blocking))
            self.assertEqual(1, len(runtime_failure_messages(blocking)))

    def test_runner_exit_code_follows_runtime_failure_strings(self):
        cases = [
            (_policy_summary("failed", report_only=True), 0),
            (_policy_summary("failed"), 1),
            (_policy_summary("warning", fail_on_warning=False), 0),
            (_policy_summary("warning", fail_on_warning=True), 1),
        ]
        for summary, expected in cases:
            with self.subTest(summary=summary):
                def run_func():
                    return runtime_failure_messages(summary)

                with (
                    patch.object(
                        runner,
                        "get_run_viewport_names",
                        return_value=["desktop"],
                    ),
                    patch.object(
                        runner,
                        "get_run_pages",
                        return_value=(("Home", "home", run_func),),
                    ),
                    patch.object(
                        runner,
                        "write_results",
                        return_value="<memory>",
                    ),
                ):
                    self.assertEqual(expected, runner.run_all())


class VisualStrictWarningEnvironmentTests(unittest.TestCase):
    def test_explicit_boolean_values_have_highest_priority(self):
        values = {
            "true": True,
            "1": True,
            "yes": True,
            "false": False,
            "0": False,
            "no": False,
        }
        for value, expected in values.items():
            with self.subTest(value=value), patch.dict(
                os.environ,
                {
                    "JENKINS_URL": "https://jenkins.test",
                    "VISUAL_STRICT_WARNINGS": value,
                },
                clear=True,
            ):
                self.assertIs(expected, visual._strict_warnings_enabled())

    def test_unset_uses_ci_then_config_and_invalid_falls_back(self):
        with patch.dict(
            os.environ,
            {"JENKINS_URL": "https://jenkins.test"},
            clear=True,
        ):
            self.assertTrue(visual._strict_warnings_enabled())


class RuntimeConfigPolicyTests(unittest.TestCase):
    def test_runtime_environment_overrides_are_explicit_and_invalid_falls_back(self):
        site = {
            "site": "fixture",
            "runtime_health": {
                "reporting": {
                    "report_only": True,
                    "affect_exit_code": False,
                    "fail_on_warning": False,
                }
            },
        }
        with patch.dict(
            os.environ,
            {
                "RUNTIME_HEALTH_REPORT_ONLY": "false",
                "RUNTIME_HEALTH_AFFECT_EXIT_CODE": "yes",
                "RUNTIME_HEALTH_FAIL_ON_WARNING": "1",
            },
            clear=True,
        ):
            config = get_runtime_health_config(site, {"url": "https://x.test"})
        self.assertFalse(config["reporting"]["report_only"])
        self.assertTrue(config["reporting"]["affect_exit_code"])
        self.assertTrue(config["reporting"]["fail_on_warning"])

        with patch.dict(
            os.environ,
            {"RUNTIME_HEALTH_REPORT_ONLY": "invalid"},
            clear=True,
        ):
            fallback = get_runtime_health_config(
                site,
                {"url": "https://x.test"},
            )
        self.assertTrue(fallback["reporting"]["report_only"])

    def test_runtime_validator_checks_nested_policy_and_selector_shapes(self):
        errors = []
        warnings = []
        run_all.validate_runtime_health(
            errors,
            "pages.product.mobile.runtime_health",
            {
                "reporting": {"report_only": "yes"},
                "retry_policy": {"recovered_status": "sometimes"},
                "loading_confirmation_ms": -1,
                "critical_selectors": [
                    {"name": "", "selector": ""},
                ],
                "unknown_field": True,
            },
            warnings,
        )
        self.assertGreaterEqual(len(errors), 4)
        self.assertTrue(
            any("unknown_field" in warning for warning in warnings)
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(
                visual,
                "load_settings",
                return_value={"ci": {"strict_warnings": False}},
            ),
        ):
            self.assertFalse(visual._strict_warnings_enabled())
        with patch.dict(
            os.environ,
            {
                "JENKINS_URL": "https://jenkins.test",
                "VISUAL_STRICT_WARNINGS": "not-a-boolean",
            },
            clear=True,
        ):
            self.assertTrue(visual._strict_warnings_enabled())


class RuntimeRedactionTests(unittest.TestCase):
    def tearDown(self):
        clear_results()

    def test_credentials_are_redacted_in_urls_text_and_nested_payloads(self):
        values = [
            "Authorization: Bearer secret",
            "authorization=Bearer secret",
            "Bearer eyJhbGciOiJIUzI1NiJ9.secret",
            "Basic dXNlcjpwYXNz",
            "token=secret",
            "key: 'secret'",
            "AUTH=\"secret\"",
            "access_token=secret",
            '{"api_key":"secret"}',
            "signature=secret",
        ]
        for value in values:
            with self.subTest(value=value):
                redacted = redact_text(value)
                self.assertNotIn("secret", redacted.lower())
                self.assertIn("[REDACTED]", redacted)

        url = redact_url(
            "https://user:password@example.com/path"
            "?token=secret&KEY=secret&access_token=secret#secret"
        )
        self.assertNotIn("user", url)
        self.assertNotIn("password", url)
        self.assertNotIn("secret", url)
        self.assertNotIn("#", url)

        nested = sanitize_payload(
            {
                "message": "Authorization: Bearer secret",
                "stack": ["key=secret"],
            }
        )
        self.assertNotIn("secret", json.dumps(nested).lower())

    def test_email_is_preserved_outside_credential_context(self):
        self.assertEqual(
            "email=user@example.com",
            redact_text("email=user@example.com"),
        )

    def test_finalize_fail_open_log_and_finding_are_redacted(self):
        class BrokenSession:
            config = {
                "reporting": {
                    "report_only": True,
                    "affect_exit_code": False,
                }
            }

            @staticmethod
            def capture_post_visual_state():
                raise RuntimeError("Authorization: Bearer secret")

        output = io.StringIO()
        with redirect_stdout(output):
            failures = finalize_runtime_health_fail_open(
                BrokenSession(),
                "fixture",
                "home",
                "desktop",
            )
        self.assertEqual([], failures)
        self.assertNotIn("secret", output.getvalue().lower())
        summaries = [
            item
            for item in get_results()
            if item.get("result_type") == "page_summary"
        ]
        self.assertEqual(1, len(summaries))
        self.assertEqual(
            "runtime_finalize_failed",
            summaries[0]["primary_failure_reason"],
        )
        self.assertNotIn("secret", json.dumps(summaries[0]).lower())


class RuntimeDomainAndNoiseTests(unittest.TestCase):
    def test_conservative_root_domain_and_hostname_only_patterns(self):
        self.assertEqual("example.com", _root_domain("api.example.com"))
        self.assertEqual("example.co.uk", _root_domain("www.example.co.uk"))
        self.assertEqual("example.com.au", _root_domain("api.example.com.au"))
        self.assertEqual("example.co.za", _root_domain("a.example.co.za"))
        self.assertEqual("other.co.za", _root_domain("b.other.co.za"))

        collector = RuntimeEventCollector(
            None,
            "https://www.example.com",
            {
                "third_party_patterns": [
                    "google-analytics.com",
                    "clarity.ms",
                ]
            },
        )
        self.assertEqual(
            "first_party",
            collector.classify_url("https://api.example.com/data"),
        )
        self.assertEqual(
            "third_party",
            collector.classify_url("https://cdn.shopify.com/file.js"),
        )
        self.assertEqual(
            "third_party",
            collector.classify_url("https://cdn.shopifycdn.net/file.js"),
        )
        self.assertEqual(
            "third_party",
            collector.classify_url("https://shopifycloud.com/file.js"),
        )
        self.assertEqual(
            "third_party",
            collector.classify_url("https://shopifysvc.com/api"),
        )
        self.assertEqual(
            "third_party",
            collector.classify_url("https://google-analytics.com/collect"),
        )
        self.assertEqual(
            "third_party",
            collector.classify_url("https://clarity.ms/tag"),
        )
        overlap = RuntimeEventCollector(
            None,
            "https://www.example.com",
            {
                "first_party_patterns": ["shared.test"],
                "third_party_patterns": ["shared.test"],
            },
        )
        self.assertEqual(
            "first_party",
            overlap.classify_url("https://shared.test/resource"),
        )
        self.assertEqual("unknown", collector.classify_url("/relative"))
        self.assertEqual(
            "unknown",
            collector.classify_url("data:text/plain,fixture"),
        )

        query_collector = RuntimeEventCollector(
            None,
            "https://merchant.test",
            {"third_party_patterns": ["google-analytics.com"]},
        )
        self.assertEqual(
            "first_party",
            query_collector.classify_url(
                "https://merchant.test/path"
                "?redirect=google-analytics.com"
            ),
        )

        conservative = RuntimeEventCollector(
            None,
            "https://a.example.invalidcc",
            {},
        )
        self.assertEqual(
            "third_party",
            conservative.classify_url("https://b.other.invalidcc"),
        )

    def test_expected_aborted_requests_are_non_blocking_but_critical_xhr_is_not(self):
        collector = RuntimeEventCollector(
            None,
            "https://www.example.com",
            {},
        )

        class Request:
            method = "GET"
            failure = "net::ERR_ABORTED"

            def __init__(self, url, resource_type):
                self.url = url
                self.resource_type = resource_type

        collector._on_request_failed(
            Request("https://google-analytics.com/collect", "fetch")
        )
        collector._on_request_failed(
            Request("https://www.example.com/lazy.jpg", "image")
        )
        collector._on_request_failed(
            Request("https://www.example.com/api", "xhr")
        )
        events = collector.snapshot()["events"]
        self.assertFalse(events[0]["blocking"])
        self.assertFalse(events[1]["blocking"])
        self.assertTrue(events[2]["blocking"])


class RuntimePhase11PlaywrightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            channel="chrome",
            headless=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        clear_results()
        set_current_viewport("desktop")
        self.temp = tempfile.TemporaryDirectory()
        self.context = self.browser.new_context(
            viewport={"width": 800, "height": 600}
        )
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()
        self.temp.cleanup()
        clear_results()

    def test_loading_filters_hidden_offscreen_image_and_transient_nodes(self):
        self.page.set_content(
            """
            <main class="main">Healthy content</main>
            <div class="loading" style="display:none;width:20px;height:20px">
              hidden
            </div>
            <img class="loading" style="width:20px;height:20px"
              src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==">
            <div style="height:2000px"></div>
            <div class="loading" style="width:20px;height:20px">bottom</div>
            """
        )
        health = collect_health_fingerprint(
            self.page,
            {},
            {
                "_page_name": "home",
                "loading_selectors": [".loading"],
                "loading_confirmation_ms": 0,
            },
        )
        self.assertEqual(0, health["loading_visible_count"])

        self.page.set_content(
            """
            <main class="main">
              <div class="spinner" style="width:20px;height:20px">wait</div>
            </main>
            <script>setTimeout(() => document.querySelector('.spinner').remove(), 20)</script>
            """
        )
        transient = collect_health_fingerprint(
            self.page,
            {},
            {
                "_page_name": "home",
                "loading_selectors": [".spinner"],
                "loading_critical_selectors": [".main"],
                "loading_confirmation_ms": 80,
            },
        )
        self.assertEqual(0, transient["loading_visible_count"])

    def test_persistent_critical_loading_and_selector_severity(self):
        self.page.set_content(
            """
            <main class="main">
              <div class="spinner" style="width:20px;height:20px">wait</div>
            </main>
            """
        )
        config = {
            "_page_name": "home",
            "critical_selectors": [
                {"name": "app", "selector": ".missing-app"}
            ],
            "optional_selectors": [
                {"name": "reviews", "selector": ".reviews"}
            ],
            "loading_selectors": [".spinner"],
            "loading_critical_selectors": [".main"],
            "loading_confirmation_ms": 20,
        }
        health = collect_health_fingerprint(self.page, {}, config)
        findings = build_findings(
            {"status": 200, "attempts": []},
            {"events": [], "collector_errors": [], "page_crashed": False},
            health,
            config,
        )
        reasons = {item.reason_code for item in findings}
        self.assertEqual(1, health["loading_critical_count"])
        self.assertIn("infinite_loading", reasons)
        self.assertIn("missing_optional_component", reasons)
        self.assertEqual("failed", runtime_status(findings))

    def test_only_explicit_or_reliable_default_selectors_are_critical(self):
        self.page.set_content(
            """
            <main>Healthy page</main>
            """
        )
        health = collect_health_fingerprint(
            self.page,
            {
                "modules": {
                    "banner": ["css", ".missing-banner"],
                    "reviews": ["css", ".missing-reviews"],
                }
            },
            {
                "_page_name": "home",
                "loading_selectors": [],
                "loading_confirmation_ms": 0,
            },
        )
        self.assertEqual([], health["missing_critical_elements"])
        self.assertEqual(["home.main"], [
            item["name"] for item in health["critical_elements"]
        ])

    def test_optional_missing_is_info_and_sold_out_satisfies_purchase_state(self):
        self.page.set_content(
            """
            <main>
              <h1>Fixture Product</h1>
              <div class="price">$10</div>
              <p>Sold out</p>
            </main>
            """
        )
        config = {
            "_page_name": "product",
            "optional_selectors": [
                {"name": "reviews", "selector": ".reviews"}
            ],
            "loading_selectors": [],
            "loading_confirmation_ms": 0,
        }
        health = collect_health_fingerprint(
            self.page,
            {"modules": {"add_to_cart": ["css", "button[name='add']"]}},
            config,
        )
        self.assertEqual([], health["missing_critical_elements"])
        findings = build_findings(
            {"status": 200, "attempts": []},
            {"events": [], "collector_errors": [], "page_crashed": False},
            health,
            config,
        )
        self.assertEqual("passed", runtime_status(findings))
        self.assertIn(
            "missing_optional_component",
            {item.reason_code for item in findings},
        )

    def test_runner_retry_preserves_attempts_and_marks_recovery(self):
        runtime_dir = Path(self.temp.name) / "retry-runtime"
        call_count = 0

        def run_func():
            nonlocal call_count
            call_count += 1
            page = self.context.new_page()
            page_config = {
                "url": "https://fixture.test/runtime",
                "modules": {"main": ["css", "main"]},
                "runtime_health": {
                    "reporting": {
                        "report_only": False,
                        "affect_exit_code": True,
                        "fail_on_failed": True,
                        "fail_on_warning": False,
                    },
                    "retry_policy": {"recovered_status": "warning"},
                    "loading_confirmation_ms": 0,
                },
            }
            session = RuntimeHealthSession(
                page,
                {"site": "fixture"},
                page_config,
                "home",
                evidence_directory=runtime_dir,
            )
            session.start_before_navigation()
            if call_count == 1:
                page.set_content("<html><body></body></html>")
            else:
                page.set_content(
                    "<html><body><main>Healthy retry content</main></body></html>"
                )
            session.record_navigation_attempt(
                {
                    "attempt": 1,
                    "requested_url": page_config["url"],
                    "final_url": page_config["url"],
                    "status": 200,
                }
            )
            session.complete_navigation(
                {"final_url": page_config["url"], "status": 200}
            )
            session.collect_after_ready()
            summary = session.finalize("passed")
            page.close()
            return runtime_failure_messages(summary)

        failures = runner.run_page(
            "Home",
            "home",
            run_func,
            "desktop",
        )
        self.assertEqual([], failures)
        self.assertEqual(2, call_count)
        self.assertTrue((runtime_dir / "attempt-1.json").exists())
        self.assertTrue((runtime_dir / "attempt-2.json").exists())
        persisted = json.loads(
            (runtime_dir / "summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual("failed", persisted["initial_runtime_status"])
        self.assertEqual("passed", persisted["final_runtime_status"])
        self.assertEqual("failed", persisted["worst_runtime_status"])
        self.assertTrue(persisted["recovered_after_retry"])
        self.assertEqual(1, persisted["retry_count"])
        page_summaries = [
            item
            for item in get_results()
            if item.get("result_type") == "page_summary"
        ]
        self.assertEqual(1, len(page_summaries))
        self.assertEqual("warning", page_summaries[0]["overall_status"])

    def test_runner_does_not_retry_terminal_main_document_error(self):
        call_count = 0

        def run_func():
            nonlocal call_count
            call_count += 1
            return [
                "Home: Playwright runtime error: "
                "TerminalMainDocumentError: status=429"
            ]

        failures = runner.run_page(
            "Home",
            "home",
            run_func,
            "desktop",
        )

        self.assertEqual(1, call_count)
        self.assertEqual(1, len(failures))
        self.assertIn("TerminalMainDocumentError", failures[0])

    def test_report_only_and_enforced_modes_keep_status_separate_from_exit(self):
        for report_only, expected_failure_count in ((True, 0), (False, 1)):
            with self.subTest(report_only=report_only):
                page = self.context.new_page()
                directory = (
                    Path(self.temp.name)
                    / f"mode-{str(report_only).lower()}"
                )
                session = RuntimeHealthSession(
                    page,
                    {"site": "fixture"},
                    {
                        "url": "https://fixture.test",
                        "runtime_health": {
                            "reporting": {
                                "report_only": report_only,
                                "affect_exit_code": True,
                                "fail_on_failed": True,
                                "fail_on_warning": False,
                            },
                            "loading_confirmation_ms": 0,
                        },
                    },
                    "home",
                    evidence_directory=directory,
                )
                session.start_before_navigation()
                page.set_content("<html><body></body></html>")
                session.complete_navigation(
                    {"final_url": "https://fixture.test", "status": 200}
                )
                session.collect_after_ready()
                summary = session.finalize("passed")
                persisted = json.loads(
                    (directory / "attempt-1.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual("failed", summary["runtime_status"])
                self.assertEqual("failed", persisted["runtime_status"])
                self.assertEqual(
                    expected_failure_count,
                    len(runtime_failure_messages(summary)),
                )
                page.close()

    def test_repeated_health_collection_replaces_pre_visual_fingerprint(self):
        session = RuntimeHealthSession(
            self.page,
            {"site": "fixture"},
            {
                "url": "https://fixture.test",
                "runtime_health": {"loading_confirmation_ms": 0},
            },
            "home",
            evidence_directory=Path(self.temp.name) / "refresh",
        )
        self.page.set_content("<main>First navigation content</main>")
        session.collect_after_ready()
        self.page.set_content("<main>Final navigation content</main>")
        session.collect_after_ready()
        self.assertIn(
            "Final navigation content",
            session.pre_visual_health["body_text"],
        )
        self.assertNotIn(
            "First navigation content",
            session.pre_visual_health["body_text"],
        )

    def test_evidence_write_failure_keeps_page_summary(self):
        self.page.set_content("<main>Healthy content</main>")
        session = RuntimeHealthSession(
            self.page,
            {"site": "fixture"},
            {
                "url": "https://fixture.test",
                "runtime_health": {"loading_confirmation_ms": 0},
            },
            "home",
            evidence_directory=Path(self.temp.name) / "blocked",
        )
        session.start_before_navigation()
        session.complete_navigation(
            {"final_url": "https://fixture.test", "status": 200}
        )
        session.collect_after_ready()
        with patch.object(
            session.evidence,
            "write_attempt",
            side_effect=PermissionError("Bearer secret"),
        ):
            summary = session.finalize("passed")
        self.assertEqual("warning", summary["runtime_status"])
        self.assertIsNone(summary["runtime_evidence"])
        self.assertIn(
            "runtime_evidence_write_failed",
            {item["reason_code"] for item in summary["findings"]},
        )
        self.assertNotIn("secret", json.dumps(summary).lower())


class ContinuousNavigationAttemptTests(unittest.TestCase):
    def test_same_session_uses_continuous_attempt_ids_across_open_calls(self):
        class Response:
            status = 200

        class Body:
            @staticmethod
            def inner_text(timeout=None):
                return "healthy body"

        class Page:
            url = "https://fixture.test"

            @staticmethod
            def goto(*_args, **_kwargs):
                return Response()

            @staticmethod
            def title():
                return "Fixture"

            @staticmethod
            def locator(_selector):
                return Body()

            @staticmethod
            def is_closed():
                return False

            @staticmethod
            def on(_name, _callback):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            session = RuntimeHealthSession(
                Page(),
                {"site": "fixture"},
                {
                    "url": "https://fixture.test",
                    "runtime_health": {"enabled": False},
                },
                "product",
                evidence_directory=Path(temp_dir) / "runtime",
            )
            with patch(
                "playwright_checks.utils.waits.time.sleep",
                return_value=None,
            ):
                for _ in range(2):
                    navigation = session.begin_navigation()
                    result = open_page_with_retry(
                        session.page,
                        "https://fixture.test",
                        lambda _page: None,
                        on_navigation_attempt=(
                            session.record_navigation_attempt
                        ),
                        **navigation,
                    )
                    session.complete_navigation(result)

        self.assertEqual(
            [1, 2],
            [item["attempt"] for item in session.navigation.attempts],
        )
        self.assertEqual(
            [1, 2],
            [
                item["navigation_sequence"]
                for item in session.navigation.attempts
            ],
        )


if __name__ == "__main__":
    unittest.main()
