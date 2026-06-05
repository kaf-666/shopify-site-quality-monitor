import os
import copy
from functools import lru_cache
from pathlib import Path

try:
    import yaml
except ModuleNotFoundError as exc:
    raise RuntimeError(
        "PyYAML is required to load configs. "
        "Install dependencies with `pip install -r requirements.txt`."
    ) from exc


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "configs"
SITE_CONFIG_DIR = CONFIG_DIR / "sites"
SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
DEFAULT_SITE_CONFIG = "mondressy_US"
SITE_CONFIG_ENV = "VISUAL_SITE_CONFIG"

DEFAULT_ACCESS_DENIED_PATTERNS = [
    "cloudflare",
    "captcha",
    "access denied",
    "attention required",
    "checking your browser",
    "verify you are human",
    "Cloudflare Ray ID",
]
DEFAULT_DYNAMIC_HIDE = {
    "selectors": [
        ".price",
        ".sale-badge",
        ".spr-badge",
        "#size_error",
        ".alr-wh-rw-popup",
        ".cbb-frequently-bought-container",
        "[id*='cbb']",
        "[class*='cbb']",
        "[id*='frequently']",
        "[class*='frequently']",
        "[id*='bought']",
        "[class*='bought']",
        ".shopify-payment-button",
        ".shopify-payment-button__button",
        "[data-shopify='payment-button']",
        "shopify-payment-terms",
    ],
    "text_exact": [
        "frequently bought together",
        "more payment options",
        "buy it now",
    ],
    "text_contains": [
        "pay with paypal",
    ],
    "text_max_length": 80,
    "container_selector": "[id*='cbb'], [class*='cbb'], section, form, div",
}


@lru_cache(maxsize=1)
def load_settings():
    if not SETTINGS_FILE.exists():
        return {}

    with SETTINGS_FILE.open("r", encoding="utf-8") as config_file:
        settings = yaml.safe_load(config_file) or {}

    if not isinstance(settings, dict):
        raise ValueError(f"Settings config must be a mapping: {SETTINGS_FILE}")

    return settings


def _config_path(config_name):
    settings = load_settings()
    value = (
        config_name
        or os.environ.get(SITE_CONFIG_ENV)
        or settings.get("default_site")
        or DEFAULT_SITE_CONFIG
    )
    path = Path(value)

    if path.suffix in (".yaml", ".yml"):
        return path if path.is_absolute() else PROJECT_ROOT / path

    return SITE_CONFIG_DIR / f"{value}.yaml"


@lru_cache(maxsize=None)
def load_site_config(config_name=None):
    path = _config_path(config_name)

    if not path.exists():
        raise FileNotFoundError(f"Site config not found: {path}")

    with path.open("r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}

    if not isinstance(config, dict):
        raise ValueError(f"Site config must be a mapping: {path}")

    config.setdefault("site", path.stem)
    config["_config_path"] = str(path)
    return config


def _current_viewport_name():
    return os.environ.get("VISUAL_VIEWPORT") or "desktop"


def _deep_merge(base, override):
    merged = copy.deepcopy(base)

    for key, value in (override or {}).items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)

    return merged


def get_page_config(page, site_config=None, viewport=None):
    config = site_config if isinstance(site_config, dict) else load_site_config(site_config)
    pages = config.get("pages", {})

    if page not in pages:
        raise KeyError(f"Page {page!r} not found in {config.get('_config_path')}")

    page_config = copy.deepcopy(pages[page])
    viewport_name = viewport or _current_viewport_name()
    viewport_config = page_config.get(viewport_name, {})

    if viewport_config:
        page_config = _deep_merge(page_config, viewport_config)

    for reserved in ("desktop", "mobile", "tablet"):
        page_config.pop(reserved, None)

    if "url" not in page_config:
        raise KeyError(f"Page {page!r} is missing url in {config.get('_config_path')}")

    return page_config


def get_access_denied_patterns(site_config=None, page_config=None):
    config = site_config if isinstance(site_config, dict) else load_site_config(site_config)
    patterns = list(DEFAULT_ACCESS_DENIED_PATTERNS)
    patterns.extend(config.get("access_denied_patterns", []))

    if page_config:
        patterns.extend(page_config.get("access_denied_patterns", []))

    seen = set()
    unique_patterns = []
    for pattern in patterns:
        key = str(pattern).lower()
        if key in seen:
            continue
        seen.add(key)
        unique_patterns.append(pattern)

    return unique_patterns


def _merge_dynamic_hide(base, override):
    merged = {
        "selectors": list(base.get("selectors", [])),
        "text_exact": list(base.get("text_exact", [])),
        "text_contains": list(base.get("text_contains", [])),
        "text_max_length": base.get("text_max_length", 80),
        "container_selector": base.get("container_selector", "section, form, div"),
    }

    if not override:
        return merged

    if not isinstance(override, dict):
        raise ValueError(f"dynamic_hide must be a mapping, got: {override!r}")

    if override.get("replace_defaults"):
        merged = {
            "selectors": [],
            "text_exact": [],
            "text_contains": [],
            "text_max_length": 80,
            "container_selector": "section, form, div",
        }

    for key in ("selectors", "text_exact", "text_contains"):
        values = override.get(key, [])
        if isinstance(values, str):
            values = [values]
        merged[key].extend(values)

    if "text_max_length" in override:
        merged["text_max_length"] = override["text_max_length"]

    if "container_selector" in override:
        merged["container_selector"] = override["container_selector"]

    return merged


def get_dynamic_hide_config(site_config=None, page_config=None):
    config = site_config if isinstance(site_config, dict) else load_site_config(site_config)
    merged = _merge_dynamic_hide(DEFAULT_DYNAMIC_HIDE, config.get("dynamic_hide"))
    merged = _merge_dynamic_hide(merged, page_config.get("dynamic_hide") if page_config else None)

    for key in ("selectors", "text_exact", "text_contains"):
        seen = set()
        values = []
        for value in merged.get(key, []):
            if not value:
                continue
            dedupe_key = str(value).lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            values.append(value)
        merged[key] = values

    return merged


def locator(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"Locator must be [method, value], got: {value!r}")

    method, selector = value
    return method, selector


def locator_map(values):
    return {name: locator(value) for name, value in values.items()}
