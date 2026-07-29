import unittest
from pathlib import Path
from urllib.parse import urlsplit

from playwright_checks.core.config_loader import (
    get_page_config,
    load_site_config,
)
from playwright_checks.pages.base_page import BasePage
from playwright_checks.runtime.collector import RuntimeEventCollector


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "configs" / "sites" / "mondressy_US.yaml"
EXPECTED_BASE_URL = "https://mondressy.com"
EXPECTED_PAGE_URLS = {
    "home": "https://mondressy.com",
    "collection": (
        "https://mondressy.com/collections/wedding-guest-dresses"
    ),
    "product": (
        "https://mondressy.com/products/"
        "a-line-princess-sleeveless-tea-length-"
        "wedding-guest-dresses-mon2311613"
    ),
}


class MondressyHostConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_site_config.cache_clear()
        cls.site_config = load_site_config("mondressy_US")

    @classmethod
    def tearDownClass(cls):
        load_site_config.cache_clear()

    def test_base_and_page_entries_use_only_bare_host(self):
        self.assertEqual(
            EXPECTED_BASE_URL,
            self.site_config["base_url"],
        )
        self.assertEqual(
            ["mondressy.com"],
            self.site_config["signed_request_hosts"],
        )
        for page_name, expected_url in EXPECTED_PAGE_URLS.items():
            with self.subTest(page=page_name):
                page_config = get_page_config(
                    page_name,
                    self.site_config,
                    viewport="desktop",
                )
                self.assertEqual(expected_url, page_config["url"])
                self.assertEqual(
                    "mondressy.com",
                    urlsplit(page_config["url"]).hostname,
                )

    def test_production_site_config_has_no_www_host(self):
        content = CONFIG_PATH.read_text(encoding="utf-8").lower()
        self.assertNotIn("www.mondressy.com", content)

    def test_page_identity_accepts_configured_bare_domain(self):
        model = BasePage.__new__(BasePage)
        model.config = {"expected_keywords": ["mondressy"]}
        model._read_page_identity = lambda _target: ("", "")
        target = type(
            "Target",
            (),
            {"url": "https://mondressy.com/"},
        )()

        model.assert_expected_keywords(target)

    def test_runtime_treats_bare_and_redirected_www_as_first_party(self):
        collector = RuntimeEventCollector(
            page=None,
            requested_url=EXPECTED_PAGE_URLS["home"],
            config={},
        )
        self.assertEqual(
            "first_party",
            collector.classify_url("https://mondressy.com/theme.js"),
        )
        self.assertEqual(
            "first_party",
            collector.classify_url("https://www.mondressy.com/error"),
        )
        self.assertEqual(
            "third_party",
            collector.classify_url("https://example.test/script.js"),
        )


if __name__ == "__main__":
    unittest.main()
