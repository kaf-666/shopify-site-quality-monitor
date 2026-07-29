import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from playwright_checks.core.config_loader import get_runtime_health_config
from playwright_checks.core.test_results import clear_results, get_results
from playwright_checks.runtime.evidence import redact_url
from playwright_checks.runtime.session import (
    FailOpenRuntimeHealthSession,
    RuntimeHealthSession,
    finalize_runtime_health_fail_open,
    runtime_failure_messages,
)
from playwright_checks.utils.visual import build_result
from playwright_checks.utils.waits import (
    TerminalMainDocumentError,
    open_page_with_retry,
)


NORMAL_HTML = """
<!doctype html>
<html>
  <head><title>Fixture Store</title></head>
  <body>
    <main class="main">
      <h1>Fixture Store</h1>
      <p>This page has enough meaningful content to represent a healthy page.</p>
      <img
        alt="Fixture product"
        width="16"
        height="16"
        src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="
      >
      <button type="button">Continue</button>
      <section><p>Products and editorial content are ready.</p></section>
    </main>
  </body>
</html>
"""


class RuntimeHealthPlaywrightTests(unittest.TestCase):
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
        self.temp = tempfile.TemporaryDirectory()
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()
        self.temp.cleanup()
        clear_results()

    def _session(self, page=None, page_config=None, directory=None):
        config = {
            "url": "https://fixture.test/runtime",
            "modules": {
                "main": ["css", ".main"],
            },
        }
        if page_config:
            config.update(page_config)
        return RuntimeHealthSession(
            page=page or self.page,
            site_config={"site": "fixture"},
            page_config=config,
            page_name="home",
            evidence_directory=directory
            or Path(self.temp.name) / "runtime",
        )

    @staticmethod
    def _set_navigation(session, status=200):
        session.record_navigation_attempt(
            {
                "attempt": 1,
                "requested_url": "https://fixture.test/runtime",
                "final_url": "https://fixture.test/runtime",
                "status": status,
                "redirected": False,
            }
        )
        session.complete_navigation(
            {
                "final_url": "https://fixture.test/runtime",
                "status": status,
                "redirected": False,
            }
        )

    def _finalize(self, session, status=200):
        self._set_navigation(session, status)
        session.collect_after_ready()
        session.capture_post_visual_state()
        return session.finalize("passed")

    def test_normal_page_passes_and_writes_runtime_evidence(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(NORMAL_HTML)

        summary = self._finalize(session)

        self.assertEqual("passed", summary["runtime_status"])
        self.assertEqual("passed", summary["status"])
        evidence_path = Path(self.temp.name) / "runtime" / "attempt-1.json"
        self.assertTrue(evidence_path.exists())
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(200, payload["navigation"]["status"])
        self.assertEqual(
            200,
            payload["pre_visual_health"]["main_document_status"],
        )
        self.assertGreaterEqual(
            payload["pre_visual_health"]["visible_image_count"],
            1,
        )
        self.assertEqual([], payload["findings"])

    def test_terminal_status_writes_minimal_anomaly_page_evidence(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content("<title>Rate limited</title><body>limited</body>")

        summary = self._finalize(session, status=429)

        payload = json.loads(
            (Path(self.temp.name) / "runtime" / "attempt-1.json").read_text(
                encoding="utf-8"
            )
        )
        terminal = payload["pre_visual_health"]["terminal_page_evidence"]
        self.assertEqual(429, terminal["status"])
        self.assertEqual(
            "https://fixture.test/runtime",
            terminal["final_url"],
        )
        self.assertIn("body_text_length", terminal)
        self.assertEqual("failed", summary["runtime_status"])

    def test_page_error_is_warning_when_core_content_is_present(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(
            NORMAL_HTML.replace(
                "</body>",
                "<script>setTimeout(() => { throw new Error('boom'); }, 0)</script>"
                "</body>",
            )
        )
        self.page.wait_for_timeout(50)

        summary = self._finalize(session)

        self.assertEqual("warning", summary["runtime_status"])
        self.assertIn(
            "page_error",
            {item["reason_code"] for item in summary["findings"]},
        )

    def test_console_warning_is_deduplicated_and_reported(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(
            NORMAL_HTML.replace(
                "</body>",
                """
                <script>
                  console.warn("fixture warning");
                  console.warn("fixture warning");
                </script>
                </body>
                """,
            )
        )

        summary = self._finalize(session)
        payload = json.loads(
            (Path(self.temp.name) / "runtime" / "attempt-1.json").read_text(
                encoding="utf-8"
            )
        )
        console_events = [
            event
            for event in payload["events"]
            if event["event_type"] == "console"
        ]

        self.assertEqual("warning", summary["runtime_status"])
        self.assertEqual(1, len(console_events))
        self.assertEqual(2, console_events[0]["count"])
        self.assertIn(
            "console_warning",
            {item["reason_code"] for item in summary["findings"]},
        )

    def test_third_party_request_failure_is_warning(self):
        session = self._session()
        session.start_before_navigation()
        self.page.route(
            "https://www.google-analytics.com/**",
            lambda route: route.abort("failed"),
        )
        self.page.set_content(NORMAL_HTML)
        self.page.evaluate(
            """
            fetch("https://www.google-analytics.com/collect")
              .catch(() => undefined)
            """
        )
        self.page.wait_for_timeout(50)

        summary = self._finalize(session)

        self.assertEqual("warning", summary["runtime_status"])
        self.assertIn(
            "third_party_error",
            {item["reason_code"] for item in summary["findings"]},
        )

    def test_first_party_xhr_500_is_failed(self):
        session = self._session()
        session.start_before_navigation()
        self.page.route(
            "https://fixture.test/api",
            lambda route: route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":"fixture"}',
            ),
        )
        self.page.set_content(NORMAL_HTML)
        self.page.evaluate(
            """
            fetch("https://fixture.test/api")
              .catch(() => undefined)
            """
        )
        self.page.wait_for_timeout(50)

        summary = self._finalize(session)

        self.assertEqual("failed", summary["runtime_status"])
        self.assertIn(
            "first_party_server_error",
            {item["reason_code"] for item in summary["findings"]},
        )

    def test_main_document_500_is_failed(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(NORMAL_HTML)

        summary = self._finalize(session, status=500)

        self.assertEqual("failed", summary["runtime_status"])
        self.assertEqual("network_error", summary["primary_failure_reason"])
        self.assertEqual("network_error", summary["primary_failure_type"])
        self.assertEqual("failed", summary["overall_status"])

    def test_http_200_application_error_page_is_failed(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(
            """
            <html><head><title>Application Error</title></head>
            <body><h1>Application Error</h1>
            <p>Something went wrong. Please try again later.</p></body></html>
            """
        )

        summary = self._finalize(session)

        self.assertEqual("failed", summary["runtime_status"])
        self.assertIn(
            summary["primary_failure_reason"],
            {"application_error", "partial_render_failure"},
        )
        self.assertEqual("error_page", summary["primary_failure_type"])

    def test_blank_page_is_failed(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content("<html><head></head><body></body></html>")

        summary = self._finalize(session)

        self.assertEqual("failed", summary["runtime_status"])
        self.assertEqual("blank_page", summary["primary_failure_reason"])

    def test_configured_legitimate_empty_state_is_conservative_warning(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(
            "<html><body><p>No results</p></body></html>"
        )

        summary = self._finalize(session)

        self.assertEqual("warning", summary["runtime_status"])
        self.assertNotIn(
            "blank_page",
            {item["reason_code"] for item in summary["findings"]},
        )

    def test_missing_critical_component_plus_page_error_is_failed(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(
            """
            <html><head><title>Fixture Store</title></head>
            <body>
              <article>
                This response contains substantial fallback text, but the
                configured application root never rendered.
              </article>
              <script>setTimeout(() => { throw new Error("render failed"); }, 0)</script>
            </body></html>
            """
        )
        self.page.wait_for_timeout(50)

        summary = self._finalize(session)

        self.assertEqual("failed", summary["runtime_status"])
        self.assertIn(
            "partial_render_failure",
            {item["reason_code"] for item in summary["findings"]},
        )

    def test_visible_loading_indicator_plus_missing_core_is_failed(self):
        session = self._session(
            page_config={
                "runtime_health": {
                    "loading_selectors": [".fixture-loading"],
                    "loading_critical_selectors": ["body"],
                    "loading_confirmation_ms": 0,
                }
            }
        )
        session.start_before_navigation()
        self.page.set_content(
            """
            <html><head><title>Fixture Store</title></head>
            <body>
              <div class="fixture-loading">Loading products...</div>
              <article>
                The shell rendered, but the configured main application
                component did not finish loading.
              </article>
            </body></html>
            """
        )

        summary = self._finalize(session)

        self.assertEqual("failed", summary["runtime_status"])
        self.assertIn(
            "infinite_loading",
            {item["reason_code"] for item in summary["findings"]},
        )

    def test_dialog_is_recorded_and_dismissed(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(NORMAL_HTML)

        self.page.evaluate("alert('fixture dialog')")

        summary = self._finalize(session)
        self.assertEqual("warning", summary["runtime_status"])
        self.assertIn(
            "unexpected_dialog",
            {item["reason_code"] for item in summary["findings"]},
        )
        self.assertEqual("Fixture Store", self.page.title())

    def test_collector_internal_error_is_fail_open(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(NORMAL_HTML)

        def broken_handler(_event):
            raise RuntimeError("collector fixture failure")

        session.collector._safe_handler(
            "fixture",
            broken_handler,
            object(),
        )
        summary = self._finalize(session)

        self.assertFalse(self.page.is_closed())
        self.assertEqual("warning", summary["runtime_status"])
        self.assertIn(
            "runtime_collector_error",
            {item["reason_code"] for item in summary["findings"]},
        )

    def test_crash_event_is_severe_and_visual_can_be_not_run(self):
        session = self._session()
        session.start_before_navigation()
        self.page.set_content(NORMAL_HTML)
        session.collector._on_crash(self.page)
        self._set_navigation(session)
        session.collect_after_ready()

        summary = session.finalize("not_run")

        self.assertEqual("failed", summary["runtime_status"])
        self.assertEqual("not_run", summary["visual_status"])
        self.assertEqual("failed", summary["overall_status"])
        self.assertEqual("page_crash", summary["primary_failure_type"])

    def test_retry_summary_preserves_first_attempt(self):
        runtime_directory = Path(self.temp.name) / "retry-runtime"
        first = self._session(directory=runtime_directory)
        first.start_before_navigation()
        self.page.set_content("<html><body></body></html>")
        first_summary = self._finalize(first)
        self.assertEqual("failed", first_summary["runtime_status"])

        retry_page = self.context.new_page()
        second = self._session(
            page=retry_page,
            directory=runtime_directory,
        )
        second.start_before_navigation()
        retry_page.set_content(NORMAL_HTML)
        second_summary = self._finalize(second)

        self.assertEqual("passed", second_summary["runtime_status"])
        self.assertEqual("failed", second_summary["initial_runtime_status"])
        self.assertEqual("passed", second_summary["final_runtime_status"])
        self.assertEqual("failed", second_summary["worst_runtime_status"])
        self.assertTrue(second_summary["recovered_after_retry"])
        self.assertEqual(1, second_summary["retry_count"])
        self.assertEqual("warning", second_summary["overall_status"])
        self.assertEqual(2, len(second_summary["attempts"]))
        self.assertEqual(
            ["failed", "passed"],
            [item["status"] for item in second_summary["attempts"]],
        )
        self.assertEqual(
            "failed",
            json.loads(
                (runtime_directory / "summary.json").read_text(
                    encoding="utf-8"
                )
            )["worst_runtime_status"],
        )


class RuntimeCompatibilityTests(unittest.TestCase):
    def test_unconfigured_site_and_page_receive_safe_defaults(self):
        config = get_runtime_health_config(
            {"site": "fixture"},
            {"url": "https://fixture.test"},
        )
        self.assertTrue(config["enabled"])
        self.assertGreater(config["max_events_per_category"], 0)
        self.assertIsInstance(config["loading_selectors"], list)

    def test_runtime_config_supports_site_and_page_overrides(self):
        config = get_runtime_health_config(
            {
                "site": "fixture",
                "runtime_health": {
                    "max_events_per_category": 20,
                    "first_party_patterns": ["cdn.fixture.test"],
                },
            },
            {
                "url": "https://fixture.test",
                "runtime_health": {
                    "max_events_per_category": 5,
                },
            },
        )
        self.assertEqual(5, config["max_events_per_category"])
        self.assertEqual(
            ["cdn.fixture.test"],
            config["first_party_patterns"],
        )

    def test_disabled_runtime_health_does_not_create_false_failure(self):
        clear_results()

        class FakePage:
            url = "https://fixture.test"

            @staticmethod
            def is_closed():
                return False

            @staticmethod
            def title():
                return "Fixture"

            @staticmethod
            def on(_name, _callback):
                return None

        with tempfile.TemporaryDirectory() as temp_dir:
            session = RuntimeHealthSession(
                page=FakePage(),
                site_config={"site": "fixture"},
                page_config={
                    "url": "https://fixture.test",
                    "runtime_health": {"enabled": False},
                },
                page_name="home",
                evidence_directory=Path(temp_dir) / "runtime",
            )
            session.start_before_navigation()
            summary = session.finalize("passed")

        self.assertEqual("disabled", summary["runtime_status"])
        self.assertEqual("passed", summary["overall_status"])
        clear_results()

    def test_visual_result_keeps_legacy_fields_and_adds_discriminator(self):
        result = build_result(
            "fixture",
            "visual",
            "home",
            "global",
            "passed",
            None,
            ratio=0.0,
        )
        legacy_fields = {
            "site",
            "suite",
            "run_id",
            "viewport",
            "page",
            "case",
            "status",
            "ratio",
            "threshold",
            "warning_threshold",
            "baseline",
            "target_baseline",
            "legacy_baseline",
            "current",
            "diff",
        }
        self.assertTrue(legacy_fields.issubset(result))
        self.assertEqual("visual", result["result_type"])
        self.assertEqual(result["status"], result["visual_status"])

    def test_sensitive_query_values_are_redacted(self):
        value = redact_url(
            "https://fixture.test/path?token=secret-value&variant=blue#part"
        )
        self.assertIn("token=%5BREDACTED%5D", value)
        self.assertIn("variant=blue", value)
        self.assertNotIn("secret-value", value)
        self.assertNotIn("#part", value)

    def test_navigation_attempts_are_reported_without_changing_return_shape(self):
        class FakeResponse:
            status = 200

        class FakeBody:
            @staticmethod
            def inner_text(timeout=None):
                return "fixture body"

        class FakePage:
            def __init__(self):
                self.url = "https://fixture.test/runtime"
                self.calls = 0

            def goto(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("first navigation failed")
                return FakeResponse()

            @staticmethod
            def title():
                return "Fixture"

            @staticmethod
            def locator(_selector):
                return FakeBody()

        attempts = []
        page = FakePage()
        result = open_page_with_retry(
            page,
            "https://fixture.test/runtime",
            lambda _page: None,
            attempts=2,
            delay=0,
            on_navigation_attempt=attempts.append,
        )

        self.assertEqual(2, len(attempts))
        self.assertEqual("TimeoutError", attempts[0]["error_type"])
        self.assertEqual(200, attempts[1]["status"])
        self.assertEqual(200, result["status"])
        self.assertEqual(200, result["main_document_status"])
        self.assertEqual(2, len(result["navigation_attempts"]))
        self.assertIsNone(result["navigation_error"])
        self.assertEqual(
            [
                {
                    "url": "https://fixture.test/runtime",
                    "status": 200,
                }
            ],
            result["redirect_chain"],
        )

    def test_terminal_main_document_status_skips_readiness_and_retries(self):
        class FakeResponse:
            status = 429
            url = "https://fixture.test/runtime"

        class FakePage:
            url = "https://fixture.test/runtime"

            def __init__(self):
                self.calls = 0

            def goto(self, *_args, **_kwargs):
                self.calls += 1
                return FakeResponse()

        attempts = []
        readiness_calls = []
        page = FakePage()

        with self.assertRaises(TerminalMainDocumentError):
            open_page_with_retry(
                page,
                "https://fixture.test/runtime",
                lambda _page: readiness_calls.append(True),
                attempts=3,
                delay=0,
                on_navigation_attempt=attempts.append,
            )

        self.assertEqual(1, page.calls)
        self.assertEqual([], readiness_calls)
        self.assertEqual(1, len(attempts))
        self.assertEqual(429, attempts[0]["status"])
        self.assertEqual(
            "TerminalMainDocumentError",
            attempts[0]["error_type"],
        )
        self.assertEqual(
            [
                {
                    "url": "https://fixture.test/runtime",
                    "status": 429,
                }
            ],
            attempts[0]["redirect_chain"],
        )

    def test_navigation_redirect_chain_records_each_document_response(self):
        class FakeRequest:
            def __init__(self, url, redirected_from=None):
                self.url = url
                self.redirected_from = redirected_from
                self._response = None

            def response(self):
                return self._response

        class FakeResponse:
            def __init__(self, status, url, request):
                self.status = status
                self.url = url
                self.request = request
                request._response = self

        initial_request = FakeRequest("https://fixture.test/runtime")
        FakeResponse(302, "https://fixture.test/runtime", initial_request)
        final_request = FakeRequest(
            "https://fixture.test/final",
            redirected_from=initial_request,
        )
        final_response = FakeResponse(
            200,
            "https://fixture.test/final",
            final_request,
        )

        class FakeBody:
            @staticmethod
            def inner_text(timeout=None):
                return "fixture body"

        class FakePage:
            url = "https://fixture.test/final"

            @staticmethod
            def goto(*_args, **_kwargs):
                return final_response

            @staticmethod
            def title():
                return "Fixture"

            @staticmethod
            def locator(_selector):
                return FakeBody()

        result = open_page_with_retry(
            FakePage(),
            "https://fixture.test/runtime",
            lambda _page: None,
            attempts=1,
        )

        self.assertEqual(
            [
                {
                    "url": "https://fixture.test/runtime",
                    "status": 302,
                },
                {
                    "url": "https://fixture.test/final",
                    "status": 200,
                },
            ],
            result["redirect_chain"],
        )

    def test_page_summary_coexists_with_visual_records_in_array(self):
        clear_results()
        result = build_result(
            "fixture",
            "visual",
            "home",
            "global",
            "passed",
            None,
        )
        from playwright_checks.core.test_results import add_result

        add_result(result)
        add_result(
            {
                "result_type": "page_summary",
                "site": "fixture",
                "page": "home",
                "status": "passed",
            }
        )
        values = get_results()
        self.assertEqual(2, len(values))
        self.assertEqual("visual", values[0]["result_type"])
        self.assertEqual("page_summary", values[1]["result_type"])
        clear_results()

    def test_collector_initialization_failure_remains_non_blocking_in_ci(self):
        clear_results()

        class FakePage:
            @staticmethod
            def is_closed():
                return False

        session = FailOpenRuntimeHealthSession(
            page=FakePage(),
            site_config={"site": "fixture"},
            page_name="home",
            error=RuntimeError("fixture initialization failure"),
        )
        summary = session.finalize("passed")
        with patch.dict(os.environ, {"CI": "true"}, clear=False):
            failures = runtime_failure_messages(summary)

        self.assertEqual([], failures)
        self.assertEqual("warning", summary["runtime_status"])
        self.assertEqual("passed", summary["visual_status"])
        clear_results()

    def test_session_finalization_exception_does_not_change_visual_result(self):
        class BrokenSession:
            @staticmethod
            def capture_post_visual_state():
                raise RuntimeError("fixture finalization failure")

        failures = finalize_runtime_health_fail_open(
            BrokenSession(),
            "fixture",
            "home",
            "desktop",
        )
        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
