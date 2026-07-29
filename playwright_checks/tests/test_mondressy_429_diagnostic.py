import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import playwright_checks.diagnostics.mondressy_429 as diagnostic
from playwright_checks.diagnostics.mondressy_429 import (
    ACCEPT,
    ACCEPT_LANGUAGE,
    CACHE_CONTROL,
    CREDENTIALS,
    REFERER,
    USER_AGENT,
    DiagnosticConfigurationError,
    _body_fields,
    _document_request_audit,
    _install_external_signature_stripping,
    _new_result,
    _safe_url,
    analyze_signature_input,
    chromium_extra_http_headers,
    common_request_headers,
    interpret_results,
    read_credentials,
    run_api_request_probe,
    run_diagnostics,
)


TEST_ENV = {
    "MONDRESSY_US_SHOPIFY_SIGNATURE": "sentinel-signature-secret",
    "MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT": (
        'sig1=("@method" "@authority" "@path" "signature-agent");'
        "created=1700000000;expires=1700000300;keyid=\"fixture\""
    ),
    "MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT": "sentinel-agent-secret",
}


class FakeRequest:
    resource_type = "document"
    url = "https://www.mondressy.com/"

    def all_headers(self):
        return {
            "signature": TEST_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE"],
            "signature-input": TEST_ENV[
                "MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT"
            ],
            "signature-agent": TEST_ENV[
                "MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT"
            ],
            "referer": REFERER,
            "accept-language": ACCEPT_LANGUAGE,
            "user-agent": USER_AGENT,
            "cookie": "must-not-be-returned",
        }


class FakeRouteRequest:
    def all_headers(self):
        return {
            "signature": TEST_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE"],
            "signature-input": TEST_ENV[
                "MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT"
            ],
            "signature-agent": TEST_ENV[
                "MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT"
            ],
            "cookie": "preserved-but-never-logged",
            "accept": ACCEPT,
        }


class FakeRoute:
    def __init__(self):
        self.request = FakeRouteRequest()
        self.continued_headers = None

    def continue_(self, headers):
        self.continued_headers = dict(headers)


class FakeContext:
    def route(self, matcher, handler):
        self.matcher = matcher
        self.handler = handler


class FakeBrowser:
    def close(self):
        pass


class FakeChromium:
    def launch(self, **_kwargs):
        return FakeBrowser()


class FakePlaywright:
    chromium = FakeChromium()


class FakePlaywrightManager:
    def __enter__(self):
        return FakePlaywright()

    def __exit__(self, *_args):
        return False


class FakeAPIResponse:
    def __init__(
        self,
        status=200,
        url="https://mondressy.com/",
        headers=None,
        body=b"normal",
    ):
        self.status = status
        self.url = url
        self.headers = dict(headers or {})
        self._body = body
        self.disposed = False

    def body(self):
        return self._body

    def dispose(self):
        self.disposed = True


