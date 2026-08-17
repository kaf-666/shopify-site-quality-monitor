import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from playwright_checks.artifacts.dynamic import dynamic_region_for_case
from playwright_checks.artifacts.screenshot_manager import (
    ScreenshotArtifactManager,
)
from playwright_checks.core.config_loader import load_settings
from playwright_checks.core.paths import current_run_id, relative_to_project
from playwright_checks.core.test_results import add_result
from playwright_checks.core.viewport import get_current_viewport_name
from playwright_checks.core.visual_policy import (
    purpose_affects_exit_code,
    screenshot_case_policy,
)


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
    return _env_bool("CI") is True


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
    if env_value is not None:
        return env_value

    if is_ci_environment():
        return True

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


def _normalize_to_size(
    image,
    target_size,
    background=(255, 255, 255),
):
    if image.size == target_size:
        return image

    normalized = Image.new("RGB", target_size, background)
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


def compare_images(
    img1_path,
    img2_path,
    diff_path,
    size_tolerance=None,
):
    base_image = Image.open(img1_path).convert("RGB")
    current_image = Image.open(img2_path).convert("RGB")
    width_delta = current_image.width - base_image.width
    height_delta = current_image.height - base_image.height
    applied_tolerance = _resolved_size_tolerance(size_tolerance)
    size_within_tolerance = (
        _dimension_within_tolerance(
            width_delta,
            base_image.width,
            applied_tolerance["width_px"],
            applied_tolerance["ratio"],
        )
        and _dimension_within_tolerance(
            height_delta,
            base_image.height,
            applied_tolerance["height_px"],
            applied_tolerance["ratio"],
        )
    )
    normalized_for_compare = (
        base_image.size != current_image.size
        and size_within_tolerance
    )
    details = {
        "baseline_size": base_image.size,
        "current_size": current_image.size,
        "dimension_changed": base_image.size != current_image.size,
        "width_delta": width_delta,
        "height_delta": height_delta,
        "width_delta_px": width_delta,
        "height_delta_px": height_delta,
        "applied_tolerance": applied_tolerance,
        "normalized_for_compare": normalized_for_compare,
        "size_within_tolerance": size_within_tolerance,
        "size_mismatch": not size_within_tolerance,
    }

    background = (
        base_image.getpixel(
            (
                max(0, base_image.width - 1),
                max(0, base_image.height - 1),
            )
        )
        if size_tolerance is not None and size_within_tolerance
        else (255, 255, 255)
    )
    current_image = _normalize_to_size(
        current_image,
        base_image.size,
        background=background,
    )

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


def _resolved_size_tolerance(size_tolerance):
    if size_tolerance is None:
        return {
            "width_px": WIDTH_TOLERANCE_PX,
            "height_px": HEIGHT_TOLERANCE_PX,
            "ratio": None,
            "source": "legacy_default",
        }
    value = dict(size_tolerance or {})
    ratio = value.get("ratio")
    return {
        "width_px": (
            int(value["width_px"])
            if value.get("width_px") is not None
            else None
        ),
        "height_px": (
            int(value["height_px"])
            if value.get("height_px") is not None
            else None
        ),
        "ratio": float(ratio) if ratio is not None else None,
        "source": "case_config",
    }


def _dimension_within_tolerance(
    delta,
    baseline_dimension,
    pixel_tolerance,
    ratio_tolerance,
):
    difference = abs(int(delta))
    limits = []
    if pixel_tolerance is not None:
        limits.append(float(pixel_tolerance))
    if ratio_tolerance is not None:
        limits.append(
            max(int(baseline_dimension), 1)
            * float(ratio_tolerance)
        )
    return difference <= max(limits) if limits else difference == 0


