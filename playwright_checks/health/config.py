import copy
import os

from playwright_checks.core.config_loader import load_settings
from playwright_checks.health.shadow_maturity import ShadowMaturityPolicy


DEFAULT_HEALTH_CHECK = {
    "enabled": True,
    "report": {
        "json": True,
        "html": True,
        "output_dir": "reports",
    },
    "ai": {
        "enabled": False,
        "provider": "none",
        "max_findings": 20,
        "max_evidence_per_finding": 8,
        "self_healing": {
            "suggestions_only": True,
            "approval_required": True,
            "auto_apply": False,
        },
    },
    "false_positive_control": {
        "minimum_critical_alert_evidence": "HIGH",
        "suppress_third_party_noise": True,
        "suppress_expected_change": True,
        "suppress_selector_change": True,
        "recovered_retry_status": "FLAKY",
        "blocked_requires_repeated_confirmation": True,
    },
    "interaction_policy": {
        "allowed_levels": ["SAFE"],
        "transactional_requires_explicit_opt_in": True,
        "high_risk_allowed": False,
        "capability_overrides": {},
    },
    "shadow_executor": {
        "enabled": False,
        "timeout_ms": 10000,
        "max_retries": 0,
        "retry_delay_ms": 0,
        "retry_on_timeout": True,
        "retry_on_error": False,
        "history": {
            "enabled": True,
            "root": "history",
        },
        "maturity": ShadowMaturityPolicy().to_dict(),
    },
}


def _deep_merge(base, override):
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def get_health_check_config(site_config=None):
    settings = load_settings()
    config = _deep_merge(
        DEFAULT_HEALTH_CHECK,
        settings.get("health_check", {}),
    )
    config = _deep_merge(
        config,
        (site_config or {}).get("health_check", {}),
    )
    enabled = _optional_env_bool("HEALTH_CHECK_ENABLED")
    if enabled is not None:
        config["enabled"] = enabled
    ai_enabled = _optional_env_bool("HEALTH_AI_ENABLED")
    if ai_enabled is not None:
        config.setdefault("ai", {})["enabled"] = ai_enabled
    return config


def _optional_env_bool(name):
    value = os.environ.get(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return None