class FakeAPIRequestContext:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.disposed = False

    def get(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return self.response

    def dispose(self):
        self.disposed = True


class FakeAPIRequestFactory:
    def __init__(self, contexts):
        self.contexts = list(contexts)

    def new_context(self, **_kwargs):
        return self.contexts.pop(0)


class FakeAPIPlaywright:
    def __init__(self, contexts):
        self.request = FakeAPIRequestFactory(contexts)


def completed_result(probe, host, initial_url):
    result = _new_result(probe, host, initial_url)
    result.update(
        {
            "status": 200,
            "final_url": initial_url,
            "duration_ms": 1.0,
            "body_category": "normal",
        }
    )
    if probe == "Chromium":
        result["main_document_requests"] = []
    return result


class Mondressy429DiagnosticTests(unittest.TestCase):
    def test_credentials_reject_empty_values_without_outputting_secrets(self):
        environment = dict(TEST_ENV)
        environment["MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT"] = "  "
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(DiagnosticConfigurationError) as captured:
                read_credentials()

        message = str(captured.exception)
        self.assertIn("MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT", message)
        for secret in TEST_ENV.values():
            self.assertNotIn(secret, message)

    def test_signature_input_reports_only_timing_and_components(self):
        metadata = analyze_signature_input(
            TEST_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT"]
        )
        self.assertTrue(metadata["created_present"])
        self.assertTrue(metadata["expires_present"])
        self.assertEqual(
            ["@method", "@authority", "@path", "signature-agent"],
            metadata["covered_components"],
        )
        self.assertNotIn("keyid", metadata)

    def test_common_headers_are_identical_and_browser_uses_required_options(self):
        signature_headers = {
            header_name: TEST_ENV[env_name]
            for env_name, header_name in CREDENTIALS
        }
        common = common_request_headers(signature_headers)
        self.assertEqual(USER_AGENT, common["User-Agent"])
        self.assertEqual(ACCEPT, common["Accept"])
        self.assertEqual(ACCEPT_LANGUAGE, common["Accept-Language"])
        self.assertEqual(CACHE_CONTROL, common["Cache-Control"])
        self.assertEqual(REFERER, common["Referer"])

        browser_headers = chromium_extra_http_headers(signature_headers)
        self.assertEqual(ACCEPT, browser_headers["Accept"])
        self.assertEqual(ACCEPT_LANGUAGE, browser_headers["Accept-Language"])
        self.assertEqual(CACHE_CONTROL, browser_headers["Cache-Control"])
        self.assertNotIn("User-Agent", browser_headers)
        self.assertNotIn("Referer", browser_headers)

    def test_document_audit_contains_only_safe_required_fields(self):
        audit = _document_request_audit(FakeRequest())
        self.assertEqual(
            {
                "signature_present",
                "signature_input_present",
                "signature_agent_present",
                "referer_present",
                "accept_language_present",
                "user_agent",
                "requested_url",
                "resource_type",
            },
            set(audit),
        )
        self.assertTrue(audit["signature_present"])
        self.assertTrue(audit["signature_input_present"])
        self.assertTrue(audit["signature_agent_present"])
        self.assertNotIn("must-not-be-returned", repr(audit))
        for secret in TEST_ENV.values():
            self.assertNotIn(secret, repr(audit))

    def test_local_rate_limited_body_is_categorized_and_preview_is_redacted(self):
        body = (
            b"local_rate_limited "
            + TEST_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE"].encode("utf-8")
        )
        fields = _body_fields(
            body,
            429,
            "text/plain; charset=utf-8",
            tuple(TEST_ENV.values()),
        )
        self.assertEqual("local_rate_limited", fields["body_category"])
        self.assertEqual(len(body), fields["body_length"])
        self.assertIn("[REDACTED]", fields["body_preview"])
        self.assertNotIn(
            TEST_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE"],
            fields["body_preview"],
        )

    def test_api_request_reads_supported_headers_status_and_body_safely(self):
        response = FakeAPIResponse(
            status=429,
            headers={
                "server": "fixture-origin",
                "cf-ray": "fixture-ray",
                "retry-after": "30",
                "content-type": "text/plain; charset=utf-8",
            },
            body=(
                b"local_rate_limited "
                + TEST_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE"].encode("utf-8")
            ),
        )
        context = FakeAPIRequestContext(response=response)
        playwright = FakeAPIPlaywright([context])

        result = run_api_request_probe(
            playwright,
            "mondressy.com",
            "https://mondressy.com/",
            common_request_headers(
                {
                    header_name: TEST_ENV[env_name]
                    for env_name, header_name in CREDENTIALS
                }
            ),
            tuple(TEST_ENV.values()),
        )

        self.assertEqual(429, result["status"])
        self.assertEqual("fixture-origin", result["server"])
        self.assertEqual("fixture-ray", result["cf_ray"])
        self.assertEqual("30", result["retry_after"])
        self.assertEqual(
            "local_rate_limited",
            result["body_category"],
        )
        self.assertTrue(response.disposed)
        self.assertTrue(context.disposed)
        for secret in TEST_ENV.values():
            self.assertNotIn(secret, repr(result))

    def test_api_request_exception_is_contained_for_later_probes(self):
        failed_context = FakeAPIRequestContext(
            error=RuntimeError(
                TEST_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE"]
            )
        )
        successful_response = FakeAPIResponse()
        successful_context = FakeAPIRequestContext(
            response=successful_response
        )
        playwright = FakeAPIPlaywright(
            [failed_context, successful_context]
        )
        headers = common_request_headers(
            {
                header_name: TEST_ENV[env_name]
                for env_name, header_name in CREDENTIALS
            }
        )
        secrets = tuple(TEST_ENV.values())

        failed = run_api_request_probe(
            playwright,
            "mondressy.com",
            "https://mondressy.com/",
            headers,
            secrets,
        )
        succeeded = run_api_request_probe(
            playwright,
            "www.mondressy.com",
            "https://www.mondressy.com/",
            headers,
            secrets,
        )

        self.assertEqual("probe_error", failed["body_category"])
        self.assertIsNotNone(failed["error"])
        self.assertEqual(200, succeeded["status"])
        self.assertEqual("normal", succeeded["body_category"])
        self.assertTrue(failed_context.disposed)
        self.assertTrue(successful_context.disposed)
        for secret in TEST_ENV.values():
            self.assertNotIn(secret, repr(failed))

    def test_sensitive_redirect_query_is_redacted(self):
        safe = _safe_url(
            "https://www.mondressy.com/?token=secret-value&page=2",
            (),
        )
        self.assertIn("token=%5BREDACTED%5D", safe)
        self.assertIn("page=2", safe)
        self.assertNotIn("secret-value", safe)

    def test_external_route_only_strips_signatures_from_non_target_hosts(self):
        context = FakeContext()
        _install_external_signature_stripping(context)
        self.assertFalse(context.matcher("https://www.mondressy.com/"))
        self.assertFalse(context.matcher("https://mondressy.com/asset.js"))
        self.assertTrue(context.matcher("https://cdn.example.test/asset.js"))

        route = FakeRoute()
        context.handler(route)
        self.assertEqual(ACCEPT, route.continued_headers["accept"])
        self.assertEqual(
            "preserved-but-never-logged",
            route.continued_headers["cookie"],
        )
        self.assertNotIn("signature", route.continued_headers)
        self.assertNotIn("signature-input", route.continued_headers)
        self.assertNotIn("signature-agent", route.continued_headers)

    def test_probe_order_is_six_calls_and_report_contains_no_credentials(self):
        calls = []

        def fake_curl(host, initial_url, _headers, _secrets):
            calls.append(("curl", host))
            return completed_result("curl", host, initial_url)

        def fake_api(_playwright, host, initial_url, _headers, _secrets):
            calls.append(("APIRequest", host))
            return completed_result("APIRequest", host, initial_url)

        def fake_chromium(
            _browser,
            host,
            initial_url,
            _signature_headers,
            _secrets,
        ):
            calls.append(("Chromium", host))
            return completed_result("Chromium", host, initial_url)

        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            with (
                patch.dict(os.environ, TEST_ENV, clear=True),
                patch.object(diagnostic, "run_curl_probe", fake_curl),
                patch.object(diagnostic, "run_api_request_probe", fake_api),
                patch.object(diagnostic, "run_chromium_probe", fake_chromium),
                patch.object(
                    diagnostic,
                    "sync_playwright",
                    return_value=FakePlaywrightManager(),
                ),
                redirect_stdout(output),
            ):
                report = run_diagnostics(report_path)

            serialized = report_path.read_text(encoding="utf-8")

        self.assertEqual(
            [
                ("curl", "mondressy.com"),
                ("curl", "www.mondressy.com"),
                ("APIRequest", "mondressy.com"),
                ("APIRequest", "www.mondressy.com"),
                ("Chromium", "mondressy.com"),
                ("Chromium", "www.mondressy.com"),
            ],
            calls,
        )
        self.assertEqual(6, len(report["results"]))
        combined = output.getvalue() + serialized
        for secret in TEST_ENV.values():
            self.assertNotIn(secret, combined)

    def test_result_interpretation_matches_required_decision_rules(self):
        def matrix(curl_status, api_status, chromium_status):
            results = []
            for probe, status in (
                ("curl", curl_status),
                ("APIRequest", api_status),
                ("Chromium", chromium_status),
            ):
                for host, url in diagnostic.TARGETS:
                    result = completed_result(probe, host, url)
                    result["status"] = status
                    results.append(result)
            return results

        browser = interpret_results(matrix(200, 200, 429))
        self.assertTrue(browser["browser_fingerprint_difference"])
        self.assertEqual(
            "browser_fingerprint_client_hints_or_cookie_difference",
            browser["assessment"],
        )

        stack = interpret_results(matrix(200, 429, 429))
        self.assertTrue(stack["playwright_http_stack_difference"])
        self.assertEqual(
            "playwright_headers_host_signature_or_http_stack_difference",
            stack["assessment"],
        )

        origin = interpret_results(matrix(429, 429, 429))
        self.assertTrue(origin["all_429"])
        self.assertEqual(
            "origin_ip_signature_expiry_or_upstream_rate_limit",
            origin["assessment"],
        )

    def test_result_interpretation_detects_host_effect_and_route_resolution(self):
        results = []
        for probe in ("curl", "APIRequest", "Chromium"):
            for host, url in diagnostic.TARGETS:
                result = completed_result(probe, host, url)
                result["status"] = (
                    200 if host == "mondressy.com" else 429
                )
                results.append(result)
        host_effect = interpret_results(results)
        self.assertTrue(host_effect["host_affects_result"])
        self.assertEqual(
            "host_authority_or_redirect_signature_difference",
            host_effect["assessment"],
        )

        resolved = interpret_results(
            [
                completed_result(probe, host, url)
                for probe in ("curl", "APIRequest", "Chromium")
                for host, url in diagnostic.TARGETS
            ]
        )
        self.assertTrue(
            resolved["extra_http_headers_resolved_existing_www_429"]
        )
        self.assertEqual(
            "original_route_injection_implementation_difference",
            resolved["assessment"],
        )

    def test_missing_credentials_stop_before_any_probe(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True):
            with redirect_stdout(output):
                with self.assertRaises(DiagnosticConfigurationError):
                    run_diagnostics()

        text = output.getvalue()
        for env_name, _header_name in CREDENTIALS:
            self.assertIn(f"{env_name}: present=false", text)
        self.assertNotIn("probe=curl", text)


if __name__ == "__main__":
    unittest.main()
