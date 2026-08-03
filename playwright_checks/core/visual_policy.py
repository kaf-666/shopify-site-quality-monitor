import copy

from playwright_checks.core.config_loader import load_settings
from playwright_checks.core.viewport import get_current_viewport_name


VALID_SCREENSHOT_PURPOSES = (
    "gate",
    "report_only",
    "evidence_only",
    "structure_only",
)


DEFAULT_SCREENSHOT_POLICY = {
    "default_purpose": "gate",
    "pages": {},
}


def _deep_merge(base, override):
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def screenshot_policy_config(site_config=None, page_config=None):
    settings = load_settings()
    config = _deep_merge(
        DEFAULT_SCREENSHOT_POLICY,
        (settings.get("visual") or {}).get("screenshot_policy", {}),
    )
    config = _deep_merge(
        config,
        ((site_config or {}).get("visual") or {}).get(
            "screenshot_policy",
            {},
        ),
    )
    config = _deep_merge(
        config,
        (page_config or {}).get("screenshot_policy", {}),
    )
    return config


def screenshot_case_policy(
    page,
    case,
    viewport=None,
    site_config=None,
    page_config=None,
):
    config = screenshot_policy_config(site_config, page_config)
    raw = ((config.get("pages") or {}).get(page) or {}).get(case, {})
    if isinstance(raw, str):
        raw = {"purpose": raw}
    elif raw is None:
        raw = {}
    elif not isinstance(raw, dict):
        raise ValueError(
            f"visual.screenshot_policy.pages.{page}.{case} must be "
            "a purpose string or mapping"
        )

    purpose = str(
        raw.get("purpose") or config.get("default_purpose") or "gate"
    ).strip().lower()
    if purpose not in VALID_SCREENSHOT_PURPOSES:
        raise ValueError(
            f"Unsupported screenshot purpose for {page}.{case}: {purpose!r}"
        )

    selected_viewport = viewport or get_current_viewport_name()
    configured_viewports = raw.get("viewports")
    if isinstance(configured_viewports, str):
        configured_viewports = [configured_viewports]
    enabled = bool(raw.get("enabled", True))
    if configured_viewports:
        enabled = enabled and selected_viewport in configured_viewports

    return {
        "purpose": purpose,
        "enabled": enabled,
        "report_case": str(raw.get("report_case") or case),
        "source_case": case,
        "viewport": selected_viewport,
    }


def case_is_captured(
    page,
    case,
    viewport=None,
    site_config=None,
    page_config=None,
):
    policy = screenshot_case_policy(
        page,
        case,
        viewport=viewport,
        site_config=site_config,
        page_config=page_config,
    )
    return policy["enabled"] and policy["purpose"] != "structure_only"


def purpose_affects_exit_code(purpose, status, strict_warnings=False):
    if purpose not in ("gate", "structure_only"):
        return False
    normalized = str(status or "").strip().lower()
    return normalized == "failed" or (
        normalized == "warning" and strict_warnings
    )
