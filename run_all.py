import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SITE_CONFIG_ENV = "VISUAL_SITE_CONFIG"
VIEWPORT_ENV = "VISUAL_VIEWPORT"
PAGE_ENV = "VISUAL_PAGE"
RUN_ID_ENV = "VISUAL_RUN_ID"
SCHEDULER_ENV = "HEALTH_SCHEDULER"
TRIGGER_ENV = "HEALTH_TRIGGER"
RUNTIME_MODE_ENV = "HEALTH_RUNTIME_MODE"
SHADOW_EXECUTOR_ENV = "HEALTH_SHADOW_EXECUTOR_ENABLED"
ALL_VALUE = "all"
VIEWPORT_CHOICES = ("desktop", "mobile", ALL_VALUE)
PAGE_CHOICES = ("home", "collection", "product", ALL_VALUE)
REQUIRED_PAGES = ("home", "collection", "product")


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "configs"
SITE_CONFIG_DIR = CONFIG_DIR / "sites"
BASELINE_ROOT = PROJECT_ROOT / "baselines"


def site_config_choices():
    if not SITE_CONFIG_DIR.exists():
        return []

    names = {
        path.stem
        for pattern in ("*.yaml", "*.yml")
        for path in SITE_CONFIG_DIR.glob(pattern)
    }
    return sorted(names)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run Playwright visual regression checks."
    )
    site_kwargs = {
        "help": "Site config name under configs/sites, for example mondressy_US."
    }
    choices = site_config_choices()
    if choices:
        site_kwargs["choices"] = choices

    parser.add_argument("--site", **site_kwargs)
    parser.add_argument(
        "--viewport",
        choices=VIEWPORT_CHOICES,
        help="Viewport to run. Use all to run the configured default viewport list.",
    )
    parser.add_argument(
        "--page",
        choices=PAGE_CHOICES,
        help="Page suite to run. Use all to run home, collection, and product.",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate local config and baseline directories without opening a browser.",
    )
    parser.add_argument(
        "--run-id",
        help="Stable run ID used by all artifacts and scheduler metadata.",
    )
    parser.add_argument(
        "--scheduler",
        choices=("MANUAL", "CODEX", "HERMES", "JENKINS", "OTHER"),
        type=str.upper,
        help="Scheduler metadata only; it never changes health-check behavior.",
    )
    parser.add_argument(
        "--trigger",
        choices=("MANUAL", "SCHEDULED", "OTHER"),
        type=str.upper,
        help="Trigger metadata recorded in run-manifest.json.",
    )
    parser.add_argument(
        "--mode",
        choices=("MONITOR", "DIAGNOSE", "DISCOVER"),
        type=str.upper,
        default="MONITOR",
        help="Runtime mode contract. Only MONITOR is implemented in Phase 3.",
    )
    shadow = parser.add_mutually_exclusive_group()
    shadow.add_argument(
        "--shadow-executor",
        dest="shadow_executor",
        action="store_true",
        help="Run the new executor pipeline as a non-gating sidecar.",
    )
    shadow.add_argument(
        "--no-shadow-executor",
        dest="shadow_executor",
        action="store_false",
        help="Disable the shadow executor sidecar.",
    )
    parser.set_defaults(shadow_executor=None)
    return parser.parse_args(argv)


def apply_cli_args(args):
    if args.site:
        os.environ[SITE_CONFIG_ENV] = args.site

    if args.viewport:
        if args.viewport == ALL_VALUE:
            os.environ.pop(VIEWPORT_ENV, None)
        else:
            os.environ[VIEWPORT_ENV] = args.viewport

    if args.page and args.page != ALL_VALUE:
        os.environ[PAGE_ENV] = args.page
    else:
        os.environ.pop(PAGE_ENV, None)

    if args.run_id:
        os.environ[RUN_ID_ENV] = args.run_id
    if args.scheduler:
        os.environ[SCHEDULER_ENV] = args.scheduler
    if args.trigger:
        os.environ[TRIGGER_ENV] = args.trigger
    os.environ[RUNTIME_MODE_ENV] = args.mode
    if args.shadow_executor is not None:
        os.environ[SHADOW_EXECUTOR_ENV] = (
            "true" if args.shadow_executor else "false"
        )

    if args.mode == "MONITOR":
        os.environ["ALLOW_BASELINE_INIT"] = "false"
        os.environ["FORCE_BASELINE_INIT"] = "false"
        os.environ["ALLOW_SIDE_EFFECT_FLOW"] = "false"


def site_config_path(site_name):
    if not site_name:
        return None
    return SITE_CONFIG_DIR / f"{site_name}.yaml"


def is_valid_url(value):
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def is_selector(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(part, str) and part.strip() for part in value)
    )


