import sys

from home import run as run_home

from collection import run as run_collection

from product import run as run_product


def print_failure_summary(failures):
    print("\n" + "=" * 50)
    print("❌ Selenium 失败汇总")
    print("=" * 50)
    for index, failure in enumerate(failures, 1):
        print(f"{index}. {failure}")


def run_all():
    failures = []

    print("=" * 50)
    print("🚀 开始运行视觉回归检测")
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

    print("\n🎉 所有视觉回归检测完成")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
