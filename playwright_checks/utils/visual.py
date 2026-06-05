import os

import numpy as np
from PIL import Image

from playwright_checks.core.test_results import add_result
from playwright_checks.core.viewport import get_current_viewport_name


CHANGE_THRESHOLD = 0.005
WARNING_THRESHOLD = 0.02
ALLOW_BASELINE_INIT = os.environ.get("ALLOW_BASELINE_INIT", "").lower() in (
    "1",
    "true",
    "yes",
)
STRICT_WARNINGS = os.environ.get("VISUAL_STRICT_WARNINGS", "").lower() in (
    "1",
    "true",
    "yes",
)


def compare_images(img1_path, img2_path, diff_path):
    base_image = Image.open(img1_path).convert("RGB")
    current_image = Image.open(img2_path).convert("RGB")
    details = {
        "baseline_size": base_image.size,
        "current_size": current_image.size,
        "size_mismatch": base_image.size != current_image.size,
    }

    if details["size_mismatch"]:
        normalized = Image.new("RGB", base_image.size, (255, 255, 255))
        crop = current_image.crop((
            0,
            0,
            min(current_image.width, base_image.width),
            min(current_image.height, base_image.height),
        ))
        normalized.paste(crop, (0, 0))
        current_image = normalized

    img1 = np.array(base_image)
    img2 = np.array(current_image)
    diff = np.abs(img1.astype(int) - img2.astype(int))
    significant = (diff > 25).any(axis=2)
    ratio = significant.sum() / significant.size

    if ratio > 0:
        highlight = img2.copy()
        highlight[significant] = [255, 0, 0]
        Image.fromarray(highlight).save(diff_path)

    return ratio < CHANGE_THRESHOLD and not details["size_mismatch"], ratio, details


def capture_metadata(paths):
    if not paths:
        return {}

    metadata = {}
    for key in ("capture_duration_ms", "capture_attempts"):
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
        "viewport": get_current_viewport_name(),
        "page": page,
        "case": case,
        "status": status,
        "ratio": ratio,
        "threshold": CHANGE_THRESHOLD,
        "warning_threshold": WARNING_THRESHOLD,
        "baseline": None,
        "current": None,
        "diff": None,
    }

    if paths:
        result.update({
            "baseline": os.path.abspath(paths.get("baseline"))
            if paths.get("baseline") else None,
            "current": os.path.abspath(paths.get("current"))
            if paths.get("current") else None,
            "diff": os.path.abspath(paths.get("diff"))
            if paths.get("diff") else None,
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
                error = (
                    "baseline missing; set ALLOW_BASELINE_INIT=1 "
                    "to generate it locally"
                )
                _add_failed_result(site, suite, page, name, paths, error, failures)
                continue

            import shutil
            print(f"INIT [{name}] baseline")
            shutil.copy2(cur, base)
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

        if ratio < WARNING_THRESHOLD:
            reason = "size changed" if details.get("size_mismatch") else "minor changed"
            print(
                f"WARN [{name}] {reason} {ratio:.2%}; "
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