def case_size_tolerance(page_config, case):
    configured = (page_config or {}).get("size_tolerance")
    if not isinstance(configured, dict):
        return None
    if any(
        key in configured
        for key in ("width_px", "height_px", "ratio")
    ):
        return configured
    selected = configured.get(case)
    return selected if isinstance(selected, dict) else None


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
        "result_type": "visual",
        "site": site,
        "suite": suite,
        "run_id": current_run_id(),
        "viewport": get_current_viewport_name(),
        "page": page,
        "case": case,
        "status": status,
        "visual_status": status,
        "ratio": ratio,
        "threshold": CHANGE_THRESHOLD,
        "warning_threshold": WARNING_THRESHOLD,
        "baseline": None,
        "target_baseline": None,
        "legacy_baseline": None,
        "current": None,
        "diff": None,
        "retained": False,
        "retention_reason": None,
        "structural_status": "passed",
        "content_changes": [],
        "affects_exit_code": status == "failed",
    }

    if paths:
        baseline = paths.get("baseline")
        target_baseline = paths.get("target_baseline")
        legacy_baseline = paths.get("legacy_baseline")
        current = _existing_path(paths.get("current"))
        diff = _existing_path(paths.get("diff"))
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
                    "current": os.path.abspath(current) if current else None,
                    "diff": os.path.abspath(diff) if diff else None,
                }
            },
        })

    if error:
        result["error"] = error

    if details:
        result.update(details)

    return result


def _existing_path(value):
    return value if value and os.path.isfile(value) else None


def _masked_compare_images(
    manager,
    name,
    paths,
    boxes,
    coordinate_size=None,
):
    attempt = int(paths.get("attempt", 1) or 1)
    outputs = {}
    for kind, source_key in (
        ("masked-baseline", "baseline"),
        ("masked-current", "current"),
    ):
        source = paths[source_key]
        target = manager.temporary_path(
            name,
            kind,
            attempt=attempt,
        )
        with Image.open(source) as opened:
            image = opened.copy()
        draw = ImageDraw.Draw(image)
        fill = _mask_fill(image.mode)
        source_width = float(
            (coordinate_size or {}).get("width", 0) or image.width
        )
        source_height = float(
            (coordinate_size or {}).get("height", 0) or image.height
        )
        scale_x = image.width / max(source_width, 1)
        scale_y = image.height / max(source_height, 1)
        for box in boxes:
            left = max(
                0,
                int(float(box.get("left", 0)) * scale_x) - 2,
            )
            top = max(
                0,
                int(float(box.get("top", 0)) * scale_y) - 2,
            )
            right = min(
                image.width,
                int(float(box.get("right", 0)) * scale_x) + 2,
            )
            bottom = min(
                image.height,
                int(float(box.get("bottom", 0)) * scale_y) + 2,
            )
            if right > left and bottom > top:
                draw.rectangle(
                    (left, top, right, bottom),
                    fill=fill,
                )
        image.save(target)
        outputs[source_key] = str(target)
    return outputs["baseline"], outputs["current"]


def _mask_fill(mode):
    if mode == "RGBA":
        return (255, 0, 255, 255)
    if mode == "RGB":
        return (255, 0, 255)
    if mode in ("LA",):
        return (128, 255)
    return 128


def _finalize_visual_artifacts(
    manager,
    name,
    visual_status,
    paths,
    details,
    retention_status=None,
):
    policy = screenshot_case_policy(
        manager.page,
        name,
        viewport=manager.viewport,
        site_config=manager.site_config,
        page_config=manager.page_config,
    )
    values, retention = manager.finalize_result(
        name,
        retention_status or visual_status,
        paths,
        content_changes=(details or {}).get("content_changes", []),
        structural_status=(details or {}).get(
            "structural_status",
            "passed",
        ),
    )
    merged = dict(details or {})
    merged.update(retention)
    merged.update(
        {
            "screenshot_purpose": policy["purpose"],
            "source_case": name,
        }
    )
    merged["affects_exit_code"] = purpose_affects_exit_code(
        policy["purpose"],
        visual_status,
        strict_warnings=STRICT_WARNINGS,
    )
    return values, merged


