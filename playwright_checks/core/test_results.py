import json
import os

from playwright_checks.utils.waits import PROJECT_ROOT


DEFAULT_RESULTS_FILE = os.path.join(PROJECT_ROOT, "reports", "visual-results.json")
_RESULTS = []


def clear_results():
    _RESULTS.clear()


def add_result(result):
    _RESULTS.append(result)


def get_results():
    return list(_RESULTS)


def write_results(path=None):
    output_path = os.path.abspath(
        path or os.environ.get("TEST_RESULTS_FILE") or DEFAULT_RESULTS_FILE
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(_RESULTS, file, ensure_ascii=False, indent=2)

    return output_path
