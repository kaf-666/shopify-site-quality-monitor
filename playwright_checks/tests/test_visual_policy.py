import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

from playwright_checks.artifacts.screenshot_manager import (
    ScreenshotArtifactManager,
)
from playwright_checks.core.test_results import (
    add_result,
    clear_results,
    get_page_visual_status,
    get_results,
)
from playwright_checks.core.visual_policy import screenshot_case_policy
from playwright_checks.core.config_loader import get_page_config, load_site_config
from playwright_checks.utils.readonly_interactions import capture_readonly_panel
from playwright_checks.utils.visual import process_results
from playwright_checks.checks import collection_check, home_check, product_check
from playwright_checks.core import driver
from playwright_checks.utils import structure


class VisualPurposePolicyTests(unittest.TestCase):
    def setUp(self):
        clear_results()

    def _manager(self, root, page="home", viewport="desktop"):
        return ScreenshotArtifactManager(
            "fixture",
            page,
            viewport=viewport,
            run_id="run",
            site_config={"site": "fixture"},
            page_config={},
            root=root,
        )

    def _readonly_fault_result(
        self,
        page_name,
        interaction_name,
        case_name,
        open_state,
        panel_visible_after_close=False,
    ):
        interaction_config = {
            "capture_target": "panel",
            "trigger": ["css", "button"],
            "panel": ["css", "#panel"],
            "close": ["css", "#close"],
        }

        class PolicyContext:
            site = "fixture"
            suite = "visual"
            current_dir = "current"
            baseline_dir = "baseline"
            diff_dir = "diff"
            legacy_baseline_dir = None
            artifact_manager = MagicMock()
            page_config = {
                "readonly_interactions": {
                    interaction_name: interaction_config,
                }
            }

            @staticmethod
            def screenshot_policy(case):
                return screenshot_case_policy(
                    page_name,
                    case,
                    viewport="mobile",
                )

        PolicyContext.page_name = page_name
        trigger = MagicMock()
        trigger.is_visible.return_value = True
        panel = MagicMock()
        panel.is_visible.side_effect = [
            False,
            panel_visible_after_close,
        ]
        close = MagicMock()
        page = MagicMock()
        page.locator.return_value.first = panel
        scroll_states = [
            {"locked": False, "scrollY": 0},
            {"locked": True, "scrollY": 0},
            {"locked": False, "scrollY": 0},
        ]

        with patch(
            "playwright_checks.utils.readonly_interactions.locate_element",
            side_effect=[trigger, panel, close, close],
        ), patch(
            "playwright_checks.utils.readonly_interactions._safe_click",
        ), patch(
            "playwright_checks.utils.readonly_interactions.wait_for_layout_stable",
            return_value=True,
        ), patch(
            "playwright_checks.utils.readonly_interactions._panel_state",
            return_value=open_state,
        ), patch(
            "playwright_checks.utils.readonly_interactions._page_scroll_state",
            side_effect=scroll_states,
        ), patch(
            "playwright_checks.utils.readonly_interactions._probe_scroll_restored",
            return_value={
                "scrollable": True,
                "moved": True,
                "restored": True,
            },
        ), patch(
            "playwright_checks.utils.readonly_interactions.build_paths",
            return_value={"current": "current.png"},
        ):
            results, failures = capture_readonly_panel(
                PolicyContext(),
                page,
                interaction_name,
                case_name,
            )

        self.assertEqual([], failures)
        return results[case_name]

    @staticmethod
    def _different_images(temp_dir, manager, case):
        baseline = Path(temp_dir) / f"{case}-baseline.png"
        current = manager.temporary_path(case, "current")
        diff = manager.temporary_path(case, "diff")
        Image.new("RGB", (40, 40), "white").save(baseline)
        Image.new("RGB", (40, 40), "black").save(current)
        return {
            case: {
                "baseline": str(baseline),
                "target_baseline": str(baseline),
                "legacy_baseline": None,
                "current": str(current),
                "diff": str(diff),
            }
        }

    def test_full_page_failure_is_report_only_and_uses_report_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._manager(Path(temp_dir) / "artifacts")
            failures = process_results(
                self._different_images(temp_dir, manager, "global"),
                "fixture",
                "visual",
                "home",
                manager=manager,
            )

        result = get_results()[-1]
        self.assertEqual([], failures)
        self.assertEqual("home_full_page", result["case"])
        self.assertEqual("failed", result["status"])
        self.assertEqual("report_only", result["screenshot_purpose"])
        self.assertFalse(result["affects_exit_code"])

    def test_first_screen_failure_remains_a_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._manager(Path(temp_dir) / "artifacts")
            failures = process_results(
                self._different_images(temp_dir, manager, "first_screen"),
                "fixture",
                "visual",
                "home",
                manager=manager,
            )

        result = get_results()[-1]
        self.assertTrue(failures)
        self.assertEqual("home_first_screen", result["case"])
        self.assertEqual("gate", result["screenshot_purpose"])
        self.assertTrue(result["affects_exit_code"])

    def test_evidence_only_case_does_not_require_a_baseline(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = self._manager(
                Path(temp_dir) / "artifacts",
                page="product",
                viewport="mobile",
            )
            current = manager.temporary_path("sticky_add_to_cart", "current")
            Image.new("RGB", (40, 20), "white").save(current)
            failures = process_results(
                {
                    "sticky_add_to_cart": {
                        "baseline": str(Path(temp_dir) / "missing.png"),
                        "target_baseline": str(Path(temp_dir) / "missing.png"),
                        "legacy_baseline": None,
                        "current": str(current),
                        "diff": str(
                            manager.temporary_path(
                                "sticky_add_to_cart",
                                "diff",
                            )
                        ),
                    }
                },
                "fixture",
                "visual",
                "product",
                manager=manager,
            )

        result = get_results()[-1]
        self.assertEqual([], failures)
        self.assertEqual("passed", result["status"])
        self.assertEqual("evidence_only", result["screenshot_purpose"])
        self.assertTrue(result["pixel_compare_skipped"])
        self.assertFalse(result["affects_exit_code"])

    def test_unconfigured_readonly_selector_skips_without_touching_page(self):
        class Context:
            site = "fixture"
            suite = "visual"
            page_name = "home"
            page_config = {}

            @staticmethod
            def screenshot_policy(case):
                return screenshot_case_policy(
                    "home",
                    case,
                    viewport="mobile",
                )

        class UntouchedPage:
            def __getattr__(self, name):
                raise AssertionError(f"page should not be used: {name}")

        results, failures = capture_readonly_panel(
            Context(),
            UntouchedPage(),
            "mobile_menu",
            "mobile_menu_open",
        )

        self.assertEqual({}, results)
        self.assertEqual([], failures)
        result = get_results()[-1]
        self.assertEqual("skipped", result["status"])
        self.assertEqual("selector_not_configured", result["skip_reason"])
        self.assertFalse(result["affects_exit_code"])

    def test_readonly_selector_uses_configured_skip_reason(self):
        class Context:
            site = "fixture"
            suite = "visual"
            page_name = "collection"
            page_config = {
                "readonly_interaction_skip_reasons": {
                    "filter": "native_select_no_readonly_panel",
                }
            }

            @staticmethod
            def screenshot_policy(case):
                return screenshot_case_policy(
                    "collection",
                    case,
                    viewport="mobile",
                )

        class UntouchedPage:
            def __getattr__(self, name):
                raise AssertionError(f"page should not be used: {name}")

        results, failures = capture_readonly_panel(
            Context(),
            UntouchedPage(),
            "filter",
            "filter_drawer_open",
        )

        self.assertEqual({}, results)
        self.assertEqual([], failures)
        result = get_results()[-1]
        self.assertEqual("skipped", result["status"])
        self.assertEqual(
            "native_select_no_readonly_panel",
            result["skip_reason"],
        )

    def test_readonly_panel_records_close_and_scroll_restoration(self):
        class PolicyContext:
            site = "fixture"
            suite = "visual"
            page_name = "home"
            current_dir = "current"
            baseline_dir = "baseline"
            diff_dir = "diff"
            legacy_baseline_dir = None
            page_config = {
                "readonly_interactions": {
                    "mobile_menu": {
                        "capture_target": "panel",
                        "trigger": ["css", "button"],
                        "panel": ["css", "#panel"],
                        "close": ["css", "#close"],
                    }
                }
            }
            artifact_manager = MagicMock()

            @staticmethod
            def screenshot_policy(case):
                return screenshot_case_policy(
                    "home",
                    case,
                    viewport="mobile",
                )

        trigger = MagicMock()
        trigger.is_visible.return_value = True
        panel = MagicMock()
        panel.is_visible.side_effect = [False, False]
        close = MagicMock()
        page = MagicMock()
        page.locator.return_value.first = panel
        open_state = {
            "panelVisible": True,
            "withinViewport": True,
            "pageHorizontalOverflow": False,
            "panelHorizontalOverflow": False,
            "closeVisible": True,
            "bottomActionVisible": True,
        }
        scroll_states = [
            {"locked": False, "scrollY": 0},
            {"locked": True, "scrollY": 0},
            {"locked": False, "scrollY": 0},
        ]

        with patch(
            "playwright_checks.utils.readonly_interactions.locate_element",
            side_effect=[trigger, panel, close, close],
        ), patch(
            "playwright_checks.utils.readonly_interactions._safe_click",
        ), patch(
            "playwright_checks.utils.readonly_interactions.wait_for_layout_stable",
            return_value=True,
        ), patch(
            "playwright_checks.utils.readonly_interactions._panel_state",
            return_value=open_state,
        ), patch(
            "playwright_checks.utils.readonly_interactions._page_scroll_state",
            side_effect=scroll_states,
        ), patch(
            "playwright_checks.utils.readonly_interactions._probe_scroll_restored",
            return_value={
                "scrollable": True,
                "moved": True,
                "restored": True,
            },
        ), patch(
            "playwright_checks.utils.readonly_interactions.build_paths",
            return_value={"current": "current.png"},
        ):
            results, failures = capture_readonly_panel(
                PolicyContext(),
                page,
                "mobile_menu",
                "mobile_menu_open",
            )

        self.assertEqual([], failures)
        state = results["mobile_menu_open"]["interaction_state"]
        self.assertFalse(state["panel_visible_before"])
        self.assertTrue(state["panel_visible_after_open"])
        self.assertFalse(state["panel_visible_after_close"])
        self.assertTrue(state["body_scroll_locked_after_open"])
        self.assertTrue(state["body_scroll_restored_after_close"])
        self.assertEqual("panel", state["capture_target"])
        PolicyContext.artifact_manager.capture_element.assert_called_once_with(
            panel,
            "current.png",
        )
        PolicyContext.artifact_manager.capture_page.assert_not_called()

    def test_report_only_failure_does_not_poison_page_visual_status(self):
        add_result(
            {
                "result_type": "visual",
                "site": "fixture",
                "viewport": "desktop",
                "page": "home",
                "case": "home_first_screen",
                "status": "passed",
                "affects_exit_code": False,
            }
        )
        add_result(
            {
                "result_type": "visual",
                "site": "fixture",
                "viewport": "desktop",
                "page": "home",
                "case": "home_full_page",
                "status": "failed",
                "affects_exit_code": False,
            }
        )

        self.assertEqual(
            "passed",
            get_page_visual_status("fixture", "desktop", "home"),
        )

    def test_add_to_cart_state_check_never_clicks_button(self):
        class Button:
            @staticmethod
            def click(*args, **kwargs):
                raise AssertionError("Add To Cart must never be clicked")

        ready_state = {
            "ready": True,
            "text": "Add to cart",
            "visible": True,
            "enabled": True,
            "loading": False,
            "busy": False,
            "width": 200,
            "height": 48,
            "left": 10,
            "top": 10,
            "right": 210,
            "bottom": 58,
            "viewportWidth": 390,
            "viewportHeight": 844,
        }
        with patch.object(
            product_check,
            "locate_ready_add_to_cart",
            return_value=Button(),
        ), patch.object(
            product_check,
            "add_to_cart_button_state",
            return_value=ready_state,
        ):
            self.assertEqual([], product_check.check_add_to_cart(object()))

    def test_configured_variant_uses_exact_option(self):
        variants = MagicMock()
        variants.count.return_value = 3
        candidates = [MagicMock(), MagicMock(), MagicMock()]
        variants.nth.side_effect = candidates
        for candidate, value in zip(
            candidates,
            ("Pink", "Sky Blue", "Ivory"),
        ):
            candidate.evaluate.return_value = True
            candidate.get_attribute.side_effect = lambda name, value=value: (
                "Color" if name == "name" else value
            )
            candidate.is_enabled.return_value = True

        page_model = MagicMock()
        page_model.config = {
            "variant_check": {
                "enabled": True,
                "option_name": "Color",
                "option_value": "Sky Blue",
            }
        }
        page_model.variant_inputs.return_value = variants
        click_target = MagicMock()
        before_gallery = {
            "currentSources": ["pink.jpg"],
            "activeState": "pink",
        }
        after_gallery = {
            "currentSources": ["pink.jpg"],
            "activeState": "sky-blue",
            "imageCount": 1,
            "readyImageCount": 1,
            "width": 500,
            "height": 700,
            "loading": False,
            "pageHorizontalOverflow": False,
        }

        with patch.object(
            product_check,
            "variant_selected_state",
            side_effect=[{"checked": False}, {"checked": True}],
        ), patch.object(
            product_check,
            "variant_click_target",
            return_value=click_target,
        ) as selected_target, patch.object(
            product_check,
            "gallery_runtime_state",
            side_effect=[before_gallery, after_gallery],
        ), patch.object(
            product_check,
            "capture_product_main",
            return_value={
                "variant_changed_state": {"current": "current.png"}
            },
        ):
            results, failures = product_check.test_variants(
                MagicMock(),
                page_model,
            )

        self.assertEqual([], failures)
        selected_target.assert_called_once_with(page_model, candidates[1])
        assertions = results["variant_changed_state"][
            "variant_assertions"
        ]
        self.assertTrue(assertions["deterministic"])
        self.assertEqual("Color", assertions["option_name"])
        self.assertEqual("Sky Blue", assertions["option_value"])

    def test_variant_click_target_prefers_visible_label(self):
        variant = MagicMock()
        variant.get_attribute.return_value = "color-sky-blue"
        variant.is_visible.return_value = True
        label = MagicMock()
        label.is_visible.return_value = True
        page_model = MagicMock()
        page_model.page.locator.return_value.first = label

        target = product_check.variant_click_target(page_model, variant)

        self.assertIs(label, target)
        page_model.page.locator.assert_called_once_with(
            "label[for='color-sky-blue']"
        )

    def test_collection_scroll_recovers_from_late_navigation(self):
        page_model = MagicMock()
        cards = MagicMock()
        cards.count.return_value = 40
        page_model.product_cards.return_value = cards
        page_model.page.evaluate.side_effect = [
            collection_check.PlaywrightError(
                "Execution context was destroyed, most likely because of a navigation"
            ),
            True,
            None,
            True,
            None,
            True,
        ]

        with patch.object(collection_check.time, "sleep"):
            count = collection_check.scroll_to_load_all(
                page_model,
                timeout=10,
                max_scrolls=10,
            )

        self.assertEqual(40, count)
        page_model.page.wait_for_load_state.assert_called_once_with(
            "domcontentloaded",
            timeout=45000,
        )
        page_model.wait_until_ready.assert_called_once_with()

    def test_structure_check_scrolls_lazy_region_before_audit(self):
        class Context:
            site = "fixture"
            suite = "visual"
            page_name = "home"
            site_config = {"site": "fixture"}
            page_config = {
                "dynamic_regions": [
                    {
                        "name": "banner",
                        "module": "banner",
                        "strategy": "layout_only",
                        "region_type": "content",
                    }
                ]
            }
            modules = {"banner": ("css", "#banner")}

        element = MagicMock()
        with patch.object(
            structure,
            "locate_element",
            return_value=element,
        ), patch.object(
            structure,
            "wait_for_visible_images",
            return_value=True,
        ), patch.object(
            structure,
            "wait_for_layout_stable",
            return_value=True,
        ), patch.object(
            structure,
            "audit_dynamic_region",
            return_value={
                "structural_status": "passed",
                "structural_issues": [],
            },
        ) as audit:
            failures, results = structure.run_structure_checks(
                Context(),
                MagicMock(),
            )

        self.assertEqual([], failures)
        self.assertEqual(1, len(results))
        element.scroll_into_view_if_needed.assert_called_once_with(
            timeout=10000
        )
        audit.assert_called_once()

    def test_banner_stabilization_does_not_force_flickity_x(self):
        page = MagicMock()
        with patch.object(
            home_check,
            "stabilize_configured_display",
        ), patch.object(home_check.time, "sleep"):
            home_check.stabilize_banner(
                page,
                {"stable_banner_index": 2},
            )

        script, stable_index = page.evaluate.call_args.args
        self.assertEqual(2, stable_index)
        self.assertIn("flkty.select", script)
        self.assertNotIn("flkty.x = 0", script)

    def test_mondressy_banner_is_a_carousel_structure_region(self):
        site_config = load_site_config("mondressy_US")
        home_config = get_page_config(
            "home",
            site_config=site_config,
            viewport="mobile",
        )
        banner = next(
            region
            for region in home_config["dynamic_regions"]
            if region["name"] == "banner"
        )

        self.assertEqual("layout_only", banner["strategy"])
        self.assertEqual("carousel", banner["region_type"])

    def test_explicit_chromium_channel_uses_full_browser_channel(self):
        playwright = MagicMock()
        browser = MagicMock()
        context = MagicMock()
        page = MagicMock()
        playwright.chromium.launch.return_value = browser
        browser.new_context.return_value = context
        context.new_page.return_value = page
        starter = MagicMock()
        starter.start.return_value = playwright

        with patch.dict(
            os.environ,
            {
                "PLAYWRIGHT_BROWSER_CHANNEL": "chromium",
                "PLAYWRIGHT_HEADED": "0",
            },
        ), patch.object(
            driver,
            "sync_playwright",
            return_value=starter,
        ), patch.object(
            driver,
            "load_settings",
            return_value={"browser": {}},
        ), patch.object(
            driver,
            "load_site_config",
            return_value={"site": "fixture", "browser": {}},
        ), patch.object(
            driver,
            "load_signed_request_headers",
        ), patch.object(
            driver,
            "install_signed_request_routing",
        ), patch.object(
            driver,
            "get_viewport_config",
            return_value={"width": 390, "height": 844},
        ):
            driver.init_browser()

        self.assertEqual(
            "chromium",
            playwright.chromium.launch.call_args.kwargs["channel"],
        )

    def test_product_main_allows_small_mobile_panel_seam(self):
        state = {
            "root": {
                "left": 0,
                "top": 0,
                "right": 390,
                "bottom": 2100,
                "width": 390,
                "height": 2100,
            },
            "gallery": {
                "left": 17,
                "top": 0,
                "right": 373,
                "bottom": 550,
                "width": 356,
                "height": 550,
            },
            "info": {
                "left": 17,
                "top": 536,
                "right": 373,
                "bottom": 2070,
                "width": 356,
                "height": 1534,
            },
            "addToCart": {
                "left": 17,
                "top": 1100,
                "right": 373,
                "bottom": 1150,
                "width": 356,
                "height": 50,
            },
            "galleryVisible": True,
            "infoVisible": True,
            "addToCartVisible": True,
            "readyImageCount": 1,
            "pageHorizontalOverflow": False,
            "viewportHeight": 844,
        }

        self.assertNotIn(
            "gallery_and_info_overlap",
            product_check.product_main_issues(state),
        )

        state["info"]["top"] = 500
        self.assertIn(
            "gallery_and_info_overlap",
            product_check.product_main_issues(state),
        )

    def test_product_main_masks_are_clipped_to_their_regions(self):
        page_model = MagicMock()
        target = MagicMock()
        target.evaluate.return_value = {"maskBoxes": []}
        page_model.module.return_value.element_handle.return_value = MagicMock()

        with patch.object(
            product_check,
            "locate_ready_add_to_cart",
        ) as add_to_cart:
            add_to_cart.return_value.element_handle.return_value = MagicMock()
            product_check.product_main_snapshot(page_model, target)

        script = target.evaluate.call_args.args[0]
        self.assertIn("relativeBox(node, nodes.gallery)", script)
        self.assertIn("relativeBox(node, nodes.info)", script)

    def test_fault_injection_dynamic_product_content_does_not_fail_gate(self):
        scenarios = (
            "product_title_text",
            "product_price_text",
            "product_image_content",
            "recommended_product_order",
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temp_dir:
                clear_results()
                manager = self._manager(
                    Path(temp_dir) / "artifacts",
                    page="product",
                    viewport="desktop",
                )
                baseline = Path(temp_dir) / f"{scenario}-baseline.png"
                current = manager.temporary_path("product_main", "current")
                diff = manager.temporary_path("product_main", "diff")
                Image.new("RGB", (80, 80), "white").save(baseline)
                changed = Image.new("RGB", (80, 80), "white")
                for x in range(20, 61):
                    for y in range(20, 61):
                        changed.putpixel((x, y), (0, 0, 0))
                changed.save(current)

                failures = process_results(
                    {
                        "product_main": {
                            "baseline": str(baseline),
                            "target_baseline": str(baseline),
                            "legacy_baseline": None,
                            "current": str(current),
                            "diff": str(diff),
                            "dynamic_strategy": "mask_content",
                            "structural_status": "passed",
                            "structural_issues": [],
                            "content_mask_boxes": [
                                {
                                    "left": 18,
                                    "top": 18,
                                    "right": 63,
                                    "bottom": 63,
                                }
                            ],
                        }
                    },
                    "fixture",
                    "visual",
                    "product",
                    manager=manager,
                )
                result = get_results()[-1]

                self.assertEqual([], failures)
                self.assertEqual("content_changed", result["status"])
                self.assertFalse(result["affects_exit_code"])

    def test_fault_injection_product_structure_failures_remain_gates(self):
        valid = {
            "root": {
                "left": 0,
                "top": 0,
                "right": 1000,
                "bottom": 1200,
                "width": 1000,
                "height": 1200,
            },
            "gallery": {
                "left": 0,
                "top": 0,
                "right": 500,
                "bottom": 700,
                "width": 500,
                "height": 700,
            },
            "info": {
                "left": 520,
                "top": 0,
                "right": 1000,
                "bottom": 1100,
                "width": 480,
                "height": 1100,
            },
            "addToCart": {
                "left": 520,
                "top": 500,
                "right": 1000,
                "bottom": 550,
                "width": 480,
                "height": 50,
            },
            "galleryVisible": True,
            "infoVisible": True,
            "addToCartVisible": True,
            "readyImageCount": 1,
            "pageHorizontalOverflow": False,
            "viewportHeight": 900,
        }
        scenarios = {}
        hidden = {**valid, "root": {**valid["root"], "width": 0, "height": 0}}
        hidden.update({"galleryVisible": False, "infoVisible": False})
        scenarios["product_main_hidden"] = (
            hidden,
            "product_main_has_no_size",
        )
        gallery_zero = {
            **valid,
            "gallery": {**valid["gallery"], "height": 0, "bottom": 0},
            "galleryVisible": False,
            "readyImageCount": 0,
        }
        scenarios["gallery_height_zero"] = (
            gallery_zero,
            "product_main_section_missing",
        )
        overflow = {**valid, "pageHorizontalOverflow": True}
        scenarios["horizontal_overflow"] = (
            overflow,
            "page_horizontal_overflow",
        )

        for scenario, (state, expected) in scenarios.items():
            with self.subTest(scenario=scenario):
                self.assertIn(
                    expected,
                    product_check.product_main_issues(state),
                )

    def test_fault_injection_menu_close_failure_is_detected(self):
        result = self._readonly_fault_result(
            "home",
            "mobile_menu",
            "mobile_menu_open",
            {
                "panelVisible": True,
                "withinViewport": True,
                "pageHorizontalOverflow": False,
                "panelHorizontalOverflow": False,
                "closeVisible": True,
                "bottomActionVisible": True,
            },
            panel_visible_after_close=True,
        )

        self.assertIn("panel close failed", result["error"])

    def test_fault_injection_filter_outside_viewport_is_detected(self):
        result = self._readonly_fault_result(
            "collection",
            "filter",
            "filter_drawer_open",
            {
                "panelVisible": True,
                "withinViewport": False,
                "pageHorizontalOverflow": False,
                "panelHorizontalOverflow": False,
                "closeVisible": True,
                "bottomActionVisible": True,
            },
        )

        self.assertIn("panel_outside_viewport", result["error"])

    def test_fault_injection_add_to_cart_empty_and_loading_fail_state_check(self):
        for scenario in ("empty_text", "persistent_loading"):
            with self.subTest(scenario=scenario), patch.object(
                product_check,
                "locate_ready_add_to_cart",
                side_effect=Exception(f"Add To Cart {scenario}"),
            ):
                failures = product_check.check_add_to_cart(MagicMock())

                self.assertEqual(1, len(failures))
                self.assertIn(scenario, failures[0])


if __name__ == "__main__":
    unittest.main()
