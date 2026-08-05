import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from playwright_checks.checks import product_check
from playwright_checks.core.test_results import clear_results
from playwright_checks.pages.base_page import BasePage
from playwright_checks.runtime.checks import build_findings, runtime_status
from playwright_checks.runtime.collector import RuntimeEventCollector
from playwright_checks.runtime.evidence import event_fingerprint
from playwright_checks.runtime.gray_summary import _summarize_scope
from playwright_checks.runtime.models import RuntimeEvent
from playwright_checks.runtime.session import (
    RuntimeHealthSession,
    finalize_runtime_health_fail_open,
)
from playwright_checks.utils.waits import open_page_with_retry


FIRST_SEEN = "2026-08-05T01:00:00.000+00:00"
SECOND_SEEN = "2026-08-05T01:00:03.000+00:00"


def runtime_event(timestamp=FIRST_SEEN):
    return RuntimeEvent(
        event_type="console",
        timestamp=timestamp,
        level="warning",
        message="fixture warning",
        source_url="https://fixture.test/app.js",
        line=7,
        column=3,
    )


def only_event(collector):
    events = collector.snapshot()["events"]
    if len(events) != 1:
        raise AssertionError(f"Expected one Runtime event, got {len(events)}")
    return events[0]


class RuntimeCollectorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.collector = RuntimeEventCollector(
            None,
            "https://fixture.test",
            {},
        )

    def test_phase_context_is_nested_exception_safe_and_does_not_leak(self):
        self.assertEqual("unknown", self.collector._phase)

        with self.collector.phase("navigation"):
            self.assertEqual("navigation", self.collector._phase)
            with self.collector.phase("variant_interaction"):
                self.assertEqual("variant_interaction", self.collector._phase)
            self.assertEqual("navigation", self.collector._phase)

        self.assertEqual("unknown", self.collector._phase)
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            with self.collector.phase("finalize"):
                self.assertEqual("finalize", self.collector._phase)
                raise RuntimeError("fixture")
        self.assertEqual("unknown", self.collector._phase)

        with self.collector.phase("navigation_retry"):
            self.assertEqual("navigation_retry", self.collector._phase)
        self.assertEqual("unknown", self.collector._phase)

        with self.assertRaises(ValueError):
            with self.collector.phase("page_check"):
                pass
        self.assertEqual("unknown", self.collector._phase)

    def test_fixed_fingerprint_is_unchanged_by_lifecycle_fields(self):
        payload = runtime_event().to_dict()
        self.assertEqual("16b6d9f6782d9151", event_fingerprint(payload))

        enriched = {
            **payload,
            "phase": "navigation",
            "navigation_sequence": 1,
            "first_seen": FIRST_SEEN,
            "last_seen": SECOND_SEEN,
            "phase_occurrences": [],
        }
        self.assertEqual(
            "16b6d9f6782d9151",
            event_fingerprint(enriched),
        )

    def test_same_fingerprint_same_phase_updates_count_and_last_seen(self):
        self.collector.set_navigation_sequence(1)
        with self.collector.phase("navigation"):
            self.collector._add(runtime_event(FIRST_SEEN))
            self.collector._add(runtime_event(SECOND_SEEN))

        event = only_event(self.collector)
        self.assertEqual(2, event["count"])
        self.assertEqual(FIRST_SEEN, event["timestamp"])
        self.assertEqual(FIRST_SEEN, event["first_seen"])
        self.assertEqual(SECOND_SEEN, event["last_seen"])
        self.assertEqual("navigation", event["phase"])
        self.assertEqual(1, event["navigation_sequence"])
        self.assertEqual(1, len(event["phase_occurrences"]))
        self.assertEqual(2, event["phase_occurrences"][0]["count"])
        self.assertEqual(
            event["count"],
            sum(item["count"] for item in event["phase_occurrences"]),
        )

    def test_same_fingerprint_across_phases_keeps_first_phase(self):
        self.collector.set_navigation_sequence(1)
        with self.collector.phase("navigation"):
            self.collector._add(runtime_event(FIRST_SEEN))
        with self.collector.phase("variant_interaction"):
            self.collector._add(runtime_event(SECOND_SEEN))

        event = only_event(self.collector)
        self.assertEqual(2, event["count"])
        self.assertEqual("navigation", event["phase"])
        self.assertEqual(
            ["navigation", "variant_interaction"],
            [item["phase"] for item in event["phase_occurrences"]],
        )

    def test_same_fingerprint_across_sequences_keeps_first_sequence(self):
        self.collector.set_navigation_sequence(1)
        with self.collector.phase("navigation"):
            self.collector._add(runtime_event(FIRST_SEEN))
        self.collector.set_navigation_sequence(2)
        with self.collector.phase("navigation"):
            self.collector._add(runtime_event(SECOND_SEEN))

        event = only_event(self.collector)
        self.assertEqual(2, event["count"])
        self.assertEqual(1, event["navigation_sequence"])
        self.assertEqual(
            [1, 2],
            [
                item["navigation_sequence"]
                for item in event["phase_occurrences"]
            ],
        )

    def test_all_handlers_read_current_phase_and_sequence(self):
        class PageError:
            stack = "fixture stack"

            def __str__(self):
                return "fixture page error"

        class ConsoleMessage:
            type = "error"
            text = "fixture console error"
            location = {
                "url": "https://fixture.test/app.js",
                "lineNumber": 4,
                "columnNumber": 2,
            }

        class Request:
            def __init__(self, url, resource_type, failure=None):
                self.url = url
                self.resource_type = resource_type
                self.failure = failure
                self.method = "GET"

        class Response:
            def __init__(self, url, status, resource_type):
                self.url = url
                self.status = status
                self.request = Request(url, resource_type)

        class Dialog:
            message = "fixture dialog"
            type = "alert"

            def __init__(self):
                self.dismissed = False

            def dismiss(self):
                self.dismissed = True

        dialog = Dialog()
        self.collector.set_navigation_sequence(2)
        with self.collector.phase("variant_interaction"):
            self.collector._on_page_error(PageError())
            self.collector._on_console(ConsoleMessage())
            self.collector._on_request_failed(
                Request(
                    "https://fixture.test/api",
                    "xhr",
                    {"errorText": "net::ERR_FAILED"},
                )
            )
            self.collector._on_response(
                Response("https://fixture.test/app.js", 500, "script")
            )
            self.collector._on_response(
                Response("https://fixture.test/page", 200, "document")
            )
            self.collector._on_dialog(dialog)
            self.collector._on_crash(None)

        events = self.collector.snapshot()["events"]
        self.assertEqual(
            {
                "page_error",
                "console",
                "request_failed",
                "http_error",
                "main_document_response",
                "dialog",
                "page_crash",
            },
            {event["event_type"] for event in events},
        )
        for event in events:
            self.assertEqual("variant_interaction", event["phase"])
            self.assertEqual(2, event["navigation_sequence"])
        self.assertTrue(dialog.dismissed)


