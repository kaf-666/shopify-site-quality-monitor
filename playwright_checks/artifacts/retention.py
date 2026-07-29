import copy
import os
from dataclasses import dataclass

from playwright_checks.core.config_loader import load_settings


VALID_MODES = ("evidence_only", "standard", "debug")
ENV_OVERRIDES = {
    "max_images_per_page": "SCREENSHOT_MAX_IMAGES_PER_PAGE",
    "max_mb_per_page": "SCREENSHOT_MAX_MB_PER_PAGE",
    "max_mb_per_site": "SCREENSHOT_MAX_MB_PER_SITE",
    "max_mb_per_run": "SCREENSHOT_MAX_MB_PER_RUN",
}
DEFAULT_SCREENSHOT_RETENTION = {
    "mode": "standard",
    "keep_passed": False,
    "keep_content_changed": False,
    "keep_warning": True,
    "keep_failed": True,
    "keep_initialized": True,
    "keep_context_on_failure": {
        "global": True,
        "first_screen": True,
    },
    "generate_diff_for_passed": False,
    "generate_diff_for_content_changed": False,
    "generate_diff_for_warning": True,
    "generate_diff_for_failed": True,
    "limits": {
        "max_images_per_page": 12,
        "max_mb_per_page": 50,
        "max_mb_per_site": 200,
        "max_mb_per_run": 1000,
    },
    "cleanup": {
        "remove_temp_on_success": True,
        "remove_temp_on_failure": True,
    },
}


@dataclass(frozen=True)
class RetentionDecision:
    keep_current: bool
    keep_diff: bool
    reason: str
    priority: int


def _deep_merge(base, override):
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def screenshot_retention_config(site_config=None, page_config=None):
    settings = load_settings()
    config = _deep_merge(
        DEFAULT_SCREENSHOT_RETENTION,
        (settings.get("artifacts") or {}).get("screenshot_retention", {}),
    )
    config = _deep_merge(
        config,
        ((site_config or {}).get("artifacts") or {}).get(
            "screenshot_retention",
            {},
        ),
    )
    config = _deep_merge(
        config,
        ((page_config or {}).get("artifacts") or {}).get(
            "screenshot_retention",
            {},
        ),
    )

    mode = os.environ.get("SCREENSHOT_RETENTION_MODE", config.get("mode"))
    mode = str(mode or "standard").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError(
            "SCREENSHOT_RETENTION_MODE must be one of "
            f"{', '.join(VALID_MODES)}, got {mode!r}"
        )
    config["mode"] = mode

    limits = config.setdefault("limits", {})
    for key, env_name in ENV_OVERRIDES.items():
        value = os.environ.get(env_name)
        if value is not None:
            limits[key] = _positive_number(value, env_name)
        else:
            limits[key] = _positive_number(
                limits.get(
                    key,
                    DEFAULT_SCREENSHOT_RETENTION["limits"][key],
                ),
                key,
            )
    limits["max_images_per_page"] = int(limits["max_images_per_page"])
    return config


def _positive_number(value, label):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a positive number") from None
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive number")
    return parsed


def retention_decision(config, status, artifact_type, case):
    mode = config["mode"]
    normalized = str(status or "not_run").strip().lower()
    is_global = artifact_type == "global" or case == "global"
    is_first = artifact_type == "first_screen" or case == "first_screen"

    if normalized == "terminal_page":
        return RetentionDecision(True, False, "terminal_page_evidence", 1)
    if normalized == "baseline_missing":
        return RetentionDecision(True, False, "baseline_missing_evidence", 6)
    if normalized == "capture_failed":
        return RetentionDecision(False, False, "capture_failed", 4)

    if mode == "debug":
        priority = {
            "terminal_page": 1,
            "failed": 4,
            "warning": 5,
            "baseline_missing": 6,
            "content_changed": 7,
        }.get(normalized, 8)
        return RetentionDecision(
            True,
            True,
            "debug_retention",
            priority,
        )

    if normalized in ("passed", "pass"):
        keep = bool(config.get("keep_passed", False))
        if mode == "standard" and (is_global or is_first):
            keep = True
        return RetentionDecision(
            keep,
            keep and bool(config.get("generate_diff_for_passed", False)),
            "standard_context" if keep else "passed_cleanup",
            9,
        )

    if normalized == "content_changed":
        keep = bool(config.get("keep_content_changed", False))
        if mode == "standard":
            keep = bool(config.get("keep_content_changed", False))
        return RetentionDecision(
            keep,
            keep
            and bool(
                config.get("generate_diff_for_content_changed", False)
            ),
            (
                "content_change_evidence"
                if keep
                else "content_change_recorded"
            ),
            7,
        )

    if normalized in ("initialized", "baseline_initialized"):
        keep = bool(config.get("keep_initialized", True))
        return RetentionDecision(
            keep,
            False,
            "baseline_initialized",
            6,
        )

    if normalized == "warning":
        keep = bool(config.get("keep_warning", True))
        return RetentionDecision(
            keep,
            keep and bool(config.get("generate_diff_for_warning", True)),
            "visual_warning",
            5,
        )

    if normalized == "failed":
        keep = bool(config.get("keep_failed", True))
        priority = 2 if is_global else 3 if is_first else 4
        return RetentionDecision(
            keep,
            keep and bool(config.get("generate_diff_for_failed", True)),
            "visual_failure",
            priority,
        )

    return RetentionDecision(False, False, "not_run", 9)