def validate_selector(errors, path, value):
    if not is_selector(value):
        errors.append(f"{path} must be a non-empty [method, selector] pair")


def validate_module_selectors(errors, page_name, page_config):
    modules = page_config.get("modules")
    if not isinstance(modules, dict) or not modules:
        errors.append(f"pages.{page_name}.modules must be a non-empty mapping")
        return

    for module_name, selector in modules.items():
        validate_selector(errors, f"pages.{page_name}.modules.{module_name}", selector)


def validate_dynamic_regions(errors, page_name, page_config):
    regions = page_config.get("dynamic_regions")
    if regions is None:
        return
    if not isinstance(regions, list):
        errors.append(f"pages.{page_name}.dynamic_regions must be a list")
        return
    modules = page_config.get("modules", {})
    seen = set()
    for index, region in enumerate(regions):
        path = f"pages.{page_name}.dynamic_regions[{index}]"
        if not isinstance(region, dict):
            errors.append(f"{path} must be a mapping")
            continue
        name = region.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{path}.name must be a non-empty string")
        elif name in seen:
            errors.append(f"{path}.name must be unique")
        else:
            seen.add(name)
        strategy = region.get("strategy")
        if strategy not in (
            "mask_content",
            "layout_only",
            "ignore_visual",
        ):
            errors.append(
                f"{path}.strategy must be mask_content, layout_only, "
                "or ignore_visual"
            )
        region_type = region.get("region_type")
        if region_type is not None and region_type not in (
            "grid",
            "product_grid",
            "carousel",
            "category_carousel",
            "product_carousel",
            "content",
            "product_media",
            "product_information",
        ):
            errors.append(
                f"{path}.region_type must be grid, product_grid, "
                "carousel, category_carousel, product_carousel, content, "
                "product_media, or product_information"
            )
        module = region.get("module")
        selector = region.get("selector")
        if module is not None and module not in modules:
            errors.append(f"{path}.module references unknown module")
        if module is None and not (
            isinstance(selector, str) and selector.strip()
        ):
            errors.append(f"{path} requires module or CSS selector")
        if "item_selector" in region and not (
            isinstance(region["item_selector"], str)
            and region["item_selector"].strip()
        ):
            errors.append(f"{path}.item_selector must be CSS text")
        masks = region.get("masks")
        if masks is not None and (
            not isinstance(masks, list)
            or not all(
                isinstance(item, str) and item.strip()
                for item in masks
            )
        ):
            errors.append(f"{path}.masks must be CSS text values")


def validate_readonly_interactions(errors, page_name, page_config):
    interactions = page_config.get("readonly_interactions")
    if interactions is None:
        return
    if not isinstance(interactions, dict):
        errors.append(
            f"pages.{page_name}.readonly_interactions must be a mapping"
        )
        return
    for name, config in interactions.items():
        path = f"pages.{page_name}.readonly_interactions.{name}"
        if not isinstance(config, dict):
            errors.append(f"{path} must be a mapping")
            continue
        for key in ("trigger", "panel", "close"):
            validate_selector(errors, f"{path}.{key}", config.get(key))
        if config.get("bottom_action") is not None:
            validate_selector(
                errors,
                f"{path}.bottom_action",
                config.get("bottom_action"),
            )
        if config.get("dismiss_obstruction") is not None:
            validate_selector(
                errors,
                f"{path}.dismiss_obstruction",
                config.get("dismiss_obstruction"),
            )


def validate_screenshot_policy(errors, settings):
    from playwright_checks.core.visual_policy import VALID_SCREENSHOT_PURPOSES

    policy = ((settings.get("visual") or {}).get("screenshot_policy"))
    if policy is None:
        return
    if not isinstance(policy, dict):
        errors.append("visual.screenshot_policy must be a mapping")
        return
    default = policy.get("default_purpose", "gate")
    if default not in VALID_SCREENSHOT_PURPOSES:
        errors.append(
            "visual.screenshot_policy.default_purpose must be one of "
            + ", ".join(VALID_SCREENSHOT_PURPOSES)
        )
    pages = policy.get("pages", {})
    if not isinstance(pages, dict):
        errors.append("visual.screenshot_policy.pages must be a mapping")
        return
    for page_name, cases in pages.items():
        if not isinstance(cases, dict):
            errors.append(
                f"visual.screenshot_policy.pages.{page_name} must be a mapping"
            )
            continue
        for case_name, raw in cases.items():
            path = f"visual.screenshot_policy.pages.{page_name}.{case_name}"
            purpose = raw if isinstance(raw, str) else (
                raw.get("purpose") if isinstance(raw, dict) else None
            )
            if purpose is not None and purpose not in VALID_SCREENSHOT_PURPOSES:
                errors.append(
                    f"{path}.purpose must be one of "
                    + ", ".join(VALID_SCREENSHOT_PURPOSES)
                )
            if isinstance(raw, dict) and raw.get("viewports") is not None:
                viewports = raw.get("viewports")
                if not isinstance(viewports, list) or not all(
                    isinstance(value, str) and value.strip()
                    for value in viewports
                ):
                    errors.append(f"{path}.viewports must be a string list")


