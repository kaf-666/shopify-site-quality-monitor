import json
import os

from common.utils import PROJECT_ROOT


DEFAULT_RESULTS_FILE = os.path.join(PROJECT_ROOT, "reports", "visual-results.json")

# 当前进程内累积的结构化测试结果。
_RESULTS = []


def clear_results():
    """清空本轮测试结果。"""
    _RESULTS.clear()


def add_result(result):
    """追加单个测试结果。"""
    _RESULTS.append(result)


def get_results():
    """返回结果副本，避免外部直接修改内部列表。"""
    return list(_RESULTS)


def write_results(path=None):
    """把视觉测试结果写入 JSON 文件。"""
    output_path = os.path.abspath(
        path or os.environ.get("TEST_RESULTS_FILE") or DEFAULT_RESULTS_FILE
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(_RESULTS, file, ensure_ascii=False, indent=2)

    return output_path