class NavigationResponse:
    status = 200

    def __init__(self, url):
        self.url = url
        self.request = SimpleNamespace(url=url, redirected_from=None)


class HealthyBody:
    @staticmethod
    def inner_text(timeout=None):
        return "healthy fixture body"


class NavigationPage:
    url = "https://fixture.test"

    def __init__(self, timestamps, fail_first=False):
        self.timestamps = list(timestamps)
        self.fail_first = fail_first
        self.goto_calls = 0
        self.collector = None

    def goto(self, *_args, **_kwargs):
        self.goto_calls += 1
        self.collector._add(runtime_event(self.timestamps[self.goto_calls - 1]))
        if self.fail_first and self.goto_calls == 1:
            raise RuntimeError("fixture first navigation failure")
        return NavigationResponse(self.url)

    @staticmethod
    def title():
        return "Fixture"

    @staticmethod
    def locator(_selector):
        return HealthyBody()

    @staticmethod
    def is_closed():
        return False

    @staticmethod
    def on(_name, _callback):
        return None


class LifecycleProductPage(BasePage):
    page_name = "product"

    def wait_until_ready(self, page=None):
        return None


class RuntimeNavigationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp.cleanup()

    def _session(self, page):
        session = RuntimeHealthSession(
            page,
            {"site": "fixture"},
            {
                "url": page.url,
                "runtime_health": {"enabled": False},
            },
            "product",
            evidence_directory=Path(self.temp.name) / "runtime",
        )
        page.collector = session.collector
        return session

    def test_navigation_retry_uses_retry_phase_without_new_sequence(self):
        page = NavigationPage(
            [FIRST_SEEN, SECOND_SEEN],
            fail_first=True,
        )
        session = self._session(page)
        navigation = session.begin_navigation()

        with patch(
            "playwright_checks.utils.waits.time.sleep",
            return_value=None,
        ):
            result = open_page_with_retry(
                page,
                page.url,
                lambda _page: None,
                attempts=2,
                delay=0,
                on_navigation_attempt=session.record_navigation_attempt,
                navigation_attempt_phase=session.navigation_attempt_phase,
                **navigation,
            )
        session.complete_navigation(result)

        event = only_event(session.collector)
        self.assertEqual(2, event["count"])
        self.assertEqual(
            [("navigation", 1), ("navigation_retry", 1)],
            [
                (item["phase"], item["navigation_sequence"])
                for item in event["phase_occurrences"]
            ],
        )
        self.assertEqual(
            [1, 1],
            [item["navigation_sequence"] for item in session.navigation.attempts],
        )
        self.assertEqual(
            [1, 2],
            [item["sequence_attempt"] for item in session.navigation.attempts],
        )

        health = {
            "title": "Fixture",
            "body_text": "healthy fixture body",
            "body_text_length": 100,
            "dom_node_count": 30,
            "visible_image_count": 1,
            "loading_visible_count": 0,
            "critical_elements": [],
            "missing_critical_elements": [],
            "missing_optional_elements": [],
            "page_height": 900,
            "viewport_height": 800,
        }
        findings = build_findings(
            session.navigation.to_dict(),
            session.collector.snapshot(),
            health,
            session.config,
        )
        recovered = [
            item for item in findings
            if item.reason_code == "navigation_retry_recovered"
        ]
        self.assertEqual(1, len(recovered))
        self.assertEqual(1, recovered[0].count)
        self.assertEqual("warning", runtime_status(findings))

    def test_two_base_page_open_calls_use_sequences_one_and_two(self):
        page = NavigationPage([FIRST_SEEN, SECOND_SEEN])
        model = LifecycleProductPage(
            page,
            site_config={
                "site": "fixture",
                "pages": {
                    "product": {
                        "url": page.url,
                        "runtime_health": {"enabled": False},
                    }
                },
            },
        )
        page.collector = model.runtime.collector

        with patch(
            "playwright_checks.utils.waits.time.sleep",
            return_value=None,
        ):
            model.open()
            model.open()

        event = only_event(model.runtime.collector)
        self.assertEqual(2, event["count"])
        self.assertEqual(1, event["navigation_sequence"])
        self.assertEqual(
            [1, 2],
            [
                item["navigation_sequence"]
                for item in event["phase_occurrences"]
            ],
        )
        self.assertEqual(
            [1, 2],
            [
                item["navigation_sequence"]
                for item in model.runtime.navigation.attempts
            ],
        )


