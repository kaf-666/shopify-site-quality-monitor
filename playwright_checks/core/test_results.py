import json
import os
from pathlib import Path

from playwright_checks.core.config_loader import PROJECT_ROOT, load_settings
from playwright_checks.core.paths import artifact_root, current_run_id


DEFAULT_RESULTS_FILE = PROJECT_ROOT / "reports" / "visual-results.json"
_RESULTS = []


def clear_results():
    _RESULTS.clear()


def add_result(result):
    _RESULTS.append(result)


def get_results():
    return list(_RESULTS)


def get_page_visual_status(site, viewport, page):
    relevant = [
        result
        for result in _RESULTS
        if result.get("site") == site
        and result.get("viewport") == viewport
        and result.get("page") == page
        and result.get("result_type", "visual") == "visual"
        and result.get("case") != "runtime"
    ]
    statuses = [result.get("status") for result in relevant]
    gated_statuses = [
        result.get("status")
        for result in relevant
        if result.get("affects_exit_code", False)
    ]
    if not statuses:
        return "not_run"
    if "failed" in gated_statuses:
        return "failed"
    if "warning" in gated_statuses or "warning" in statuses:
        return "warning"
    if "content_changed" in statuses:
        return "content_changed"
    if statuses and all(status == "initialized" for status in statuses):
        return "initialized"
    if statuses and all(status == "skipped" for status in statuses):
        return "not_run"
    return "passed"


def drop_results(viewport=None, page=None):
    kept_results = []

    for result in _RESULTS:
        if viewport is not None and result.get("viewport") != viewport:
            kept_results.append(result)
            continue

        if page is not None and result.get("page") != page:
            kept_results.append(result)
            continue

    _RESULTS[:] = kept_results


def _configured_results_file():
    settings = load_settings()
    configured_path = settings.get("results_file")

    if not configured_path:
        return DEFAULT_RESULTS_FILE

    result_path = Path(configured_path)
    if result_path.is_absolute():
        return result_path

    return PROJECT_ROOT / result_path


def write_results(path=None):
    output_path = Path(
        path or os.environ.get("TEST_RESULTS_FILE") or _configured_results_file()
    )
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path

    output_path = output_path.resolve()
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with output_path.open("w", encoding="utf-8") as file:
        json.dump(_RESULTS, file, ensure_ascii=False, indent=2)

    artifact_path = artifact_root() / current_run_id() / "visual-results.json"
    if output_path != artifact_path.resolve():
        os.makedirs(os.path.dirname(artifact_path), exist_ok=True)
        with artifact_path.open("w", encoding="utf-8") as file:
            json.dump(_RESULTS, file, ensure_ascii=False, indent=2)

    from playwright_checks.artifacts.screenshot_manager import (
        finalize_artifact_run,
    )

    finalize_artifact_run(
        has_failure=any(
            result.get("status") == "failed"
            and result.get("affects_exit_code", True)
            for result in _RESULTS
        )
    )
    return str(output_path)