def validate_size_tolerance(errors, page_name, page_config):
    configured = page_config.get("size_tolerance")
    if configured is None:
        return
    if not isinstance(configured, dict):
        errors.append(
            f"pages.{page_name}.size_tolerance must be a mapping"
        )
        return
    direct = any(
        key in configured
        for key in ("width_px", "height_px", "ratio")
    )
    cases = {"page": configured} if direct else configured
    for case, tolerance in cases.items():
        path = f"pages.{page_name}.size_tolerance.{case}"
        if not isinstance(tolerance, dict):
            errors.append(f"{path} must be a mapping")
            continue
        for key in tolerance:
            if key not in ("width_px", "height_px", "ratio"):
                errors.append(f"{path}.{key} is not recognized")
        for key in ("width_px", "height_px"):
            value = tolerance.get(key)
            if value is None:
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
                or value > 20
            ):
                errors.append(
                    f"{path}.{key} must be between 0 and 20"
                )
        ratio = tolerance.get("ratio")
        if (
            ratio is not None
            and (
                isinstance(ratio, bool)
                or not isinstance(ratio, (int, float))
                or ratio < 0
                or ratio > 0.1
            )
        ):
            errors.append(f"{path}.ratio must be between 0 and 0.1")


def validate_artifact_settings(errors):
    from playwright_checks.artifacts.retention import (
        screenshot_retention_config,
    )

    try:
        screenshot_retention_config()
    except (TypeError, ValueError) as error:
        errors.append(f"artifacts.screenshot_retention: {error}")


def validate_runtime_health(errors, path, value, warnings=None):
    warnings = warnings if warnings is not None else []
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{path} must be a mapping")
        return

    known_keys = {
        "enabled",
        "reporting",
        "retry_policy",
        "max_events_per_category",
        "http_error_status",
        "blank_page_text_threshold",
        "blank_page_node_threshold",
        "loading_confirmation_ms",
        "loading_selectors",
        "loading_critical_selectors",
        "critical_selectors",
        "optional_selectors",
        "first_party_patterns",
        "third_party_patterns",
        "network_noise",
        "legitimate_empty_patterns",
        "error_page_patterns",
    }
    for key in value:
        if key not in known_keys:
            warnings.append(f"{path}.{key} is not recognized and will be ignored")

    boolean_keys = ("enabled",)
    integer_keys = (
        "max_events_per_category",
        "http_error_status",
        "blank_page_text_threshold",
        "blank_page_node_threshold",
    )
    list_keys = (
        "loading_selectors",
        "loading_critical_selectors",
        "first_party_patterns",
        "third_party_patterns",
        "legitimate_empty_patterns",
    )

    for key in boolean_keys:
        if key in value and not isinstance(value[key], bool):
            errors.append(f"{path}.{key} must be a boolean")
    for key in integer_keys:
        if key in value and (
            not isinstance(value[key], int)
            or isinstance(value[key], bool)
            or value[key] < 0
        ):
            errors.append(f"{path}.{key} must be a non-negative integer")
    for key in list_keys:
        if key not in value:
            continue
        items = value[key]
        if not isinstance(items, list) or not all(
            isinstance(item, str) and item.strip()
            for item in items
        ):
            errors.append(
                f"{path}.{key} must be a list of non-empty strings"
            )

    if "loading_confirmation_ms" in value:
        confirmation = value["loading_confirmation_ms"]
        if (
            not isinstance(confirmation, (int, float))
            or isinstance(confirmation, bool)
            or confirmation < 0
        ):
            errors.append(
                f"{path}.loading_confirmation_ms must be a non-negative number"
            )

    reporting = value.get("reporting")
    if reporting is not None:
        if not isinstance(reporting, dict):
            errors.append(f"{path}.reporting must be a mapping")
        else:
            reporting_keys = {
                "report_only",
                "affect_exit_code",
                "fail_on_failed",
                "fail_on_warning",
            }
            for key, item in reporting.items():
                if key not in reporting_keys:
                    warnings.append(
                        f"{path}.reporting.{key} is not recognized and will be ignored"
                    )
                elif not isinstance(item, bool):
                    errors.append(
                        f"{path}.reporting.{key} must be a boolean"
                    )

    retry_policy = value.get("retry_policy")
    if retry_policy is not None:
        if not isinstance(retry_policy, dict):
            errors.append(f"{path}.retry_policy must be a mapping")
        else:
            for key in retry_policy:
                if key != "recovered_status":
                    warnings.append(
                        f"{path}.retry_policy.{key} is not recognized and will be ignored"
                    )
            recovered_status = retry_policy.get("recovered_status")
            if recovered_status is not None and recovered_status not in (
                "passed",
                "warning",
                "failed",
            ):
                errors.append(
                    f"{path}.retry_policy.recovered_status must be one of "
                    "passed, warning, failed"
                )

    network_noise = value.get("network_noise")
    if network_noise is not None:
        allowed_noise_keys = {
            "ignore_third_party_aborted",
            "ignore_image_aborted",
            "ignore_favicon",
        }
        if not isinstance(network_noise, dict):
            errors.append(f"{path}.network_noise must be a mapping")
        else:
            for key, item in network_noise.items():
                if key not in allowed_noise_keys:
                    warnings.append(
                        f"{path}.network_noise.{key} is not recognized and will be ignored"
                    )
                elif not isinstance(item, bool):
                    errors.append(
                        f"{path}.network_noise.{key} must be a boolean"
                    )

    for selector_key in ("critical_selectors", "optional_selectors"):
        if selector_key not in value:
            continue
        selectors = value[selector_key]
        if not isinstance(selectors, list):
            errors.append(f"{path}.{selector_key} must be a list")
            continue
        for index, item in enumerate(selectors):
            item_path = f"{path}.{selector_key}[{index}]"
            if isinstance(item, str):
                if not item.strip():
                    errors.append(f"{item_path} must not be empty")
                continue
            if not isinstance(item, dict):
                errors.append(f"{item_path} must be a string or mapping")
                continue
            name = item.get("name")
            selector = item.get("selector")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{item_path}.name must be a non-empty string")
            if not (
                isinstance(selector, str)
                and selector.strip()
                or is_selector(selector)
            ):
                errors.append(
                    f"{item_path}.selector must be CSS text or a "
                    "non-empty [method, selector] pair"
                )
            if "visible" in item and not isinstance(item["visible"], bool):
                errors.append(f"{item_path}.visible must be a boolean")
            allow_patterns = item.get("allow_text_patterns")
            if allow_patterns is not None and (
                not isinstance(allow_patterns, list)
                or not all(
                    isinstance(pattern, str) and pattern.strip()
                    for pattern in allow_patterns
                )
            ):
                errors.append(
                    f"{item_path}.allow_text_patterns must be a list "
                    "of non-empty strings"
                )

    patterns = value.get("error_page_patterns")
    if patterns is not None:
        if not isinstance(patterns, dict):
            errors.append(f"{path}.error_page_patterns must be a mapping")
        else:
            for reason, items in patterns.items():
                if not isinstance(items, list) or not all(
                    isinstance(item, str) and item.strip()
                    for item in items
                ):
                    errors.append(
                        f"{path}.error_page_patterns.{reason} must be "
                        "a list of non-empty strings"
                    )


