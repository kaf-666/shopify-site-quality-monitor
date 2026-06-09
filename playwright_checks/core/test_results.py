import json
import os
from pathlib import Path

from playwright_checks.core.config_loader import PROJECT_ROOT, load_settings


DEFAULT_RESULTS_FILE = PROJECT_ROOT / "reports" / "visual-results.json"
_RESULTS = []


def clear_results():
    _RESULTS.clear()


def add_result(result):
    _RESULTS.append(result)


def get_results():
    return list(_RESULTS)


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

    return str(output_path)
