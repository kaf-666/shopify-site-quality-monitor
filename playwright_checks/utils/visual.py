import os

import numpy as np
from PIL import Image, ImageFilter

from playwright_checks.core.config_loader import load_settings
from playwright_checks.core.paths import current_run_id, relative_to_project
from playwright_checks.core.test_results import add_result
from playwright_checks.core.viewport import get_current_viewport_name


def _visual_setting(name, default):
    settings = load_settings()
    visual_settings = settings.get("visual", {})
    return float(visual_settings.get(name, default))


TRUE_VALUES = ("1", "true", "yes", "on")
FALSE_VALUES = ("0", "false", "no", "off")


def _env_bool(name):
    env_value = os.environ.get(name)
    if env_value is None:
        return None

    normalized = env_value.strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False

    return None


def is_ci_environment():
    return _env_bool("CI") is True or bool(os.environ.get("JENKINS_URL"))


def _bool_setting(section, name, env_name, default):
    env_value = _env_bool(env_name)
    if env_value is not None:
        return env_value

    settings = load_settings()
    return bool(settings.get(section, {}).get(name, default))


def _baseline_init_requested():
    return _bool_setting(
        "ci",
        "allow_baseline_init",
        "ALLOW_BASELINE_INIT",
        False,
    )


def _baseline_init_allowed():
    if not _baseline_init_requested():
        return False

    if not is_ci_environment():
        return True

    return _env_bool("FORCE_BASELINE_INIT") is True


def _baseline_init_blocked_by_ci():
    return (
        is_ci_environment()
        and _baseline_init_requested()
        and _env_bool("FORCE_BASELINE_INIT") is not True
    )


def _strict_warnings_enabled():
    env_value = _env_bool("VISUAL_STRICT_WARNINGS")
    if env_value is True:
        return True

    if is_ci_environment():
        return True

    if env_value is False:
        return False

    settings = load_settings()
    return bool(settings.get("ci", {}).get("strict_warnings", False))


CHANGE_THRESHOLD = _visual_setting("change_threshold", 0.005)
WARNING_THRESHOLD = _visual_setting("warning_threshold", 0.02)
WIDTH_TOLERANCE_PX = int(_visual_setting("width_tolerance_px", 0))
HEIGHT_TOLERANCE_PX = int(_visual_setting("height_tolerance_px", 10))
ALIGNMENT_TOLERANCE_PX = int(_visual_setting("alignment_tolerance_px", 1))
ALIGNMENT_MAX_PIXELS = int(_visual_setting("alignment_max_pixels", 5000000))
PERCEPTUAL_FALLBACK_ENABLED = bool(
    _visual_setting("perceptual_fallback_enabled", 1)
)
PERCEPTUAL_BLUR_RADIUS = _visual_setting("perceptual_blur_radius", 1.75)
PERCEPTUAL_BAND_HEIGHT_PX = int(
    _visual_setting("perceptual_band_height_px", 120)
)
PERCEPTUAL_ALIGNMENT_TOLERANCE_PX = int(
    _visual_setting("perceptual_alignment_tolerance_px", 6)
)
PERCEPTUAL_MAX_PIXELS = int(
    _visual_setting("perceptual_max_pixels", 5000000)
)
LARGE_IMAGE_STRUCTURAL_FALLBACK_ENABLED = bool(
    _visual_setting("large_image_structural_fallback_enabled", 1)
)
STRUCTURAL_TILE_SIZE_PX = int(
    _visual_setting("structural_tile_size_px", 128)
)
STRUCTURAL_HASH_DISTANCE_THRESHOLD = _visual_setting(
    "structural_hash_distance_threshold", 0.13
)
STRUCTURAL_CHANGED_TILE_FRACTION_THRESHOLD = _visual_setting(
    "structural_changed_tile_fraction_threshold", 0.15
)
ALLOW_BASELINE_INIT = _baseline_init_allowed()
BASELINE_INIT_BLOCKED_BY_CI = _baseline_init_blocked_by_ci()
STRICT_WARNINGS = _strict_warnings_enabled()


def _normalize_to_size(image, target_size):
    if image.size == target_size:
        return image

    normalized = Image.new("RGB", target_size, (255, 255, 255))
    crop = image.crop((
        0,
        0,
        min(image.width, target_size[0]),
        min(image.height, target_size[1]),
    ))
    normalized.paste(crop, (0, 0))
    return normalized