def validate_health_check(errors, path, value, warnings=None):
    warnings = warnings if warnings is not None else []
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append(f"{path} must be a mapping")
        return

    known_keys = {
        "enabled",
        "report",
        "ai",
        "false_positive_control",
        "interaction_policy",
        "shadow_executor",
    }
    for key in value:
        if key not in known_keys:
            warnings.append(
                f"{path}.{key} is not recognized and will be ignored"
            )

    if "enabled" in value and not isinstance(value["enabled"], bool):
        errors.append(f"{path}.enabled must be a boolean")

    report = value.get("report")
    if report is not None:
        if not isinstance(report, dict):
            errors.append(f"{path}.report must be a mapping")
        else:
            for key, item in report.items():
                if key not in {"json", "html", "output_dir"}:
                    warnings.append(
                        f"{path}.report.{key} is not recognized and will be ignored"
                    )
                elif key in {"json", "html"} and not isinstance(item, bool):
                    errors.append(f"{path}.report.{key} must be a boolean")
                elif key == "output_dir" and (
                    not isinstance(item, str) or not item.strip()
                ):
                    errors.append(
                        f"{path}.report.output_dir must be a non-empty string"
                    )

    ai = value.get("ai")
    if ai is not None:
        if not isinstance(ai, dict):
            errors.append(f"{path}.ai must be a mapping")
        else:
            for key, item in ai.items():
                if key not in {
                    "enabled",
                    "provider",
                    "max_findings",
                    "max_evidence_per_finding",
                    "self_healing",
                }:
                    warnings.append(
                        f"{path}.ai.{key} is not recognized and will be ignored"
                    )
                elif key == "enabled" and not isinstance(item, bool):
                    errors.append(f"{path}.ai.enabled must be a boolean")
                elif key == "provider" and (
                    not isinstance(item, str) or not item.strip()
                ):
                    errors.append(f"{path}.ai.provider must be a non-empty string")
                elif key in {"max_findings", "max_evidence_per_finding"} and (
                    not isinstance(item, int)
                    or isinstance(item, bool)
                    or item <= 0
                ):
                    errors.append(f"{path}.ai.{key} must be a positive integer")

            self_healing = ai.get("self_healing")
            if self_healing is not None:
                if not isinstance(self_healing, dict):
                    errors.append(f"{path}.ai.self_healing must be a mapping")
                else:
                    for key, item in self_healing.items():
                        if key not in {
                            "suggestions_only",
                            "approval_required",
                            "auto_apply",
                        }:
                            warnings.append(
                                f"{path}.ai.self_healing.{key} is not recognized "
                                "and will be ignored"
                            )
                        elif not isinstance(item, bool):
                            errors.append(
                                f"{path}.ai.self_healing.{key} must be a boolean"
                            )
                    if self_healing.get("suggestions_only") is False:
                        errors.append(
                            f"{path}.ai.self_healing.suggestions_only must remain true"
                        )
                    if self_healing.get("approval_required") is False:
                        errors.append(
                            f"{path}.ai.self_healing.approval_required must remain true"
                        )
                    if self_healing.get("auto_apply") is True:
                        errors.append(
                            f"{path}.ai.self_healing.auto_apply must remain false"
                        )

    false_positive = value.get("false_positive_control")
    if false_positive is not None:
        if not isinstance(false_positive, dict):
            errors.append(f"{path}.false_positive_control must be a mapping")
        else:
            allowed_keys = {
                "minimum_critical_alert_evidence",
                "suppress_third_party_noise",
                "suppress_expected_change",
                "suppress_selector_change",
                "recovered_retry_status",
                "blocked_requires_repeated_confirmation",
            }
            for key, item in false_positive.items():
                item_path = f"{path}.false_positive_control.{key}"
                if key not in allowed_keys:
                    warnings.append(
                        f"{item_path} is not recognized and will be ignored"
                    )
                elif key == "minimum_critical_alert_evidence" and item not in {
                    "NONE",
                    "LOW",
                    "MEDIUM",
                    "HIGH",
                }:
                    errors.append(
                        f"{item_path} must be one of NONE, LOW, MEDIUM, HIGH"
                    )
                elif key == "recovered_retry_status" and item not in {
                    "PASS",
                    "WARN",
                    "FLAKY",
                }:
                    errors.append(f"{item_path} must be one of PASS, WARN, FLAKY")
                elif (
                    key.startswith("suppress_")
                    or key == "blocked_requires_repeated_confirmation"
                ):
                    if not isinstance(item, bool):
                        errors.append(f"{item_path} must be a boolean")

    interaction = value.get("interaction_policy")
    if interaction is not None:
        if not isinstance(interaction, dict):
            errors.append(f"{path}.interaction_policy must be a mapping")
        else:
            allowed_keys = {
                "allowed_levels",
                "transactional_requires_explicit_opt_in",
                "high_risk_allowed",
                "capability_overrides",
            }
            for key, item in interaction.items():
                item_path = f"{path}.interaction_policy.{key}"
                if key not in allowed_keys:
                    warnings.append(
                        f"{item_path} is not recognized and will be ignored"
                    )
                elif key == "allowed_levels" and (
                    not isinstance(item, list)
                    or not item
                    or any(
                        level not in {"SAFE", "TRANSACTIONAL_SAFE", "HIGH_RISK"}
                        for level in item
                    )
                ):
                    errors.append(
                        f"{item_path} must be a non-empty list of valid risk levels"
                    )
                elif key == "capability_overrides":
                    if not isinstance(item, dict):
                        errors.append(f"{item_path} must be a mapping")
                    else:
                        from playwright_checks.health.capabilities import (
                            CAPABILITY_RISKS,
                        )
                        from playwright_checks.health.models import (
                            SideEffectLevel,
                        )
                        protected = {
                            name
                            for name, risk in CAPABILITY_RISKS.items()
                            if risk == SideEffectLevel.HIGH_RISK
                        }
                        for capability, level in item.items():
                            override_path = f"{item_path}.{capability}"
                            if (
                                not isinstance(capability, str)
                                or not capability.strip()
                            ):
                                errors.append(
                                    f"{item_path} keys must be non-empty strings"
                                )
                            elif level not in {
                                "SAFE",
                                "TRANSACTIONAL_SAFE",
                                "HIGH_RISK",
                            }:
                                errors.append(
                                    f"{override_path} must be a valid risk level"
                                )
                            elif capability.strip().lower() in protected and (
                                level != "HIGH_RISK"
                            ):
                                errors.append(
                                    f"{override_path} cannot downgrade a protected "
                                    "high-risk action"
                                )
                elif key != "allowed_levels" and not isinstance(item, bool):
                    errors.append(f"{item_path} must be a boolean")

    shadow_executor = value.get("shadow_executor")
    if shadow_executor is not None:
        shadow_path = f"{path}.shadow_executor"
        if not isinstance(shadow_executor, dict):
            errors.append(f"{shadow_path} must be a mapping")
        else:
            allowed_keys = {
                "enabled",
                "timeout_ms",
                "max_retries",
                "retry_delay_ms",
                "retry_on_timeout",
                "retry_on_error",
                "history",
                "maturity",
            }
            for key, item in shadow_executor.items():
                item_path = f"{shadow_path}.{key}"
                if key not in allowed_keys:
                    warnings.append(
                        f"{item_path} is not recognized and will be ignored"
                    )
                elif key in {
                    "enabled",
                    "retry_on_timeout",
                    "retry_on_error",
                } and not isinstance(item, bool):
                    errors.append(f"{item_path} must be a boolean")
                elif key == "timeout_ms" and (
                    not isinstance(item, int)
                    or isinstance(item, bool)
                    or item <= 0
                ):
                    errors.append(f"{item_path} must be a positive integer")
                elif key in {"max_retries", "retry_delay_ms"} and (
                    not isinstance(item, int)
                    or isinstance(item, bool)
                    or item < 0
                ):
                    errors.append(
                        f"{item_path} must be a non-negative integer"
                    )
            history = shadow_executor.get("history")
            if history is not None:
                history_path = f"{shadow_path}.history"
                if not isinstance(history, dict):
                    errors.append(f"{history_path} must be a mapping")
                else:
                    for key, item in history.items():
                        item_path = f"{history_path}.{key}"
                        if key not in {"enabled", "root"}:
                            warnings.append(
                                f"{item_path} is not recognized and will be ignored"
                            )
                        elif key == "enabled" and not isinstance(item, bool):
                            errors.append(f"{item_path} must be a boolean")
                        elif key == "root" and (
                            not isinstance(item, str) or not item.strip()
                        ):
                            errors.append(
                                f"{item_path} must be a non-empty string"
                            )
            maturity = shadow_executor.get("maturity")
            if maturity is not None:
                maturity_path = f"{shadow_path}.maturity"
                allowed_maturity = {
                    "overall_coverage_percent",
                    "critical_coverage_percent",
                    "executable_coverage_percent",
                    "result_parity_percent",
                    "evidence_parity_percent",
                    "max_policy_regressions",
                    "max_executor_errors",
                    "max_executor_timeouts",
                    "required_consecutive_stable_runs",
                    "require_result_parity_sample",
                    "require_evidence_parity_sample",
                }
                if not isinstance(maturity, dict):
                    errors.append(f"{maturity_path} must be a mapping")
                else:
                    for key in maturity:
                        if key not in allowed_maturity:
                            warnings.append(
                                f"{maturity_path}.{key} is not recognized and will be ignored"
                            )
                    try:
                        from playwright_checks.health.shadow_maturity import (
                            ShadowMaturityPolicy,
                        )
                        ShadowMaturityPolicy.from_config(
                            {"shadow_executor": shadow_executor}
                        )
                    except (TypeError, ValueError) as error:
                        errors.append(f"{maturity_path}: {error}")