def _add_failed_result(
    site,
    suite,
    page,
    name,
    paths,
    error,
    failures,
    manager,
    retention_status="capture_failed",
    details=None,
):
    print(f"FAIL [{name}] {error}")
    policy = screenshot_case_policy(
        manager.page,
        name,
        viewport=manager.viewport,
        site_config=manager.site_config,
        page_config=manager.page_config,
    )
    if purpose_affects_exit_code(policy["purpose"], "failed"):
        failures.append(f"visual [{name}] {error}")
    result_paths, result_details = _finalize_visual_artifacts(
        manager,
        name,
        "failed",
        paths,
        {
            **capture_metadata(paths),
            **(details or {}),
        },
        retention_status=retention_status,
    )
    add_result(
        build_result(
            site,
            suite,
            page,
            (paths or {}).get("report_case", policy["report_case"]),
            "failed",
            result_paths,
            error=error,
            details=result_details,
        )
    )


def process_results(
    results,
    site="mondressy_US",
    suite="visual",
    page=None,
    manager=None,
):
    failures = []
    manager = manager or ScreenshotArtifactManager(
        site,
        page or "unknown",
    )

    for name, paths in results.items():
        policy = screenshot_case_policy(
            manager.page,
            name,
            viewport=manager.viewport,
            site_config=manager.site_config,
            page_config=manager.page_config,
        )
        result_case = (paths or {}).get(
            "report_case",
            policy["report_case"],
        )
        if paths is None or paths.get("error"):
            error = paths.get("error") if paths else "capture failed"
            _add_failed_result(
                site,
                suite,
                page,
                name,
                paths,
                error,
                failures,
                manager,
            )
            continue

        cur = paths["current"]
        base = paths["baseline"]
        diff = paths["diff"]
        dynamic_region = dynamic_region_for_case(
            manager.page_config,
            name,
        )
        dynamic_strategy = (
            dynamic_region.get("strategy")
            if dynamic_region
            else paths.get("dynamic_strategy")
        )
        structural_status = paths.get("structural_status", "passed")
        structural_issues = list(paths.get("structural_issues", []))
        structural_diagnostics = dict(
            paths.get("structural_diagnostics", {}) or {}
        )
        size_tolerance = case_size_tolerance(
            manager.page_config,
            name,
        )

        if policy["purpose"] in ("structure_only", "evidence_only"):
            if (
                policy["purpose"] == "structure_only"
                and structural_status != "passed"
            ):
                error = (
                    "structural checks failed: "
                    + ", ".join(structural_issues)
                )
                _add_failed_result(
                    site,
                    suite,
                    page,
                    result_case,
                    paths,
                    error,
                    failures,
                    manager,
                    retention_status="failed",
                    details={
                        "structural_status": structural_status,
                        "structural_issues": structural_issues,
                        "structural_diagnostics": structural_diagnostics,
                        "pixel_compare_skipped": True,
                    },
                )
                continue
            result_paths, result_details = _finalize_visual_artifacts(
                manager,
                name,
                "passed",
                paths,
                {
                    **capture_metadata(paths),
                    "structural_status": structural_status,
                    "structural_issues": structural_issues,
                    "structural_diagnostics": structural_diagnostics,
                    "pixel_compare_skipped": True,
                },
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    result_case,
                    "passed",
                    result_paths,
                    details=result_details,
                )
            )
            continue

        if not os.path.exists(base) and dynamic_strategy not in (
            "ignore_visual",
            "layout_only",
        ):
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
                _add_failed_result(
                    site,
                    suite,
                    page,
                    result_case,
                    paths,
                    error,
                    failures,
                    manager,
                    retention_status="baseline_missing",
                    details={
                        "structural_status": structural_status,
                        "structural_issues": structural_issues,
                        "structural_diagnostics": structural_diagnostics,
                    },
                )
                continue

            import shutil
            target_base = paths.get("target_baseline", base)
            os.makedirs(os.path.dirname(target_base), exist_ok=True)
            print(f"INIT [{name}] baseline")
            shutil.copy2(cur, target_base)
            paths["baseline"] = target_base
            result_paths, result_details = _finalize_visual_artifacts(
                manager,
                name,
                "initialized",
                paths,
                capture_metadata(paths),
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    result_case,
                    "initialized",
                    result_paths,
                    ratio=0.0,
                    details=result_details,
                )
            )
            continue

        if dynamic_strategy == "ignore_visual":
            if structural_status != "passed":
                error = (
                    "dynamic region structural checks failed: "
                    + ", ".join(structural_issues)
                )
                _add_failed_result(
                    site,
                    suite,
                    page,
                    result_case,
                    paths,
                    error,
                    failures,
                    manager,
                    retention_status="failed",
                    details={
                        "dynamic_strategy": dynamic_strategy,
                        "structural_status": structural_status,
                        "structural_issues": structural_issues,
                        "structural_diagnostics": structural_diagnostics,
                    },
                )
                continue
            result_paths, result_details = _finalize_visual_artifacts(
                manager,
                name,
                "passed",
                paths,
                {
                    **capture_metadata(paths),
                    "dynamic_strategy": dynamic_strategy,
                    "structural_status": structural_status,
                    "structural_issues": structural_issues,
                    "structural_diagnostics": structural_diagnostics,
                },
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    result_case,
                    "passed",
                    result_paths,
                    details=result_details,
                )
            )
            continue

        if structural_status != "passed":
            error = (
                "dynamic region structural checks failed: "
                + ", ".join(structural_issues)
            )
            _add_failed_result(
                site,
                suite,
                page,
                name,
                paths,
                error,
                failures,
                manager,
                retention_status="failed",
                details={
                    "dynamic_strategy": dynamic_strategy,
                    "structural_status": structural_status,
                    "structural_issues": structural_issues,
                    "structural_diagnostics": structural_diagnostics,
                },
            )
            continue

        if dynamic_strategy == "layout_only":
            content_changes = list(paths.get("content_changes", []))
            item_count = (
                (paths.get("layout_snapshot") or {}).get("item_count")
            )
            expected_count = manager.page_config.get("expected_count")
            if (
                item_count is not None
                and expected_count is not None
                and int(item_count) != int(expected_count)
            ):
                content_changes.append("product_count_changed")
            if not content_changes:
                content_changes.append(f"{name}_content_observed")
            details = {
                **capture_metadata(paths),
                "dynamic_strategy": dynamic_strategy,
                "structural_status": structural_status,
                "structural_issues": structural_issues,
                "structural_diagnostics": structural_diagnostics,
                "content_changes": list(dict.fromkeys(content_changes)),
                "layout_snapshot": paths.get("layout_snapshot"),
                "pixel_compare_skipped": True,
            }
            print(
                f"CONTENT_CHANGED [{name}]; "
                "layout-only structural checks passed"
            )
            result_paths, result_details = _finalize_visual_artifacts(
                manager,
                name,
                "content_changed",
                paths,
                details,
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    result_case,
                    "content_changed",
                    result_paths,
                    details=result_details,
                )
            )
            continue

        compare_base = paths.get("compare_baseline", base)
        compare_cur = paths.get("compare_current", cur)
        raw_content_changed = False
        raw_content_ratio = None
        mask_boxes = list(paths.get("content_mask_boxes", []))
        if dynamic_strategy == "mask_content" and mask_boxes:
            raw_diff = manager.temporary_path(
                name,
                "raw-content-diff",
                attempt=int(paths.get("attempt", 1) or 1),
            )
            raw_ok, raw_content_ratio, _raw_details = manager.compare(
                compare_images,
                base,
                cur,
                str(raw_diff),
                size_tolerance=size_tolerance,
            )
            raw_content_changed = not raw_ok
            try:
                compare_base, compare_cur = _masked_compare_images(
                    manager,
                    result_case,
                    paths,
                    mask_boxes,
                    paths.get("content_mask_coordinate_size"),
                )
            except Exception as error:
                _add_failed_result(
                    site,
                    suite,
                    page,
                    result_case,
                    paths,
                    (
                        "dynamic content mask preparation failed: "
                        f"{type(error).__name__}: {error}"
                    ),
                    failures,
                    manager,
                    retention_status="failed",
                    details={
                        "dynamic_strategy": dynamic_strategy,
                        "structural_status": structural_status,
                        "structural_issues": structural_issues,
                        "structural_diagnostics": structural_diagnostics,
                    },
                )
                continue

        ok, ratio, details = manager.compare(
            compare_images,
            compare_base,
            compare_cur,
            diff,
            size_tolerance=size_tolerance,
        )
        details.update(capture_metadata(paths))
        details.update(
            {
                "dynamic_strategy": dynamic_strategy,
                "structural_status": structural_status,
                "structural_issues": structural_issues,
                "structural_diagnostics": structural_diagnostics,
                "content_mask_count": len(mask_boxes),
                "raw_content_ratio": raw_content_ratio,
            }
        )

        if ok and dynamic_strategy == "mask_content" and raw_content_changed:
            content_changes = list(paths.get("content_changes", []))
            if not content_changes:
                content_changes.append(f"{name}_content_changed")
            details["content_changes"] = list(dict.fromkeys(content_changes))
            print(
                f"CONTENT_CHANGED [{name}] {raw_content_ratio:.2%}; "
                "masked structure checks passed"
            )
            result_paths, result_details = _finalize_visual_artifacts(
                manager,
                name,
                "content_changed",
                paths,
                details,
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    result_case,
                    "content_changed",
                    result_paths,
                    ratio=raw_content_ratio,
                    details=result_details,
                )
            )
            continue

        if ok:
            print(f"OK [{name}] normal {ratio:.4%}")
            result_paths, result_details = _finalize_visual_artifacts(
                manager,
                name,
                "passed",
                paths,
                details,
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    result_case,
                    "passed",
                    result_paths,
                    ratio=ratio,
                    details=result_details,
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
            if purpose_affects_exit_code(policy["purpose"], "failed"):
                failures.append(
                    f"visual [{name}] size changed "
                    f"{baseline_size} -> {current_size}"
                )
            result_paths, result_details = _finalize_visual_artifacts(
                manager,
                name,
                "failed",
                paths,
                details,
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    result_case,
                    "failed",
                    result_paths,
                    ratio=ratio,
                    details=result_details,
                )
            )
            continue

        if ratio < WARNING_THRESHOLD:
            print(
                f"WARN [{name}] minor changed {ratio:.2%}; "
                "baseline was not updated"
            )
            result_paths, result_details = _finalize_visual_artifacts(
                manager,
                name,
                "warning",
                paths,
                details,
            )
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    result_case,
                    "warning",
                    result_paths,
                    ratio=ratio,
                    details=result_details,
                )
            )
            if purpose_affects_exit_code(
                policy["purpose"],
                "warning",
                strict_warnings=STRICT_WARNINGS,
            ):
                failures.append(f"visual [{name}] warning {ratio:.2%}")
            continue

        print(f"FAIL [{name}] changed {ratio:.2%}")
        if purpose_affects_exit_code(policy["purpose"], "failed"):
            failures.append(
                f"visual [{name}] diff {ratio:.2%} "
                f"exceeds {CHANGE_THRESHOLD:.2%}"
            )
        result_paths, result_details = _finalize_visual_artifacts(
            manager,
            name,
            "failed",
            paths,
            details,
        )
        add_result(
            build_result(
                site,
                suite,
                page,
                result_case,
                "failed",
                result_paths,
                ratio=ratio,
                details=result_details,
            )
        )

    return failures
