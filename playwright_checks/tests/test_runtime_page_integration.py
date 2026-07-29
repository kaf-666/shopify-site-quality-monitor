import unittest
from contextlib import ExitStack
from unittest.mock import patch

from playwright_checks.checks import (
    collection_check,
    home_check,
    product_check,
)
from playwright_checks.core.test_results import clear_results, get_results
from playwright_checks.runtime.session import FailOpenRuntimeHealthSession
from playwright_checks.utils.waits import TerminalMainDocumentError


class FakeContext:
    def __init__(self, page_name):
        self.page_name = page_name
        self.site = "fixture"
        self.suite = "visual"
        self.site_config = {"site": "fixture"}
        self.page_config = {"url": "https://fixture.test"}
        self.baseline_dir = "<memory-baseline>"
        self.current_dir = "<memory-current>"
        self.diff_dir = "<memory-diff>"
        self.legacy_baseline_dir = None

    @staticmethod
    def module_locators_for_capture():
        return {}


class ClosedFakePage:
    url = "https://fixture.test"

    @staticmethod
    def is_closed():
        return True


class OpenFakePage(ClosedFakePage):
    @staticmethod
    def is_closed():
        return False


class FailingPageModel:
    def __init__(self, page, page_name):
        self.runtime = FailOpenRuntimeHealthSession(
            page,
            {"site": "fixture"},
            page_name,
            RuntimeError("fixture collector initialization"),
        )

    @staticmethod
    def open():
        raise RuntimeError("fixture navigation failure")


class SuccessfulPageModel:
    modules = {}
    dom_presence = {}

    def __init__(self, page, page_name):
        self.runtime = FailOpenRuntimeHealthSession(
            page,
            {"site": "fixture"},
            page_name,
            RuntimeError("fixture collector initialization"),
        )

    @staticmethod
    def open():
        return None

    @staticmethod
    def wait_until_ready():
        return None


class TerminalPageModel(SuccessfulPageModel):
    @staticmethod
    def open():
        raise TerminalMainDocumentError(
            429,
            "https://fixture.test",
        )