def validate_settings(errors, warnings):
    from playwright_checks.core.config_loader import load_settings

    settings = load_settings()
    validate_artifact_settings(errors)
    validate_screenshot_policy(errors, settings)
    validate_runtime_health(
        errors,
        "configs/settings.yaml runtime_health",
        settings.get("runtime_health"),
        warnings,
    )
    validate_health_check(
        errors,
        "configs/settings.yaml health_check",
        settings.get("health_check"),
        warnings,
    )
    viewports = settings.get("viewports")
    run_viewports = settings.get("run_viewports")

    if not isinstance(viewports, dict) or not viewports:
        errors.append("configs/settings.yaml viewports must be a non-empty mapping")
        return settings, []

    if not isinstance(run_viewports, list) or not run_viewports:
        errors.append("configs/settings.yaml run_viewports must be a non-empty list")
        return settings, []

    valid_run_viewports = []
    for viewport in run_viewports:
        if not isinstance(viewport, str) or not viewport.strip():
            errors.append("configs/settings.yaml run_viewports contains an empty value")
            continue
        if viewport not in viewports:
            errors.append(
                f"configs/settings.yaml run_viewports contains unknown viewport: {viewport}"
            )
            continue
        valid_run_viewports.append(viewport)

    return settings, valid_run_viewports


