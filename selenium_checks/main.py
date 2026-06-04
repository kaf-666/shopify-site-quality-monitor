import sys
from pathlib import Path


CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from home import run as run_home

from collection import run as run_collection

from product import run as run_product
from common.test_results import clear_results, write_results


def print_failure_summary(failures):
    """集中打印 Selenium 阶段的失败信息。"""
    print("\n" + "=" * 50)
    print("❌ Selenium 失败汇总")
    print("=" * 50)
    for index, failure in enumerate(failures, 1):
        print(f"{index}. {failure}")


def run_all():
    """按首页、PLP、PDP 顺序运行完整视觉回归。"""
    failures = []
    clear_results()

    print("=" * 50)
    print("🚀 开始运行视觉回归检测")
    print("=" * 50)

    print("\n🏠 首页检测")
    failures.extend(run_home())

    print("\n🛍️ PLP检测")
    failures.extend(run_collection())

    print("\n📦 PDP检测")
    failures.extend(run_product())

    results_file = write_results()
    print(f"\n📄 视觉测试结果: {results_file}")

    if failures:
        print_failure_summary(failures)
        return 1

    print("\n🎉 所有视觉回归检测完成")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