class RuntimePageIntegrationTests(unittest.TestCase):
    def setUp(self):
        clear_results()

    def tearDown(self):
        clear_results()

    def _assert_exception_path_finalizes(
        self,
        module,
        page_class_name,
        page_name,
    ):
        page = ClosedFakePage()
        context = FakeContext(page_name)
        with (
            patch.object(module, "PageCheckContext", return_value=context),
            patch.object(module, "create_dirs"),
            patch.object(
                module,
                "init_browser",
                return_value=(object(), object(), object(), page),
            ),
            patch.object(
                module,
                page_class_name,
                side_effect=lambda _page, site_config=None: FailingPageModel(
                    page,
                    page_name,
                ),
            ),
            patch.object(module, "close_browser"),
        ):
            failures = module.run()

        self.assertTrue(failures)
        summaries = [
            item
            for item in get_results()
            if item.get("result_type") == "page_summary"
            and item.get("page") == page_name
        ]
        self.assertEqual(1, len(summaries))
        self.assertEqual("warning", summaries[0]["runtime_status"])
        self.assertEqual("not_run", summaries[0]["visual_status"])

    def test_home_exception_path_finalizes_page_summary(self):
        self._assert_exception_path_finalizes(
            home_check,
            "HomePage",
            "home",
        )

    def test_collection_exception_path_finalizes_page_summary(self):
        self._assert_exception_path_finalizes(
            collection_check,
            "CollectionPage",
            "collection",
        )

    def test_product_exception_path_finalizes_page_summary(self):
        self._assert_exception_path_finalizes(
            product_check,
            "ProductPage",
            "product",
        )

    def test_all_three_success_paths_finalize_page_summary(self):
        cases = [
            (
                home_check,
                "HomePage",
                "home",
                {
                    "dom_check": [],
                    "dom_presence_check": [],
                    "check_plugins": [],
                    "capture_plugins": {},
                    "hide_dynamic_elements": None,
                    "stabilize_banner": None,
                    "capture_global_screenshot": {},
                    "capture_first_screen": {},
                    "capture_modules": {},
                    "process_results": [],
                },
            ),
            (
                collection_check,
                "CollectionPage",
                "collection",
                {
                    "dom_check": [],
                    "dom_presence_check": [],
                    "check_product_count": [],
                    "hide_dynamic_elements": None,
                    "capture_global_screenshot": {},
                    "capture_first_screen": {},
                    "capture_modules": {},
                    "capture_product_cards": {},
                    "capture_hover_cards": {},
                    "process_results": [],
                },
            ),
            (
                product_check,
                "ProductPage",
                "product",
                {
                    "dom_check": [],
                    "dom_presence_check": [],
                    "check_product_content": [],
                    "check_add_to_cart": [],
                    "check_variant_count": None,
                    "hide_dynamic_elements": None,
                    "capture_global_screenshot": {},
                    "capture_first_screen": {},
                    "capture_modules": {},
                    "test_variants": ({}, []),
                    "process_results": [],
                },
            ),
        ]
        for module, page_class_name, page_name, replacements in cases:
            with self.subTest(page=page_name):
                clear_results()
                page = OpenFakePage()
                context = FakeContext(page_name)
                with ExitStack() as stack:
                    stack.enter_context(
                        patch.object(
                            module,
                            "PageCheckContext",
                            return_value=context,
                        )
                    )
                    stack.enter_context(patch.object(module, "create_dirs"))
                    stack.enter_context(
                        patch.object(
                            module,
                            "init_browser",
                            return_value=(
                                object(),
                                object(),
                                object(),
                                page,
                            ),
                        )
                    )
                    stack.enter_context(
                        patch.object(
                            module,
                            page_class_name,
                            side_effect=(
                                lambda _page, site_config=None, name=page_name:
                                SuccessfulPageModel(page, name)
                            ),
                        )
                    )
                    stack.enter_context(patch.object(module, "close_browser"))
                    stack.enter_context(
                        patch.object(module.time, "sleep", return_value=None)
                    )
                    for name, return_value in replacements.items():
                        stack.enter_context(
                            patch.object(
                                module,
                                name,
                                return_value=return_value,
                            )
                        )
                    failures = module.run()

                self.assertEqual([], failures)
                summaries = [
                    item
                    for item in get_results()
                    if item.get("result_type") == "page_summary"
                    and item.get("page") == page_name
                ]
                self.assertEqual(1, len(summaries))

    def test_terminal_main_document_skips_long_page_checks(self):
        cases = [
            (
                home_check,
                "HomePage",
                "home",
                ("dom_check", "check_plugins", "capture_modules"),
            ),
            (
                collection_check,
                "CollectionPage",
                "collection",
                ("dom_check", "check_product_count", "capture_modules"),
            ),
            (
                product_check,
                "ProductPage",
                "product",
                ("dom_check", "check_add_to_cart", "capture_modules"),
            ),
        ]
        for module, page_class_name, page_name, check_names in cases:
            with self.subTest(page=page_name):
                clear_results()
                page = OpenFakePage()
                context = FakeContext(page_name)
                with ExitStack() as stack:
                    stack.enter_context(
                        patch.object(
                            module,
                            "PageCheckContext",
                            return_value=context,
                        )
                    )
                    stack.enter_context(patch.object(module, "create_dirs"))
                    stack.enter_context(
                        patch.object(
                            module,
                            "init_browser",
                            return_value=(
                                object(),
                                object(),
                                object(),
                                page,
                            ),
                        )
                    )
                    stack.enter_context(
                        patch.object(
                            module,
                            page_class_name,
                            side_effect=(
                                lambda _page, site_config=None, name=page_name:
                                TerminalPageModel(page, name)
                            ),
                        )
                    )
                    stack.enter_context(patch.object(module, "close_browser"))
                    long_checks = [
                        stack.enter_context(patch.object(module, name))
                        for name in check_names
                    ]
                    failures = module.run()

                self.assertTrue(failures)
                for long_check in long_checks:
                    long_check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
