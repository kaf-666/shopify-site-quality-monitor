import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from playwright_checks.core.request_headers import (
    INTERCEPTED_REQUEST_PROFILE,
    install_signed_request_routing,
    is_mondressy_us_signed_url,
    load_signed_request_headers,
    mondressy_us_signed_hosts,
)


SECRET_ENV = {
    "MONDRESSY_US_SHOPIFY_SIGNATURE": "sentinel-signature-secret",
    "MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT": "sentinel-input-secret",
    "MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT": "sentinel-agent-secret",
}
SITE_CONFIG = {
    "site": "mondressy_US",
    "base_url": "https://mondressy.com",
    "signed_request_hosts": ["mondressy.com"],
}


class FakeRequest:
    def __init__(self, headers=None):
        self._headers = dict(headers or {})

    def all_headers(self):
        return dict(self._headers)


class FakeRoute:
    def __init__(self, headers=None, error=None):
        self.request = FakeRequest(headers)
        self.error = error
        self.continued_headers = None

    def continue_(self, headers):
        if self.error:
            raise self.error
        self.continued_headers = dict(headers)


class FakeContext:
    def __init__(self):
        self.matcher = None
        self.handler = None

    def route(self, matcher, handler):
        self.matcher = matcher
        self.handler = handler


class SignedRequestHeaderTests(unittest.TestCase):
    def test_only_configured_mondressy_host_matches(self):
        accepted = (
            "https://mondressy.com/",
            "http://mondressy.com/path",
        )
        rejected = (
            "https://www.mondressy.com/collections/test?x=1",
            "https://cdn.mondressy.com/file.js",
            "https://mondressy.com.evil.test/",
            "https://evil.test/?next=https://mondressy.com/",
            "ftp://mondressy.com/file",
            "not-a-url",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(
                    is_mondressy_us_signed_url(url, SITE_CONFIG)
                )
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(
                    is_mondressy_us_signed_url(url, SITE_CONFIG)
                )

    def test_signed_host_defaults_to_configured_entry_host(self):
        config = {
            "site": "mondressy_US",
            "base_url": "https://mondressy.com",
        }
        self.assertEqual(
            frozenset({"mondressy.com"}),
            mondressy_us_signed_hosts(config),
        )

    def test_route_merges_original_headers_and_replaces_signature_headers(self):
        context = FakeContext()
        with patch.dict(os.environ, SECRET_ENV, clear=True):
            profile = install_signed_request_routing(context, SITE_CONFIG)

        self.assertEqual(INTERCEPTED_REQUEST_PROFILE, profile)
        self.assertIsNotNone(context.matcher)
        self.assertIsNotNone(context.handler)
        self.assertTrue(context.matcher("https://mondressy.com/"))
        self.assertFalse(context.matcher("https://www.mondressy.com/"))
        self.assertFalse(context.matcher("https://cdn.shopify.com/file.js"))

        route = FakeRoute(
            {
                "accept": "text/html",
                "signature": "stale-value",
                "x-request-id": "fixture",
            }
        )
        context.handler(route)

        self.assertEqual("text/html", route.continued_headers["accept"])
        self.assertEqual("fixture", route.continued_headers["x-request-id"])
        self.assertEqual(
            SECRET_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE"],
            route.continued_headers["Signature"],
        )
        self.assertNotIn("signature", route.continued_headers)
        self.assertEqual(
            SECRET_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT"],
            route.continued_headers["Signature-Input"],
        )
        self.assertEqual(
            SECRET_ENV["MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT"],
            route.continued_headers["Signature-Agent"],
        )

    def test_missing_credentials_report_names_without_present_secret(self):
        partial = {
            "MONDRESSY_US_SHOPIFY_SIGNATURE": "sentinel-present-secret",
        }
        with patch.dict(os.environ, partial, clear=True):
            with self.assertRaises(RuntimeError) as captured:
                load_signed_request_headers(SITE_CONFIG)

        message = str(captured.exception)
        self.assertIn("MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT", message)
        self.assertIn("MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT", message)
        self.assertNotIn("sentinel-present-secret", message)

    def test_route_failure_does_not_expose_secret_values(self):
        context = FakeContext()
        output = io.StringIO()
        with patch.dict(os.environ, SECRET_ENV, clear=True):
            with redirect_stdout(output):
                install_signed_request_routing(context, SITE_CONFIG)
                with self.assertRaises(RuntimeError) as captured:
                    context.handler(
                        FakeRoute(
                            error=RuntimeError(
                                SECRET_ENV[
                                    "MONDRESSY_US_SHOPIFY_SIGNATURE"
                                ]
                            )
                        )
                    )

        combined = output.getvalue() + str(captured.exception)
        for value in SECRET_ENV.values():
            self.assertNotIn(value, combined)

    def test_other_sites_do_not_require_credentials_or_install_route(self):
        context = FakeContext()
        with patch.dict(os.environ, {}, clear=True):
            profile = install_signed_request_routing(
                context,
                {"site": "shirees_US"},
            )

        self.assertEqual("none", profile["request_header_injection"])
        self.assertIsNone(context.matcher)
        self.assertIsNone(context.handler)


if __name__ == "__main__":
    unittest.main()