def _shift_array(image, dx, dy):
    """Translate an RGB array on a white canvas without wrapping pixels."""

    height, width = image.shape[:2]
    shifted = np.full_like(image, 255)

    source_x = max(0, -dx)
    source_y = max(0, -dy)
    target_x = max(0, dx)
    target_y = max(0, dy)
    copy_width = min(width - source_x, width - target_x)
    copy_height = min(height - source_y, height - target_y)

    if copy_width > 0 and copy_height > 0:
        shifted[
            target_y:target_y + copy_height,
            target_x:target_x + copy_width,
        ] = image[
            source_y:source_y + copy_height,
            source_x:source_x + copy_width,
        ]

    return shifted


def _diff_ratio(base, current):
    diff = np.abs(np.subtract(base, current, dtype=np.int16))
    significant = (diff > 25).any(axis=2)
    return significant.sum() / significant.size, significant


def _perceptual_diff_ratio(base, current):
    """Compare low-frequency visual structure with small per-band Y alignment.

    Shopify's CDN can re-encode an unchanged image at the same URL.  It can
    also change responsive rounding by a few pixels.  The strict comparison
    remains authoritative unless this conservative fallback falls below the
    normal pass threshold.
    """

    base_image = Image.fromarray(base).filter(
        ImageFilter.GaussianBlur(PERCEPTUAL_BLUR_RADIUS)
    )
    current_image = Image.fromarray(current).filter(
        ImageFilter.GaussianBlur(PERCEPTUAL_BLUR_RADIUS)
    )
    base_array = np.asarray(base_image, dtype=np.int16)
    current_array = np.asarray(current_image, dtype=np.int16)
    height, width = base_array.shape[:2]
    band_height = max(1, PERCEPTUAL_BAND_HEIGHT_PX)
    tolerance = max(0, PERCEPTUAL_ALIGNMENT_TOLERANCE_PX)
    significant_pixels = 0
    compared_pixels = 0
    offsets = []

    for start_y in range(0, height, band_height):
        end_y = min(height, start_y + band_height)
        best_count = None
        best_offset = 0

        for offset_y in range(-tolerance, tolerance + 1):
            current_start = start_y + offset_y
            current_end = end_y + offset_y
            if current_start < 0 or current_end > height:
                continue

            diff = np.abs(
                np.subtract(
                    base_array[start_y:end_y],
                    current_array[current_start:current_end],
                    dtype=np.int16,
                )
            )
            count = int((diff > 25).any(axis=2).sum())
            if best_count is None or count < best_count:
                best_count = count
                best_offset = offset_y

        significant_pixels += best_count or 0
        compared_pixels += (end_y - start_y) * width
        offsets.append(best_offset)

    ratio = significant_pixels / max(1, compared_pixels)
    return ratio, offsets


def _structural_diff_score(base, current):
    """Compare large images by tiled difference hashes.

    Long Shopify pages can exceed the safe memory budget for the blurred
    pixel fallback.  A difference hash keeps only the direction of local
    luminance changes, so it is insensitive to CDN colour/quality variants
    while still reacting strongly when product imagery or layout changes.
    """

    base_image = Image.fromarray(base).convert("L")
    current_image = Image.fromarray(current).convert("L")
    tile_size = max(32, STRUCTURAL_TILE_SIZE_PX)
    distances = []

    for top in range(0, base_image.height, tile_size):
        for left in range(0, base_image.width, tile_size):
            box = (
                left,
                top,
                min(left + tile_size, base_image.width),
                min(top + tile_size, base_image.height),
            )
            base_tile = np.asarray(
                base_image.crop(box).resize((9, 8), Image.Resampling.LANCZOS)
            )
            current_tile = np.asarray(
                current_image.crop(box).resize((9, 8), Image.Resampling.LANCZOS)
            )
            base_hash = base_tile[:, 1:] > base_tile[:, :-1]
            current_hash = current_tile[:, 1:] > current_tile[:, :-1]
            distances.append(float(np.mean(base_hash != current_hash)))

    if not distances:
        return 0.0, 0.0

    distance_array = np.asarray(distances)
    mean_distance = float(distance_array.mean())
    changed_fraction = float((distance_array > 0.30).mean())
    return mean_distance, changed_fraction


