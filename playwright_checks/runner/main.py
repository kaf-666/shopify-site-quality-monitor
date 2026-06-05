import sys

from playwright_checks.checks.collection_check import run as run_collection
from playwright_checks.checks.home_check import run as run_home
from playwright_checks.checks.product_check import run as run_product
from playwright_checks.core.test_results import clear_results, write_results
from playwright_checks.core.viewport import get_run_viewport_names, set_current_viewport


def print_failure_summary(failures):
    print("\n" + "=" * 50)
    print("Playwright failure summary")
    print("=" * 50)
    for index, failure in enumerate(failures, 1):
        print(f"{index}. {failure}")


def with_viewport(failures, viewport_name):
    return [f"[{viewport_name}] {failure}" for failure in failures]


def run_all():
    failures = []
    clear_results()
    viewport_names = get_run_viewport_names()

    print("=" * 50)
    print("Start Playwright visual regression")
    print("=" * 50)

    for viewport_name in viewport_names:
        set_current_viewport(viewport_name)

        print("\n" + "-" * 50)
        print(f"Viewport: {viewport_name}")
        print("-" * 50)

        print("\nHome checks")
        failures.extend(with_viewport(run_home(), viewport_name))

        print("\nPLP checks")
        failures.extend(with_viewport(run_collection(), viewport_name))

        print("\nPDP checks")
        failures.extend(with_viewport(run_product(), viewport_name))

    results_file = write_results()
    print(f"\nVisual test results: {results_file}")

    if failures:
        print_failure_summary(failures)
        return 1

    print("\nAll Playwright visual checks completed")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
