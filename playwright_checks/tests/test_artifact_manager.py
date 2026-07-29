import errno
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from playwright_checks.artifacts.cleanup import (
    cleanup_current_run_temp,
    cleanup_old_runs,
    resolved_within,
)
from playwright_checks.artifacts.dynamic import (
    audit_dynamic_region,
    classify_content_changes,
    content_snapshot,
    evaluate_structural_snapshot,
)
from playwright_checks.artifacts.manifest import build_artifact_summary
from playwright_checks.artifacts.screenshot_manager import (
    ScreenshotArtifactManager,
    finalize_artifact_run,
    safe_move,
)
from playwright_checks.artifacts.simulation import run_simulation
from playwright_checks.checks.product_check import (
    monitoring_product_is_unavailable,
)
from playwright_checks.core.test_results import clear_results, get_results
from playwright_checks.utils.dom import dom_check
from playwright_checks.utils.visual import process_results


class ScreenshotArtifactManagerTests(unittest.TestCase):
    def setUp(self):
        clear_results()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "artifacts"
        self.run_id = "fixture-run"

    def tearDown(self):
        clear_results()
        self.temp.cleanup()

    def manager(
        self,
        mode="evidence_only",
        site="fixture_site",
        page="home",
        limits=None,
        page_config=None,
    ):
        retention = {"mode": mode}
        if limits:
            retention["limits"] = limits
        site_config = {
            "site": site,
            "artifacts": {
                "screenshot_retention": retention,
            },
        }
        return ScreenshotArtifactManager(
            site,
            page,
            viewport="desktop",
            run_id=self.run_id,
            site_config=site_config,
            page_config=page_config or {},
            root=self.root,
        )

    @staticmethod
    def write(path, size=128):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * size)
        return target

    def paths(self, manager, case, size=128):
        current = self.write(
            manager.temporary_path(case, "current"),
            size,
        )
        diff = self.write(
            manager.temporary_path(case, "diff"),
            size,
        )
        return {"current": str(current), "diff": str(diff)}

    def test_same_filesystem_move_creates_parent_and_removes_source(self):
        source = self.write(self.root / "source.png")
        target = self.root / "nested" / "target.png"

        safe_move(source, target)

        self.assertFalse(source.exists())
        self.assertEqual(b"x" * 128, target.read_bytes())

    def test_exdev_falls_back_to_copy_and_removes_source(self):
        source = self.write(self.root / "source.png")
        target = self.root / "nested" / "target.png"
        original_replace = os.replace

        with patch(
            "playwright_checks.artifacts.screenshot_manager.os.replace",
            side_effect=OSError(errno.EXDEV, "cross device"),
        ):
            safe_move(source, target)

        self.assertFalse(source.exists())
        self.assertTrue(target.exists())
        original_replace(target, self.root / "moved.png")

    def test_exdev_copy_failure_preserves_source_and_original_error(self):
        source = self.write(self.root / "source.png")
        target = self.root / "target.png"
        with (
            patch(
                "playwright_checks.artifacts.screenshot_manager.os.replace",
                side_effect=OSError(errno.EXDEV, "cross device"),
            ),
            patch(
                "playwright_checks.artifacts.screenshot_manager.shutil.copy2",
                side_effect=OSError("copy failed"),
            ),
        ):
            with self.assertRaises(OSError) as captured:
                safe_move(source, target)

        self.assertEqual(errno.EXDEV, captured.exception.errno)
        self.assertTrue(source.exists())
        self.assertFalse(target.exists())

    def test_non_exdev_move_error_is_not_swallowed(self):
        source = self.write(self.root / "source.png")
        with patch(
            "playwright_checks.artifacts.screenshot_manager.os.replace",
            side_effect=OSError(errno.EACCES, "denied"),
        ):
            with self.assertRaises(OSError) as captured:
                safe_move(source, self.root / "target.png")
        self.assertEqual(errno.EACCES, captured.exception.errno)
        self.assertTrue(source.exists())

    def test_evidence_only_retention_matrix(self):
        manager = self.manager()
        passed, passed_meta = manager.finalize_result(
            "passed",
            "passed",
            self.paths(manager, "passed"),
        )
        changed, changed_meta = manager.finalize_result(
            "changed",
            "content_changed",
            self.paths(manager, "changed"),
            content_changes=["product_price_changed"],
        )
        warning, warning_meta = manager.finalize_result(
            "warning",
            "warning",
            self.paths(manager, "warning"),
        )
        failed, failed_meta = manager.finalize_result(
            "failed",
            "failed",
            self.paths(manager, "failed"),
        )

        self.assertIsNone(passed["current"])
        self.assertIsNone(passed["diff"])
        self.assertFalse(passed_meta["retained"])
        self.assertEqual("passed_cleanup", passed_meta["retention_reason"])
        self.assertIsNone(changed["current"])
        self.assertFalse(changed_meta["affects_exit_code"])
        self.assertTrue(Path(warning["current"]).exists())
        self.assertTrue(Path(warning["diff"]).exists())
        self.assertTrue(Path(failed["current"]).exists())
        self.assertTrue(Path(failed["diff"]).exists())
        self.assertTrue(warning_meta["retained"])
        self.assertTrue(failed_meta["retained"])

        manifest = manager.manifest.read()
        self.assertTrue(
            all(
                (self.root / item["relative_path"]).is_file()
                for item in manifest["retained_images"]
            )
        )
        self.assertEqual(
            2,
            len(manifest["deleted_passed_images"]),
        )
        self.assertIn(
            "product_price_changed",
            manifest["content_changes"],
        )

    def test_baseline_missing_keeps_only_one_representative(self):
        manager = self.manager()
        first, _ = manager.finalize_result(
            "first",
            "baseline_missing",
            self.paths(manager, "first"),
        )
        second, _ = manager.finalize_result(
            "second",
            "baseline_missing",
            self.paths(manager, "second"),
        )

        self.assertTrue(Path(first["current"]).exists())
        self.assertIsNone(first["diff"])
        self.assertIsNone(second["current"])
        self.assertEqual(1, manager.manifest.read()["total_files"])

    def test_terminal_page_is_single_current_without_diff(self):
        manager = self.manager()
        first, _ = manager.finalize_result(
            "terminal_page",
            "terminal_page",
            self.paths(manager, "terminal"),
            artifact_type="terminal_page",
        )
        second, _ = manager.finalize_result(
            "terminal_page",
            "terminal_page",
            self.paths(manager, "terminal-retry"),
            artifact_type="terminal_page",
        )

        self.assertTrue(Path(first["current"]).name == "terminal_page.png")
        self.assertTrue(Path(second["current"]).is_file())
        self.assertIsNone(second["diff"])
        manifest = manager.manifest.read()
        self.assertEqual(1, manifest["total_files"])

    def test_failed_page_promotes_only_one_deferred_context_image(self):
        manager = self.manager()
        global_paths = self.paths(manager, "global")
        first_paths = self.paths(manager, "first_screen")

        global_result, _ = manager.finalize_result(
            "global",
            "passed",
            global_paths,
            artifact_type="global",
        )
        manager.finalize_result(
            "first_screen",
            "passed",
            first_paths,
            artifact_type="first_screen",
        )
        self.assertIsNone(global_result["current"])
        self.assertTrue(Path(global_paths["current"]).is_file())

        manager.finalize_page(has_failure=True)

        manifest = manager.manifest.read()
        contexts = [
            item
            for item in manifest["retained_images"]
            if item["retention_reason"] == "failure_context"
        ]
        self.assertEqual(1, len(contexts))
        self.assertEqual("global_failure_context", contexts[0]["case"])
        self.assertTrue(
            (self.root / contexts[0]["relative_path"]).is_file()
        )
        self.assertFalse(Path(first_paths["current"]).exists())

    def test_successful_page_deletes_deferred_context_images(self):
        manager = self.manager()
        paths = self.paths(manager, "global")
        manager.finalize_result(
            "global",
            "passed",
            paths,
            artifact_type="global",
        )

        manager.finalize_page(has_failure=False)

        self.assertFalse(Path(paths["current"]).exists())
        manifest = manager.manifest.read()
        self.assertEqual(0, manifest["total_files"])
        self.assertEqual(2, len(manifest["deleted_passed_images"]))

    def test_content_changed_context_is_only_kept_if_page_fails(self):
        manager = self.manager()
        paths = self.paths(manager, "global")
        result, metadata = manager.finalize_result(
            "global",
            "content_changed",
            paths,
            artifact_type="global",
            content_changes=["dynamic_regions_content_changed"],
        )

        self.assertIsNone(result["current"])
        self.assertEqual(
            "content_change_recorded",
            metadata["retention_reason"],
        )
        self.assertTrue(Path(paths["current"]).is_file())

        manager.finalize_page(has_failure=False)

        manifest = manager.manifest.read()
        self.assertEqual(0, manifest["total_files"])
        self.assertEqual([], manifest["deleted_passed_images"])
        self.assertIn(
            "dynamic_regions_content_changed",
            manifest["content_changes"],
        )

    def test_page_quota_keeps_failed_before_warning_and_content(self):
        manager = self.manager(
            mode="debug",
            limits={
                "max_images_per_page": 2,
                "max_mb_per_page": 10,
                "max_mb_per_site": 10,
                "max_mb_per_run": 10,
            },
        )
        manager.finalize_result(
            "warning",
            "warning",
            self.paths(manager, "warning"),
        )
        manager.finalize_result(
            "changed",
            "content_changed",
            self.paths(manager, "changed"),
        )
        manager.finalize_result(
            "failed",
            "failed",
            self.paths(manager, "failed"),
        )

        manifest = manager.manifest.read()
        self.assertEqual(2, manifest["total_files"])
        self.assertEqual(
            {"failed"},
            {
                item["visual_status"]
                for item in manifest["retained_images"]
            },
        )
        self.assertGreaterEqual(len(manifest["dropped_by_quota"]), 4)

    def test_page_site_and_run_byte_quota_do_not_crash(self):
        limits = {
            "max_images_per_page": 20,
            "max_mb_per_page": 0.001,
            "max_mb_per_site": 0.001,
            "max_mb_per_run": 0.001,
        }
        first = self.manager(
            mode="debug",
            site="site_a",
            limits=limits,
        )
        second = self.manager(
            mode="debug",
            site="site_b",
            limits=limits,
        )
        first.finalize_result(
            "failed-a",
            "failed",
            self.paths(first, "failed-a", size=800),
        )
        second.finalize_result(
            "warning-b",
            "warning",
            self.paths(second, "warning-b", size=800),
        )

        summary = build_artifact_summary(self.root, self.run_id)
        self.assertLessEqual(summary["total_bytes"], 1048)
        self.assertGreater(summary["dropped_by_quota"], 0)

    def test_parallel_sites_have_unique_paths_and_scoped_cleanup(self):
        first = self.manager(site="site_a")
        second = self.manager(site="site_b")

        with ThreadPoolExecutor(max_workers=2) as executor:
            paths = list(
                executor.map(
                    lambda manager: manager.temporary_path(
                        "currency",
                        "plugin-probe",
                        attempt=2,
                    ),
                    (first, second),
                )
            )
        for path in paths:
            self.write(path)

        self.assertNotEqual(paths[0], paths[1])
        self.assertIn("site_a", paths[0].as_posix())
        self.assertIn("site_b", paths[1].as_posix())
        self.assertIn("attempt-2", paths[0].name)
        first.cleanup_temporary()
        self.assertFalse(first.temp_root.exists())
        self.assertTrue(second.temp_root.exists())

    def test_cleanup_refuses_artifact_root_escape(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        with self.assertRaises(ValueError):
            resolved_within(outside, self.root)

    def test_old_run_cleanup_is_pattern_scoped(self):
        keep = self.root / "jenkins-2-job"
        old = self.root / "jenkins-1-job"
        unrelated = self.root / "local-diagnostic"
        for path in (keep, old, unrelated):
            path.mkdir(parents=True)

        removed = cleanup_old_runs(
            self.root,
            keep_run_id=keep.name,
            run_pattern="jenkins-*-job",
        )

        self.assertEqual(["jenkins-1-job"], removed)
        self.assertTrue(keep.exists())
        self.assertTrue(unrelated.exists())

    def test_cleanup_module_executes_without_runpy_runtime_warning(self):
        project_root = Path(__file__).resolve().parents[2]
        completed = subprocess.run(
            [
                sys.executable,
                "-W",
                "error::RuntimeWarning",
                "-m",
                "playwright_checks.artifacts.cleanup",
                "--help",
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertNotIn("RuntimeWarning", completed.stderr)

    def test_finalize_run_cleans_temp_and_writes_summary(self):
        manager = self.manager()
        manager.finalize_result(
            "failed",
            "failed",
            self.paths(manager, "failed"),
        )
        leftover = manager.temporary_path("leftover", "probe")
        self.write(leftover)

        summary_path, summary = finalize_artifact_run(
            self.root,
            self.run_id,
        )

        self.assertTrue(summary_path.is_file())
        self.assertFalse(manager.temp_root.exists())
        self.assertEqual(2, summary["total_images"])
        self.assertGreater(summary["total_bytes"], 0)
        persisted = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["total_images"], persisted["total_images"])

    def test_disk_simulation_reports_peak_retention_and_cleanup(self):
        result = run_simulation()

        self.assertEqual(0, result["before_bytes"])
        self.assertGreater(result["peak_bytes"], result["retention_bytes"])
        self.assertEqual(12, result["deleted_pass_images"])
        self.assertEqual(6, result["retained_images"])
        self.assertEqual(2, result["dropped_by_quota"])


class DynamicContentPolicyTests(unittest.TestCase):
    @staticmethod
    def item(index, **overrides):
        value = {
            "href": f"/products/{index}",
            "image": f"/images/{index}.jpg",
            "title": f"Product {index}",
            "price": f"${index}.00",
            "availability": "true",
            "imageReady": True,
            "rect": {
                "left": index * 110,
                "right": index * 110 + 100,
                "top": 0,
                "bottom": 180,
                "width": 100,
                "height": 180,
            },
        }
        value.update(overrides)
        return value

    def test_content_change_categories(self):
        before = content_snapshot([self.item(1), self.item(2)])
        after_count = content_snapshot(
            [self.item(1), self.item(2), self.item(3)]
        )
        after_order = content_snapshot([self.item(2), self.item(1)])
        after_image = content_snapshot(
            [self.item(1, image="/new.jpg"), self.item(2)]
        )
        after_price = content_snapshot(
            [self.item(1, price="$99"), self.item(2)]
        )

        self.assertIn(
            "product_count_changed",
            classify_content_changes(before, after_count),
        )
        self.assertIn(
            "product_order_changed",
            classify_content_changes(before, after_order),
        )
        self.assertIn(
            "product_image_changed",
            classify_content_changes(before, after_image),
        )
        self.assertIn(
            "product_price_changed",
            classify_content_changes(before, after_price),
        )

    def test_layout_only_structural_failures_remain_failed(self):
        base = {
            "visible": True,
            "rootRect": {"width": 320, "height": 200},
            "horizontalOverflow": False,
            "itemCount": 2,
            "visibleItemCount": 2,
            "items": [self.item(0), self.item(1)],
        }
        healthy = evaluate_structural_snapshot(
            base,
            "layout_only",
            {"minimum_count": 1},
        )
        self.assertEqual("passed", healthy["structural_status"])

        cases = {
            "empty": {
                **base,
                "itemCount": 0,
                "visibleItemCount": 0,
                "items": [],
            },
            "overlap": {
                **base,
                "items": [
                    self.item(0),
                    self.item(
                        1,
                        rect=self.item(0)["rect"],
                    ),
                ],
            },
            "images": {
                **base,
                "items": [
                    self.item(0, imageReady=False),
                    self.item(1, imageReady=False),
                ],
            },
            "overflow": {**base, "horizontalOverflow": True},
            "title": {
                **base,
                "items": [self.item(0, title=""), self.item(1)],
            },
            "price": {
                **base,
                "items": [self.item(0, price=""), self.item(1)],
            },
        }
        expected = {
            "empty": "product_grid_below_minimum_count",
            "overlap": "product_cards_overlap",
            "images": "product_image_success_rate_low",
            "overflow": "horizontal_overflow",
            "title": "product_title_missing",
            "price": "product_price_missing",
        }
        for name, snapshot in cases.items():
            with self.subTest(name=name):
                result = evaluate_structural_snapshot(
                    snapshot,
                    "layout_only",
                    {"minimum_count": 1},
                )
                self.assertEqual("failed", result["structural_status"])
                self.assertIn(expected[name], result["structural_issues"])

    def test_home_carousel_passes_with_only_some_items_visible(self):
        visible_items = [self.item(0), self.item(1)]
        result = evaluate_structural_snapshot(
            {
                "visible": True,
                "rootRect": {"width": 600, "height": 240},
                "containerHorizontalOverflow": True,
                "pageHorizontalOverflow": False,
                "itemCount": 8,
                "visibleItemCount": 2,
                "isCarousel": True,
                "items": visible_items,
            },
            "mask_content",
            {
                "minimum_count": 1,
                "check_image_visible": True,
                "check_title_present": True,
                "check_price_present": True,
            },
            region_type="product_carousel",
        )

        self.assertEqual("passed", result["structural_status"])
        diagnostics = result["structural_diagnostics"]
        self.assertEqual(8, diagnostics["matched_count"])
        self.assertEqual(2, diagnostics["visible_count"])
        self.assertEqual(6, diagnostics["hidden_count"])
        self.assertTrue(diagnostics["is_carousel"])
        self.assertEqual(2, diagnostics["valid_card_count"])

    def test_wrong_home_item_selector_returns_full_diagnostics(self):
        class FakeElement:
            @staticmethod
            def evaluate(_script, _options):
                return {
                    "visible": True,
                    "rootRect": {
                        "width": 800,
                        "height": 220,
                    },
                    "containerHorizontalOverflow": True,
                    "pageHorizontalOverflow": False,
                    "itemCount": 0,
                    "visibleItemCount": 0,
                    "isCarousel": True,
                    "items": [],
                    "maskBoxes": [],
                }

        result = audit_dynamic_region(
            FakeElement(),
            {
                "name": "collections",
                "module": "collections",
                "strategy": "mask_content",
                "region_type": "category_carousel",
                "item_selector": ".wrong-product-selector",
            },
            page_config={
                "modules": {
                    "collections": [
                        "xpath",
                        "(//*[contains(@class,'index-section')])[2]",
                    ]
                },
                "layout_checks": {
                    "collections": {
                        "minimum_count": 1,
                        "check_price_present": False,
                    }
                },
            },
        )

        self.assertEqual("failed", result["structural_status"])
        self.assertIn(
            "dynamic_region_item_selector_no_match",
            result["structural_issues"],
        )
        diagnostics = result["structural_diagnostics"]
        required = {
            "region",
            "region_selector",
            "item_selector",
            "minimum_count",
            "matched_count",
            "visible_count",
            "hidden_count",
            "image_success_count",
            "image_total_count",
            "is_carousel",
            "audit_duration_ms",
        }
        self.assertTrue(required.issubset(diagnostics))
        self.assertEqual("collections", diagnostics["region"])
        self.assertEqual(
            ".wrong-product-selector",
            diagnostics["item_selector"],
        )
        self.assertTrue(
            diagnostics["region_selector"].startswith("xpath=")
        )
        self.assertEqual(0, diagnostics["matched_count"])

    def test_carousel_matched_zero_and_visible_zero_fail(self):
        result = evaluate_structural_snapshot(
            {
                "visible": True,
                "rootRect": {"width": 600, "height": 200},
                "itemCount": 0,
                "visibleItemCount": 0,
                "isCarousel": True,
                "items": [],
            },
            "mask_content",
            {"minimum_count": 1},
            region_type="category_carousel",
        )

        self.assertEqual("failed", result["structural_status"])
        self.assertIn(
            "dynamic_region_item_selector_no_match",
            result["structural_issues"],
        )
        self.assertIn(
            "dynamic_region_no_visible_items",
            result["structural_issues"],
        )

    def test_carousel_with_only_hidden_matches_fails_visibility(self):
        result = evaluate_structural_snapshot(
            {
                "visible": True,
                "rootRect": {"width": 600, "height": 200},
                "itemCount": 5,
                "visibleItemCount": 0,
                "isCarousel": True,
                "items": [],
            },
            "mask_content",
            {"minimum_count": 1},
            region_type="category_carousel",
        )

        self.assertEqual("failed", result["structural_status"])
        self.assertNotIn(
            "dynamic_region_item_selector_no_match",
            result["structural_issues"],
        )
        self.assertIn(
            "dynamic_region_no_visible_items",
            result["structural_issues"],
        )

    def test_carousel_true_minimum_shortage_fails(self):
        result = evaluate_structural_snapshot(
            {
                "visible": True,
                "rootRect": {"width": 600, "height": 200},
                "itemCount": 2,
                "visibleItemCount": 1,
                "isCarousel": True,
                "items": [self.item(0)],
            },
            "mask_content",
            {"minimum_count": 3},
            region_type="product_carousel",
        )

        self.assertEqual("failed", result["structural_status"])
        self.assertIn(
            "carousel_below_minimum_count",
            result["structural_issues"],
        )

    def test_collection_grid_keeps_visible_count_rule(self):
        result = evaluate_structural_snapshot(
            {
                "visible": True,
                "rootRect": {"width": 600, "height": 200},
                "itemCount": 8,
                "visibleItemCount": 0,
                "isCarousel": False,
                "items": [],
            },
            "layout_only",
            {"minimum_count": 1},
            region_type="product_grid",
        )

        self.assertEqual("failed", result["structural_status"])
        self.assertIn(
            "product_grid_below_minimum_count",
            result["structural_issues"],
        )

    def test_dynamic_pixel_change_becomes_content_changed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifacts"
            site_config = {
                "site": "fixture",
                "artifacts": {
                    "screenshot_retention": {
                        "mode": "evidence_only",
                    }
                },
            }
            page_config = {
                "dynamic_regions": [
                    {
                        "name": "product_grid",
                        "module": "product_grid",
                        "strategy": "layout_only",
                    }
                ]
            }
            manager = ScreenshotArtifactManager(
                "fixture",
                "collection",
                viewport="desktop",
                run_id="run",
                site_config=site_config,
                page_config=page_config,
                root=root,
            )
            baseline = Path(temp_dir) / "baseline.png"
            current = manager.temporary_path("product_grid", "current")
            diff = manager.temporary_path("product_grid", "diff")
            Image.new("RGB", (30, 30), "white").save(baseline)
            Image.new("RGB", (30, 45), "black").save(current)
            results = {
                "product_grid": {
                    "baseline": str(baseline),
                    "target_baseline": str(baseline),
                    "legacy_baseline": None,
                    "current": str(current),
                    "diff": str(diff),
                    "dynamic_strategy": "layout_only",
                    "structural_status": "passed",
                    "structural_issues": [],
                    "layout_snapshot": {"item_count": 3},
                }
            }

            failures = process_results(
                results,
                "fixture",
                "visual",
                "collection",
                manager=manager,
            )
            result = get_results()[-1]

            self.assertEqual([], failures)
            self.assertEqual("content_changed", result["status"])
            self.assertFalse(result["affects_exit_code"])
            self.assertTrue(result["pixel_compare_skipped"])
            self.assertIsNone(result["ratio"])
            self.assertFalse(result["retained"])
            self.assertIsNone(result["current"])
            self.assertIsNone(result["diff"])

    def test_mask_content_compares_masked_copies_and_records_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifacts"
            page_config = {
                "dynamic_regions": [
                    {
                        "name": "featured",
                        "module": "featured",
                        "strategy": "mask_content",
                    }
                ]
            }
            manager = ScreenshotArtifactManager(
                "fixture",
                "home",
                viewport="desktop",
                run_id="run",
                site_config={
                    "site": "fixture",
                    "artifacts": {
                        "screenshot_retention": {
                            "mode": "evidence_only",
                        }
                    },
                },
                page_config=page_config,
                root=root,
            )
            baseline = Path(temp_dir) / "baseline.png"
            current = manager.temporary_path("featured", "current")
            diff = manager.temporary_path("featured", "diff")
            Image.new("RGB", (40, 40), "white").save(baseline)
            changed = Image.new("RGB", (40, 40), "white")
            for x in range(10, 31):
                for y in range(10, 31):
                    changed.putpixel((x, y), (0, 0, 0))
            changed.save(current)

            failures = process_results(
                {
                    "featured": {
                        "baseline": str(baseline),
                        "target_baseline": str(baseline),
                        "legacy_baseline": None,
                        "current": str(current),
                        "diff": str(diff),
                        "attempt": 2,
                        "dynamic_strategy": "mask_content",
                        "structural_status": "passed",
                        "structural_issues": [],
                        "content_mask_boxes": [
                            {
                                "left": 8,
                                "top": 8,
                                "right": 33,
                                "bottom": 33,
                            }
                        ],
                    }
                },
                "fixture",
                "visual",
                "home",
                manager=manager,
            )
            result = get_results()[-1]

            self.assertEqual([], failures)
            self.assertEqual("content_changed", result["status"])
            self.assertEqual(1, result["content_mask_count"])
            self.assertFalse(result["retained"])

    def test_case_size_tolerance_is_recorded_in_result(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifacts"
            manager = ScreenshotArtifactManager(
                "fixture",
                "home",
                viewport="desktop",
                run_id="run",
                site_config={
                    "site": "fixture",
                    "artifacts": {
                        "screenshot_retention": {
                            "mode": "evidence_only",
                        }
                    },
                },
                page_config={
                    "size_tolerance": {
                        "currency": {
                            "width_px": 2,
                            "height_px": 2,
                            "ratio": 0.03,
                        }
                    }
                },
                root=root,
            )
            baseline = Path(temp_dir) / "baseline.png"
            current = manager.temporary_path("currency", "current")
            diff = manager.temporary_path("currency", "diff")
            Image.new("RGB", (69, 34), (245, 245, 245)).save(
                baseline
            )
            Image.new("RGB", (68, 34), (245, 245, 245)).save(
                current
            )

            failures = process_results(
                {
                    "currency": {
                        "baseline": str(baseline),
                        "target_baseline": str(baseline),
                        "legacy_baseline": None,
                        "current": str(current),
                        "diff": str(diff),
                    }
                },
                "fixture",
                "visual",
                "home",
                manager=manager,
            )
            result = get_results()[-1]

            self.assertEqual([], failures)
            self.assertEqual("passed", result["status"])
            self.assertEqual([69, 34], list(result["baseline_size"]))
            self.assertEqual([68, 34], list(result["current_size"]))
            self.assertEqual(-1, result["width_delta"])
            self.assertEqual(0, result["height_delta"])
            self.assertTrue(result["normalized_for_compare"])
            self.assertEqual(
                "case_config",
                result["applied_tolerance"]["source"],
            )

    def test_structural_failure_cannot_be_content_changed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "artifacts"
            manager = ScreenshotArtifactManager(
                "fixture",
                "collection",
                viewport="desktop",
                run_id="run",
                site_config={
                    "site": "fixture",
                    "artifacts": {
                        "screenshot_retention": {
                            "mode": "evidence_only",
                        }
                    },
                },
                page_config={
                    "dynamic_regions": [
                        {
                            "name": "product_grid",
                            "module": "product_grid",
                            "strategy": "layout_only",
                        }
                    ]
                },
                root=root,
            )
            baseline = Path(temp_dir) / "baseline.png"
            current = manager.temporary_path("product_grid", "current")
            diff = manager.temporary_path("product_grid", "diff")
            Image.new("RGB", (20, 20), "white").save(baseline)
            Image.new("RGB", (20, 20), "black").save(current)

            failures = process_results(
                {
                    "product_grid": {
                        "baseline": str(baseline),
                        "target_baseline": str(baseline),
                        "legacy_baseline": None,
                        "current": str(current),
                        "diff": str(diff),
                        "dynamic_strategy": "layout_only",
                        "structural_status": "failed",
                        "structural_issues": [
                            "product_grid_below_minimum_count"
                        ],
                    }
                },
                "fixture",
                "visual",
                "collection",
                manager=manager,
            )
            result = get_results()[-1]

            self.assertTrue(failures)
            self.assertEqual("failed", result["status"])
            self.assertTrue(result["affects_exit_code"])
            self.assertTrue(result["retained"])

    def test_missing_filter_sort_and_add_to_cart_are_failures(self):
        modules = {
            "filter": ("css", ".filter"),
            "sort": ("css", ".sort"),
            "add_to_cart": ("css", "button[name='add']"),
        }
        with patch(
            "playwright_checks.utils.dom.locate_element",
            side_effect=RuntimeError("missing"),
        ):
            failures = dom_check(object(), modules)

        self.assertEqual(3, len(failures))
        for name in modules:
            self.assertTrue(
                any(f"DOM [{name}]" in value for value in failures)
            )

    def test_stable_monitoring_product_reports_http_unavailable(self):
        class Navigation:
            status = 404

        class Runtime:
            navigation = Navigation()

        class PageModel:
            runtime = Runtime()

        self.assertTrue(
            monitoring_product_is_unavailable(
                PageModel(),
                {"monitoring_product": {"stable": True}},
            )
        )
        self.assertFalse(
            monitoring_product_is_unavailable(
                PageModel(),
                {"monitoring_product": {"stable": False}},
            )
        )


if __name__ == "__main__":
    unittest.main()