def selected_site_name(args, settings):
    return (
        args.site
        or os.environ.get(SITE_CONFIG_ENV)
        or settings.get("default_site")
        or "mondressy_US"
    )


def baseline_viewports(args, settings_run_viewports):
    if args.viewport and args.viewport != ALL_VALUE:
        return [args.viewport]
    return settings_run_viewports


def validate_config(args):
    from playwright_checks.core.config_loader import get_page_config, load_site_config
    from playwright_checks.health.profile_artifacts import build_profile_bundle

    errors = []
    warnings = []

    settings, run_viewports = validate_settings(errors, warnings)
    site_name = selected_site_name(args, settings)
    path = site_config_path(site_name)

    print("Config validation")
    print(f"site: {site_name}")
    if run_viewports:
        print(f"run_viewports: {', '.join(run_viewports)}")

    if not path or not path.exists():
        errors.append(f"site yaml not found: {path}")
        return finish_validation(errors, warnings)

    print(f"site yaml: {path.relative_to(PROJECT_ROOT)}")

    try:
        site_config = load_site_config(site_name)
    except Exception as exc:
        errors.append(f"failed to load site yaml: {type(exc).__name__}: {exc}")
        return finish_validation(errors, warnings)

    validate_runtime_health(
        errors,
        "runtime_health",
        site_config.get("runtime_health"),
        warnings,
    )
    validate_health_check(
        errors,
        "health_check",
        site_config.get("health_check"),
        warnings,
    )

    pages = site_config.get("pages")
    if not isinstance(pages, dict):
        errors.append("pages must be a mapping")
        return finish_validation(errors, warnings)

    base_url = site_config.get("base_url")
    base_url_source = "base_url"
    if not base_url:
        base_url = pages.get("home", {}).get("url") if isinstance(pages.get("home"), dict) else None
        base_url_source = "pages.home.url"

    if not is_valid_url(base_url):
        errors.append("base_url is missing or invalid; expected top-level base_url or valid pages.home.url")
    else:
        print(f"base_url: {base_url} ({base_url_source})")

    for page_name in REQUIRED_PAGES:
        if page_name not in pages:
            errors.append(f"pages.{page_name} is missing")
            continue

        try:
            page_config = get_page_config(page_name, site_config)
        except Exception as exc:
            errors.append(
                f"pages.{page_name} failed to load: {type(exc).__name__}: {exc}"
            )
            continue

        if not is_valid_url(page_config.get("url")):
            errors.append(f"pages.{page_name}.url is missing or invalid")
        validate_module_selectors(errors, page_name, page_config)
        validate_dynamic_regions(errors, page_name, page_config)
        validate_readonly_interactions(errors, page_name, page_config)
        validate_size_tolerance(errors, page_name, page_config)
        raw_page_config = pages[page_name]
        validate_runtime_health(
            errors,
            f"pages.{page_name}.runtime_health",
            raw_page_config.get("runtime_health"),
            warnings,
        )
        for viewport_name in ("desktop", "mobile"):
            viewport_config = raw_page_config.get(viewport_name)
            if viewport_config is None:
                continue
            if not isinstance(viewport_config, dict):
                errors.append(
                    f"pages.{page_name}.{viewport_name} must be a mapping"
                )
                continue
            validate_runtime_health(
                errors,
                (
                    f"pages.{page_name}.{viewport_name}."
                    "runtime_health"
                ),
                viewport_config.get("runtime_health"),
                warnings,
            )

        if page_name == "collection":
            validate_selector(errors, "pages.collection.product_card", page_config.get("product_card"))
        if page_name == "product":
            validate_selector(errors, "pages.product.variant_inputs", page_config.get("variant_inputs"))

    try:
        profile_bundle = build_profile_bundle(site_config)
        print(
            "site profile: "
            f"{profile_bundle.profile.site_identity.site_type.value}, "
            f"pages={len(profile_bundle.profile.pages)}, "
            f"planned_checks={len(profile_bundle.plan.checks)}"
        )
    except (TypeError, ValueError) as exc:
        errors.append(
            "site profile / capability registry validation failed: "
            f"{type(exc).__name__}: {exc}"
        )

    if not errors:
        print("pages: home, collection, product")
        print("selectors: OK")

    baseline_warnings_before = len(warnings)
    for viewport in baseline_viewports(args, run_viewports):
        for page_name in REQUIRED_PAGES:
            baseline_dir = BASELINE_ROOT / site_name / viewport / page_name
            if not baseline_dir.exists():
                warnings.append(
                    "baseline directory missing: "
                    f"{baseline_dir.relative_to(PROJECT_ROOT)}"
                )
            elif not any(baseline_dir.glob("*.png")):
                warnings.append(
                    "baseline directory has no png files: "
                    f"{baseline_dir.relative_to(PROJECT_ROOT)}"
                )

    if len(warnings) == baseline_warnings_before:
        print("baseline directories: OK")

    return finish_validation(errors, warnings)


