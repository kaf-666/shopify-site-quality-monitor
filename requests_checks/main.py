import sys

from home_health_check import run as run_home
from collection_health_check import run as run_collection
from product_health_check import run as run_product


def print_failure_summary(failures):
    """集中打印 requests 阶段的所有失败，便于 CI 或人工查看。"""
    print("\n" + "=" * 50)
    print("❌ requests 失败汇总")
    print("=" * 50)
    for index, failure in enumerate(failures, 1):
        print(f"{index}. {failure}")


def run_all():
    """按首页、PLP、PDP 顺序运行所有 requests 健康检查。"""
    failures = []

    print("=" * 50)
    print("🚀 开始运行 requests 健康检测")
    print("=" * 50)

    print("\n🏠 首页检测")
    failures.extend(run_home())

    print("\n🛍️ PLP检测")
    failures.extend(run_collection())

    print("\n📦 PDP检测")
    failures.extend(run_product())

    if failures:
        print_failure_summary(failures)
        return 1

    print("\n🎉 所有 requests 检测通过")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