def compare_images(img1_path, img2_path, diff_path):
    base_image = Image.open(img1_path).convert("RGB")
    current_image = Image.open(img2_path).convert("RGB")
    width_delta = current_image.width - base_image.width
    height_delta = current_image.height - base_image.height
    size_within_tolerance = (
        abs(width_delta) <= WIDTH_TOLERANCE_PX
        and abs(height_delta) <= HEIGHT_TOLERANCE_PX
    )
    details = {
        "baseline_size": base_image.size,
        "current_size": current_image.size,
        "dimension_changed": base_image.size != current_image.size,
        "width_delta_px": width_delta,
        "height_delta_px": height_delta,
        "size_within_tolerance": size_within_tolerance,
        "size_mismatch": not size_within_tolerance,
    }

    current_image = _normalize_to_size(current_image, base_image.size)

    img1 = np.array(base_image)
    img2 = np.array(current_image)
    ratio, significant = _diff_ratio(img1, img2)
    best_offset = (0, 0)
    best_image = img2

    if img1.shape[0] * img1.shape[1] <= ALIGNMENT_MAX_PIXELS:
        for dx in range(-ALIGNMENT_TOLERANCE_PX, ALIGNMENT_TOLERANCE_PX + 1):
            for dy in range(-ALIGNMENT_TOLERANCE_PX, ALIGNMENT_TOLERANCE_PX + 1):
                if dx == 0 and dy == 0:
                    continue

                aligned = _shift_array(img2, dx, dy)
                aligned_ratio, aligned_significant = _diff_ratio(img1, aligned)
                if aligned_ratio < ratio:
                    ratio = aligned_ratio
                    significant = aligned_significant
                    best_offset = (dx, dy)
                    best_image = aligned

    details["alignment_offset_px"] = best_offset

    raw_ratio = ratio
    details["raw_ratio"] = raw_ratio
    details["perceptual_fallback_used"] = False
    details["large_image_structural_fallback_used"] = False
    pixel_count = img1.shape[0] * img1.shape[1]
    if (
        PERCEPTUAL_FALLBACK_ENABLED
        and raw_ratio >= CHANGE_THRESHOLD
        and not details["size_mismatch"]
        and pixel_count <= PERCEPTUAL_MAX_PIXELS
    ):
        perceptual_ratio, band_offsets = _perceptual_diff_ratio(img1, best_image)
        details["perceptual_ratio"] = perceptual_ratio
        details["perceptual_band_offsets_px"] = band_offsets
        if perceptual_ratio < CHANGE_THRESHOLD:
            ratio = perceptual_ratio
            details["perceptual_fallback_used"] = True

    if (
        LARGE_IMAGE_STRUCTURAL_FALLBACK_ENABLED
        and raw_ratio >= CHANGE_THRESHOLD
        and not details["size_mismatch"]
        and pixel_count > PERCEPTUAL_MAX_PIXELS
    ):
        structural_score, changed_tile_fraction = _structural_diff_score(
            img1, best_image
        )
        details["structural_score"] = structural_score
        details["structural_changed_tile_fraction"] = changed_tile_fraction
        details["structural_hash_distance_threshold"] = (
            STRUCTURAL_HASH_DISTANCE_THRESHOLD
        )
        details["structural_changed_tile_fraction_threshold"] = (
            STRUCTURAL_CHANGED_TILE_FRACTION_THRESHOLD
        )
        if (
            structural_score <= STRUCTURAL_HASH_DISTANCE_THRESHOLD
            and changed_tile_fraction
            <= STRUCTURAL_CHANGED_TILE_FRACTION_THRESHOLD
        ):
            normalized_score = max(
                structural_score / max(
                    STRUCTURAL_HASH_DISTANCE_THRESHOLD, 1e-9
                ),
                changed_tile_fraction / max(
                    STRUCTURAL_CHANGED_TILE_FRACTION_THRESHOLD, 1e-9
                ),
            )
            ratio = normalized_score * CHANGE_THRESHOLD * 0.99
            details["large_image_structural_fallback_used"] = True

    if ratio > 0:
        highlight = best_image.copy()
        highlight[significant] = [255, 0, 0]
        Image.fromarray(highlight).save(diff_path)

    return ratio < CHANGE_THRESHOLD and not details["size_mismatch"], ratio, details


def capture_metadata(paths):
    if not paths:
        return {}

    metadata = {}
    for key in (
        "capture_duration_ms",
        "capture_attempts",
        "capture_height_strategy",
        "capture_height_px",
    ):
        if key in paths:
            metadata[key] = paths[key]

    return metadata


