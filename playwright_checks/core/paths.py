import os
import threading
from datetime import datetime
from pathlib import Path

from playwright_checks.core.config_loader import PROJECT_ROOT, load_settings
from playwright_checks.core.viewport import get_current_viewport_name


RUN_ID_ENV = "VISUAL_RUN_ID"
LEGACY_BASELINE_FALLBACK_ENV = "VISUAL_LEGACY_BASELINE_FALLBACK"

_RUN_ID = None
_ATTEMPT_LOCK = threading.Lock()
_PAGE_ATTEMPTS = {}


def _project_path(value):
    path = Path(value)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _path_setting(name, default):
    settings = load_settings()
    paths = settings.get("paths", {})
    return _project_path(paths.get(name, default))


def _bool_from_env(value):
    if value is None:
        return None
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def current_run_id():
    global _RUN_ID

    configured = os.environ.get(RUN_ID_ENV)
    if configured:
        return configured

    if _RUN_ID is None:
        _RUN_ID = datetime.now().strftime("%Y%m%d_%H%M%S")

    return _RUN_ID


def baseline_root():
    return _path_setting("baseline_root", "baselines")


def artifact_root():
    return _path_setting("artifact_root", "artifacts")


def legacy_screenshot_root_base():
    return _path_setting("legacy_screenshot_root", "screenshots")


def screenshot_suffix():
    settings = load_settings()
    return os.environ.get(
        "PLAYWRIGHT_SCREENSHOT_SUFFIX",
        settings.get("screenshot_suffix", "_playwright"),
    )


def legacy_baseline_fallback_enabled():
    env_value = _bool_from_env(os.environ.get(LEGACY_BASELINE_FALLBACK_ENV))
    if env_value is not None:
        return env_value

    settings = load_settings()
    paths = settings.get("paths", {})
    return bool(paths.get("legacy_baseline_fallback", True))


def baseline_dir(site, page, viewport=None):
    viewport_name = viewport or get_current_viewport_name()
    return baseline_root() / site / viewport_name / page


def legacy_screenshot_root(site, viewport=None):
    viewport_name = viewport or get_current_viewport_name()
    return legacy_screenshot_root_base() / f"{site}{screenshot_suffix()}" / viewport_name


def legacy_baseline_dir(site, page, viewport=None):
    return legacy_screenshot_root(site, viewport) / page / "baseline"


def artifact_viewport_dir(site, viewport=None):
    viewport_name = viewport or get_current_viewport_name()
    return artifact_root() / current_run_id() / site / viewport_name


def artifact_page_dir(site, page, viewport=None):
    return artifact_viewport_dir(site, viewport) / page


def next_page_attempt(site, page, viewport=None):
    viewport_name = viewport or get_current_viewport_name()
    key = (current_run_id(), site, viewport_name, page)
    with _ATTEMPT_LOCK:
        attempt = _PAGE_ATTEMPTS.get(key, 0) + 1
        _PAGE_ATTEMPTS[key] = attempt
    return attempt


def page_paths(site, page, attempt=None):
    page_dir = artifact_page_dir(site, page)
    selected_attempt = int(
        attempt or next_page_attempt(site, page)
    )
    temp_dir = page_dir / ".tmp" / f"attempt-{selected_attempt}"
    legacy_dir = None
    if legacy_baseline_fallback_enabled():
        legacy_dir = legacy_baseline_dir(site, page)

    return {
        "run_id": current_run_id(),
        "attempt": selected_attempt,
        "root_dir": str(artifact_viewport_dir(site)),
        "page_dir": str(page_dir),
        "temp_dir": str(temp_dir),
        "baseline_dir": str(baseline_dir(site, page)),
        "legacy_baseline_dir": str(legacy_dir) if legacy_dir else None,
        "current_dir": str(temp_dir / "current"),
        "diff_dir": str(temp_dir / "diff"),
    }


def relative_to_project(path):
    if not path:
        return None

    path = Path(path).resolve()
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)
