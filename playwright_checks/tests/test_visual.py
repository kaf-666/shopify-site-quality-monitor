import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from playwright_checks.utils.visual import _structural_diff_score, compare_images


class CompareImagesTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def save(self, name, image):
        path = self.root / name
        image.save(path)
        return path

    def compare(self, baseline, current, size_tolerance=None):
        return compare_images(
            str(baseline),
            str(current),
            str(self.root / "diff.png"),
            size_tolerance=size_tolerance,
        )

    def test_one_pixel_capture_shift_is_aligned(self):
        baseline = Image.new("RGB", (80, 60), "white")
        ImageDraw.Draw(baseline).rectangle((20, 15, 50, 40), fill="black")

        current = Image.new("RGB", baseline.size, "white")
        current.paste(baseline.crop((0, 0, 80, 59)), (0, 1))

        ok, ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
        )

        self.assertTrue(ok)
        self.assertEqual(ratio, 0.0)
        self.assertEqual(details["alignment_offset_px"], (0, -1))

    def test_small_height_drift_is_tolerated(self):
        baseline = Image.new("RGB", (40, 40), "white")
        current = Image.new("RGB", (40, 46), "white")

        ok, ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
        )

        self.assertTrue(ok)
        self.assertEqual(ratio, 0.0)
        self.assertTrue(details["dimension_changed"])
        self.assertFalse(details["size_mismatch"])

    def test_footer_height_drift_within_tolerance_is_tolerated(self):
        baseline = Image.new("RGB", (40, 40), "white")
        current = Image.new("RGB", (40, 62), "white")

        ok, ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
        )

        self.assertTrue(ok)
        self.assertEqual(ratio, 0.0)
        self.assertEqual(details["height_delta_px"], 22)

    def test_real_content_change_still_fails(self):
        baseline = Image.new("RGB", (40, 40), "white")
        current = Image.new("RGB", (40, 40), "black")

        ok, ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
        )

        self.assertFalse(ok)
        self.assertGreater(ratio, 0.9)
        self.assertFalse(details["size_mismatch"])

    def test_currency_one_pixel_width_delta_continues_compare(self):
        baseline = Image.new("RGB", (69, 34), (245, 245, 245))
        current = Image.new("RGB", (68, 34), (245, 245, 245))

        ok, ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
            {
                "width_px": 2,
                "height_px": 2,
                "ratio": 0.03,
            },
        )

        self.assertTrue(ok)
        self.assertEqual(0.0, ratio)
        self.assertEqual(-1, details["width_delta"])
        self.assertEqual(0, details["height_delta"])
        self.assertTrue(details["normalized_for_compare"])
        self.assertEqual(
            "case_config",
            details["applied_tolerance"]["source"],
        )

    def test_currency_width_delta_over_tolerance_fails(self):
        baseline = Image.new("RGB", (69, 34), "white")
        current = Image.new("RGB", (75, 34), "white")

        ok, _ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
            {
                "width_px": 2,
                "height_px": 2,
                "ratio": 0.03,
            },
        )

        self.assertFalse(ok)
        self.assertTrue(details["size_mismatch"])
        self.assertFalse(details["normalized_for_compare"])

    def test_currency_height_delta_over_tolerance_fails(self):
        baseline = Image.new("RGB", (69, 34), "white")
        current = Image.new("RGB", (69, 37), "white")

        ok, _ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
            {
                "width_px": 2,
                "height_px": 2,
                "ratio": 0.03,
            },
        )

        self.assertFalse(ok)
        self.assertEqual(3, details["height_delta"])
        self.assertTrue(details["size_mismatch"])

    def test_tolerated_geometry_with_large_pixel_diff_still_fails(self):
        baseline = Image.new("RGB", (69, 34), "white")
        current = Image.new("RGB", (68, 34), "black")

        ok, ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
            {
                "width_px": 2,
                "height_px": 2,
                "ratio": 0.03,
            },
        )

        self.assertFalse(ok)
        self.assertGreater(ratio, 0.9)
        self.assertFalse(details["size_mismatch"])
        self.assertTrue(details["normalized_for_compare"])

    def test_unconfigured_width_delta_keeps_legacy_behavior(self):
        baseline = Image.new("RGB", (69, 34), "white")
        current = Image.new("RGB", (68, 34), "white")

        ok, _ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
        )

        self.assertFalse(ok)
        self.assertTrue(details["size_mismatch"])
        self.assertEqual(
            "legacy_default",
            details["applied_tolerance"]["source"],
        )

    def test_cdn_reencoding_uses_perceptual_fallback(self):
        baseline = Image.new("RGB", (240, 240), "white")
        draw = ImageDraw.Draw(baseline)
        for y in range(240):
            color = (40 + y // 2, 80 + y // 3, 150 + y // 4)
            draw.line((0, y, 239, y), fill=color)
        for x in range(12, 240, 24):
            draw.ellipse((x - 8, 35, x + 16, 190), outline="white", width=3)

        baseline_path = self.save("baseline.png", baseline)
        jpeg_path = self.root / "reencoded.jpg"
        baseline.save(jpeg_path, format="JPEG", quality=72)
        current = Image.open(jpeg_path).convert("RGB")

        ok, ratio, details = self.compare(
            baseline_path,
            self.save("current.png", current),
        )

        self.assertTrue(ok)
        self.assertLess(ratio, details["raw_ratio"])
        self.assertTrue(details["perceptual_fallback_used"])

    def test_local_five_pixel_shift_uses_perceptual_fallback(self):
        baseline = Image.new("RGB", (240, 360), "white")
        draw = ImageDraw.Draw(baseline)
        for y in range(130, 230, 4):
            draw.line((10, y, 229, y), fill=(20, 70, 140), width=2)
        for x in range(20, 230, 30):
            draw.rectangle((x, 145, x + 12, 215), fill=(190, 80, 50))

        current = baseline.copy()
        ImageDraw.Draw(current).rectangle((0, 115, 239, 239), fill="white")
        current.paste(baseline.crop((0, 120, 240, 240)), (0, 115))

        ok, ratio, details = self.compare(
            self.save("baseline.png", baseline),
            self.save("current.png", current),
        )

        self.assertTrue(ok)
        self.assertLess(ratio, details["raw_ratio"])
        self.assertTrue(details["perceptual_fallback_used"])

    def test_structural_hash_separates_tonal_variant_from_replacement(self):
        baseline = Image.new("RGB", (512, 512), "white")
        draw = ImageDraw.Draw(baseline)
        for index in range(4):
            left = index * 128 + 12
            draw.ellipse(
                (left, 60, left + 96, 400),
                fill=(45 + index * 25, 95, 145),
            )
            draw.line((left, 440, left + 96, 440), fill="black", width=5)

        tonal_variant = baseline.point(lambda value: min(255, int(value * 0.82 + 18)))
        replacement = baseline.copy()
        replacement_draw = ImageDraw.Draw(replacement)
        replacement_draw.rectangle((128, 40, 383, 460), fill=(235, 205, 80))
        replacement_draw.line((128, 40, 383, 460), fill="black", width=20)

        tonal_score, tonal_changed = _structural_diff_score(
            np.array(baseline),
            np.array(tonal_variant),
        )
        replacement_score, replacement_changed = _structural_diff_score(
            np.array(baseline),
            np.array(replacement),
        )

        self.assertLess(tonal_score, replacement_score)
        self.assertLess(tonal_changed, replacement_changed)


if __name__ == "__main__":
    unittest.main()
