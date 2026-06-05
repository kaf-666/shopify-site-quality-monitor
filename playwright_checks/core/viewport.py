import os

from playwright_checks.core.config_loader import load_settings


VIEWPORT_ENV = "VISUAL_VIEWPORT"
DEFAULT_VIEWPORT = "desktop"
_CURRENT_VIEWPORT = None


def set_current_viewport(name):
    global _CURRENT_VIEWPORT
    _CURRENT_VIEWPORT = name
    os.environ[VIEWPORT_ENV] = name


def get_current_viewport_name():
    return _CURRENT_VIEWPORT or os.environ.get(VIEWPORT_ENV) or DEFAULT_VIEWPORT


def get_viewport_config(name=None):
    settings = load_settings()
    viewports = settings.get("viewports", {})
    viewport_name = name or get_current_viewport_name()

    if viewport_name not in viewports:
        raise KeyError(f"Viewport {viewport_name!r} not found in configs/settings.yaml")

    config = dict(viewports[viewport_name])
    config["name"] = viewport_name
    return config


def get_run_viewport_names():
    settings = load_settings()
    selected = os.environ.get(VIEWPORT_ENV)

    if selected:
        names = [name.strip() for name in selected.split(",") if name.strip()]
    else:
        names = settings.get("run_viewports") or [DEFAULT_VIEWPORT]

    viewports = settings.get("viewports", {})
    missing = [name for name in names if name not in viewports]
    if missing:
        raise KeyError(
            "Viewport(s) not found in configs/settings.yaml: "
            + ", ".join(missing)
        )

    return names


def is_mobile_viewport(name=None):
    return bool(get_viewport_config(name).get("is_mobile"))