def finish_validation(errors, warnings):
    for warning in warnings:
        print(f"WARNING: {warning}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print("Config validation FAILED")
        return 1

    print("OK config validation passed")
    if warnings:
        print(
            "Warnings are non-blocking; review configuration and baselines "
            "before CI visual runs."
        )
    return 0


def run_stage(label, folder, script):
    """Run one test stage and return whether it passed."""
    print("\n" + "=" * 60)
    print(f"{' STAGE: ' + label + ' ':=^60}")
    print("=" * 60 + "\n")
    sys.stdout.flush()

    result = subprocess.run(
        [sys.executable, "-u", "-m", script],
        cwd=folder,
        env=os.environ.copy(),
    )

    if result.returncode != 0:
        print(f"\nFAILED: {label}")
        return False

    print(f"\nPASSED: {label}")
    return True


def run_scheduler_neutral_runtime(args):
    from playwright_checks.core.config_loader import (
        load_settings,
        load_site_config,
    )
    from playwright_checks.core.paths import artifact_root, current_run_id
    from playwright_checks.health.config import get_health_check_config
    from playwright_checks.health.execution_models import RuntimeMode
    from playwright_checks.health.shadow_runtime import shadow_executor_enabled
    from playwright_checks.runtime.run_manifest import (
        RunManifestStore,
        SchedulerType,
        TriggerType,
        build_run_manifest,
    )
    from playwright_checks.runtime.run_summary import (
        build_machine_run_summary,
        stdout_contract,
        write_machine_run_summary,
    )

    settings = load_settings()
    site_name = selected_site_name(args, settings)
    site_config = load_site_config(site_name)
    run_id = current_run_id()
    run_root = artifact_root() / run_id
    mode = RuntimeMode(args.mode)
    scheduler = SchedulerType(
        str(
            args.scheduler
            or os.environ.get(SCHEDULER_ENV)
            or "MANUAL"
        ).upper()
    )
    trigger = TriggerType(
        str(
            args.trigger
            or os.environ.get(TRIGGER_ENV)
            or "MANUAL"
        ).upper()
    )
    shadow_enabled = shadow_executor_enabled(site_config)
    health_config = get_health_check_config(site_config)
    config_path = site_config_path(site_name)
    config_reference = config_path if config_path else None
    manifest = build_run_manifest(
        run_id=run_id,
        site=site_name,
        scheduler=scheduler,
        trigger=trigger,
        mode=mode,
        ai_enabled=bool((health_config.get("ai") or {}).get("enabled", False)),
        transactional_safe_enabled=False,
        shadow_executor_enabled=shadow_enabled,
        config_path=config_reference,
        runtime_metadata={
            "viewport": args.viewport or "configured",
            "page": args.page or "all",
            "artifact_root": "artifacts",
            "scheduler_behavior_invariant": True,
        },
    )
    store = RunManifestStore(run_root)
    manifest_path = store.write(manifest)
    exit_code = 2
    unsupported = mode != RuntimeMode.MONITOR

    try:
        if unsupported:
            print(
                f"Runtime mode {mode.value} is RESERVED/UNSUPPORTED in Phase 3; "
                "no browser or health executor was started."
            )
        else:
            passed = run_stage(
                "Playwright visual regression",
                str(PROJECT_ROOT),
                "playwright_checks.runner.main",
            )
            exit_code = 0 if passed else 1
            if passed:
                print("\n" + "=" * 60)
                print("All checks passed")
                print("=" * 60)
    except Exception as error:
        print(
            "Runtime orchestration failed: "
            f"{type(error).__name__}: {error}",
            file=sys.stderr,
        )
        exit_code = 2
    finally:
        manifest.finish(exit_code, unsupported=unsupported)
        manifest_path = store.write(manifest)
        health_report = run_root / "health-report.json"
        shadow_comparison = run_root / "shadow-comparison.json"
        shadow_history_summary = run_root / "shadow-history-summary.json"
        summary = build_machine_run_summary(
            run_id=run_id,
            legacy_exit_code=exit_code,
            health_report_path=(
                health_report.resolve() if health_report.is_file() else None
            ),
            manifest_path=manifest_path,
            shadow_comparison_path=(
                shadow_comparison.resolve()
                if shadow_comparison.is_file()
                else None
            ),
            shadow_history_summary_path=(
                shadow_history_summary.resolve()
                if shadow_history_summary.is_file()
                else None
            ),
            overall_status_override="UNSUPPORTED" if unsupported else None,
        )
        summary_path = write_machine_run_summary(summary, run_root)
        for line in stdout_contract(summary, summary_path):
            print(line)
    return exit_code


def main(argv=None):
    args = parse_args(argv)
    apply_cli_args(args)

    if args.validate_config:
        return validate_config(args)

    return run_scheduler_neutral_runtime(args)

if __name__ == "__main__":
    sys.exit(main())
