import os

import numpy as np
from PIL import Image

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
ALLOW_BASELINE_INIT = _baseline_init_allowed()
BASELINE_INIT_BLOCKED_BY_CI = _baseline_init_blocked_by_ci()
STRICT_WARNINGS = _strict_warnings_enabled()


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