class ProductVariantRuntime:
    def __init__(self):
        self.collector = RuntimeEventCollector(
            None,
            "https://fixture.test/product",
            {},
        )
        self.navigation_sequence = 0

    def begin_navigation(self):
        self.navigation_sequence += 1
        self.collector.set_navigation_sequence(self.navigation_sequence)

    def phase(self, name):
        return self.collector.phase(name)

    @staticmethod
    def page_available():
        return True


class ProductRunModel:
    modules = {}
    dom_presence = {}

    def __init__(self, runtime):
        self.runtime = runtime

    def open(self):
        self.runtime.begin_navigation()

    @staticmethod
    def wait_until_ready():
        return None


class ProductRunContext:
    page_name = "product"
    site = "fixture"
    suite = "visual"
    site_config = {"site": "fixture"}
    page_config = {}
    baseline_dir = "<baseline>"
    current_dir = "<current>"
    diff_dir = "<diff>"
    legacy_baseline_dir = None

    def __init__(self):
        self.artifact_manager = SimpleNamespace(
            finalize_page=lambda _failed: None,
        )

    @staticmethod
    def locator(_key, default=None):
        return default


class ProductAndFinalizeLifecycleTests(unittest.TestCase):
    def setUp(self):
        clear_results()

    def tearDown(self):
        clear_results()

    def test_product_variant_outer_call_marks_event_with_sequence_two(self):
        runtime = ProductVariantRuntime()
        model = ProductRunModel(runtime)
        context = ProductRunContext()
        page = SimpleNamespace()

        def variant_event(_ctx, _model):
            runtime.collector._add(runtime_event(FIRST_SEEN))
            return {}, []

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    product_check,
                    "PageCheckContext",
                    return_value=context,
                )
            )
            stack.enter_context(patch.object(product_check, "create_dirs"))
            stack.enter_context(
                patch.object(
                    product_check,
                    "init_browser",
                    return_value=(None, None, None, page),
                )
            )
            stack.enter_context(
                patch.object(product_check, "ProductPage", return_value=model)
            )
            stack.enter_context(
                patch.object(product_check.time, "sleep", return_value=None)
            )
            replacements = {
                "collect_runtime_health_fail_open": None,
                "monitoring_product_is_unavailable": False,
                "dom_check": [],
                "dom_presence_check": [],
                "check_product_content": [],
                "check_add_to_cart": [],
                "check_variant_count": None,
                "run_structure_checks": ([], []),
                "capture_product_main": {},
                "hide_dynamic_elements": None,
                "capture_global_screenshot": {},
                "capture_first_screen": {},
                "is_mobile_viewport": False,
                "process_results": [],
                "finalize_runtime_health_fail_open": [],
                "close_browser": None,
            }
            for name, return_value in replacements.items():
                stack.enter_context(
                    patch.object(
                        product_check,
                        name,
                        return_value=return_value,
                    )
                )
            stack.enter_context(
                patch.object(
                    product_check,
                    "test_variants",
                    side_effect=variant_event,
                )
            )
            self.assertEqual([], product_check.run())

        event = only_event(runtime.collector)
        self.assertEqual("variant_interaction", event["phase"])
        self.assertEqual(2, event["navigation_sequence"])

    def test_finalize_phase_restores_after_success_and_failure(self):
        class FinalizeSession:
            config = {
                "reporting": {
                    "report_only": True,
                    "affect_exit_code": False,
                }
            }

            def __init__(self, fail=False):
                self.collector = RuntimeEventCollector(
                    None,
                    "https://fixture.test",
                    {},
                )
                self.fail = fail

            def phase(self, name):
                return self.collector.phase(name)

            @staticmethod
            def capture_post_visual_state():
                return None

            def finalize(self, _visual_status):
                self.collector._add(runtime_event(FIRST_SEEN))
                if self.fail:
                    raise RuntimeError("fixture finalize failure")
                return {
                    "runtime_status": "passed",
                    "runtime_affects_exit_code": False,
                    "findings": [],
                }

        with patch(
            "playwright_checks.runtime.session.get_page_visual_status",
            return_value="passed",
        ):
            successful = FinalizeSession()
            self.assertEqual(
                [],
                finalize_runtime_health_fail_open(
                    successful,
                    "fixture",
                    "home",
                    "desktop",
                ),
            )
            self.assertEqual("unknown", successful.collector._phase)
            self.assertEqual("finalize", only_event(successful.collector)["phase"])

            failing = FinalizeSession(fail=True)
            self.assertEqual(
                [],
                finalize_runtime_health_fail_open(
                    failing,
                    "fixture",
                    "home",
                    "desktop",
                ),
            )
            self.assertEqual("unknown", failing.collector._phase)
            self.assertEqual("finalize", only_event(failing.collector)["phase"])

    def test_lifecycle_fields_do_not_change_findings_or_gray_totals(self):
        old_event = {
            "event_type": "console",
            "level": "warning",
            "message": "fixture warning",
            "party": "first_party",
            "blocking": True,
            "count": 2,
            "timestamp": FIRST_SEEN,
            "fingerprint": "16b6d9f6782d9151",
        }
        new_event = {
            **old_event,
            "phase": "navigation",
            "navigation_sequence": 1,
            "first_seen": FIRST_SEEN,
            "last_seen": SECOND_SEEN,
            "phase_occurrences": [
                {
                    "phase": "navigation",
                    "navigation_sequence": 1,
                    "first_seen": FIRST_SEEN,
                    "last_seen": SECOND_SEEN,
                    "count": 2,
                }
            ],
        }
        navigation = {"status": 200, "attempts": []}
        health = {
            "title": "Fixture",
            "body_text": "healthy fixture body",
            "body_text_length": 100,
            "dom_node_count": 30,
            "visible_image_count": 1,
            "loading_visible_count": 0,
            "critical_elements": [],
            "missing_critical_elements": [],
            "missing_optional_elements": [],
            "page_height": 900,
            "viewport_height": 800,
        }
        config = {"blank_page_text_threshold": 30, "blank_page_node_threshold": 20}

        def finding_signature(events):
            findings = build_findings(
                navigation,
                {
                    "events": events,
                    "collector_errors": [],
                    "page_crashed": False,
                },
                health,
                config,
            )
            return (
                [
                    (
                        item.reason_code,
                        item.severity,
                        item.count,
                        item.category,
                    )
                    for item in findings
                ],
                runtime_status(findings),
            )

        self.assertEqual(
            finding_signature([old_event]),
            finding_signature([new_event]),
        )

        expected = {
            "site": "fixture",
            "viewport": "desktop",
            "page": "home",
        }
        page_summary = {
            **expected,
            "runtime_status": "warning",
            "runtime_affects_exit_code": False,
            "findings": [],
        }

        def summarize(event):
            return _summarize_scope(
                expected,
                {
                    **expected,
                    "runtime_status": "warning",
                    "runtime_affects_exit_code": False,
                    "findings": [],
                    "events": [event],
                    "pre_visual_health": {"loading_visible_count": 0},
                },
                1,
                page_summary,
                [],
            )

        self.assertEqual(summarize(old_event), summarize(new_event))


if __name__ == "__main__":
    unittest.main()