def build_result(
    site,
    suite,
    page,
    case,
    status,
    paths,
    ratio=None,
    error=None,
    details=None
):
    result = {
        "site": site,
        "suite": suite,
        "run_id": current_run_id(),
        "viewport": get_current_viewport_name(),
        "page": page,
        "case": case,
        "status": status,
        "ratio": ratio,
        "threshold": CHANGE_THRESHOLD,
        "warning_threshold": WARNING_THRESHOLD,
        "baseline": None,
        "target_baseline": None,
        "legacy_baseline": None,
        "current": None,
        "diff": None,
    }

    if paths:
        baseline = paths.get("baseline")
        target_baseline = paths.get("target_baseline")
        legacy_baseline = paths.get("legacy_baseline")
        current = paths.get("current")
        diff = paths.get("diff")
        result.update({
            "baseline": relative_to_project(baseline),
            "target_baseline": relative_to_project(target_baseline),
            "legacy_baseline": relative_to_project(legacy_baseline),
            "current": relative_to_project(current),
            "diff": relative_to_project(diff),
            "debug": {
                "absolute_paths": {
                    "baseline": os.path.abspath(baseline)
                    if baseline else None,
                    "target_baseline": os.path.abspath(target_baseline)
                    if target_baseline else None,
                    "legacy_baseline": os.path.abspath(legacy_baseline)
                    if legacy_baseline else None,
                    "current": os.path.abspath(current)
                    if current else None,
                    "diff": os.path.abspath(diff)
                    if diff else None,
                }
            },
        })

    if error:
        result["error"] = error

    if details:
        result.update(details)

    return result


def _add_failed_result(site, suite, page, name, paths, error, failures):
    print(f"FAIL [{name}] {error}")
    failures.append(f"visual [{name}] {error}")
    add_result(
        build_result(
            site,
            suite,
            page,
            name,
            "failed",
            paths,
            error=error,
            details=capture_metadata(paths),
        )
    )


def process_results(results, site="mondressy_US", suite="visual", page=None):
    failures = []

    for name, paths in results.items():
        if paths is None or paths.get("error"):
            error = paths.get("error") if paths else "capture failed"
            _add_failed_result(site, suite, page, name, paths, error, failures)
            continue

        cur = paths["current"]
        base = paths["baseline"]
        diff = paths["diff"]

        if not os.path.exists(base):
            if not ALLOW_BASELINE_INIT:
                if BASELINE_INIT_BLOCKED_BY_CI:
                    error = (
                        "baseline missing; CI/Jenkins cannot auto-generate "
                        "baselines. Set FORCE_BASELINE_INIT=1 with "
                        "ALLOW_BASELINE_INIT=1 only for an approved baseline job"
                    )
                else:
                    error = (
                        "baseline missing; set ALLOW_BASELINE_INIT=1 "
                        "to generate it locally"
                    )
                _add_failed_result(site, suite, page, name, paths, error, failures)
                continue

            import shutil
            target_base = paths.get("target_baseline", base)
            os.makedirs(os.path.dirname(target_base), exist_ok=True)
            print(f"INIT [{name}] baseline")
            shutil.copy2(cur, target_base)
            paths["baseline"] = target_base
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    name,
                    "initialized",
                    paths,
                    ratio=0.0,
                    details=capture_metadata(paths),
                )
            )
            continue

        compare_base = paths.get("compare_baseline", base)
        compare_cur = paths.get("compare_current", cur)

        ok, ratio, details = compare_images(
            compare_base,
            compare_cur,
            diff
        )
        details.update(capture_metadata(paths))

        if ok:
            print(f"OK [{name}] normal {ratio:.4%}")
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    name,
                    "passed",
                    paths,
                    ratio=ratio,
                    details=details,
                )
            )
            continue

        if details.get("size_mismatch"):
            baseline_size = details.get("baseline_size")
            current_size = details.get("current_size")
            print(
                f"FAIL [{name}] size changed "
                f"{baseline_size} -> {current_size}; "
                "baseline was not updated"
            )
            failures.append(
                f"visual [{name}] size changed "
                f"{baseline_size} -> {current_size}"
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    name,
                    "failed",
                    paths,
                    ratio=ratio,
                    details=details,
                )
            )
            continue

        if ratio < WARNING_THRESHOLD:
            print(
                f"WARN [{name}] minor changed {ratio:.2%}; "
                "baseline was not updated"
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    name,
                    "warning",
                    paths,
                    ratio=ratio,
                    details=details,
                )
            )
            if STRICT_WARNINGS:
                failures.append(f"visual [{name}] warning {ratio:.2%}")
            continue

        print(f"FAIL [{name}] changed {ratio:.2%}")
        failures.append(
            f"visual [{name}] diff {ratio:.2%} exceeds {CHANGE_THRESHOLD:.2%}"
        )
        add_result(
            build_result(
                site,
                suite,
                page,
                name,
                "failed",
                paths,
                ratio=ratio,
                details=details,
            )
        )

    return failures
