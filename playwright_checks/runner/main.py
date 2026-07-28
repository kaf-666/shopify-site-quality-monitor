import os
import sys

from playwright_checks.checks.collection_check import run as run_collection
from playwright_checks.checks.home_check import run as run_home
from playwright_checks.checks.product_check import run as run_product
from playwright_checks.core.test_results import clear_results, drop_results, write_results
from playwright_checks.core.viewport import get_run_viewport_names, set_current_viewport


PAGE_ENV = "VISUAL_PAGE"
ALL_PAGES = (
    ("Home", "home", run_home),
    ("PLP", "collection", run_collection),
    ("PDP", "product", run_product),
)


def print_failure_summary(failures):
    print("\n" + "=" * 50)
    print("Playwright failure summary")
    print("=" * 50)
    for index, failure in enumerate(failures, 1):
        print(f"{index}. {failure}")


def with_viewport(failures, viewport_name):
    return [f"[{viewport_name}] {failure}" for failure in failures]


def should_retry_page(failures):
    retry_patterns = (
        "Timeout",
        "capture failed",
        "DOM [",
        "runtime error",
        "runtime health failed",
        "not found",
        "not ready",
    )
    return any(
        any(pattern in failure for pattern in retry_patterns)
        for failure in failures
    )


def run_page(label, page_name, run_func, viewport_name):
    print(f"\n{label} checks")
    failures = run_func()

    if failures and should_retry_page(failures):
        print(f"\n{label} checks retry after transient failure")
        drop_results(viewport=viewport_name, page=page_name)
        failures = run_func()

    return with_viewport(failures, viewport_name)


def get_run_pages():
    selected = (os.environ.get(PAGE_ENV) or "all").strip().lower()
    if selected in ("", "all"):
        return ALL_PAGES

    for page in ALL_PAGES:
        if page[1] == selected:
            return (page,)

    allowed = ", ".join([page[1] for page in ALL_PAGES] + ["all"])
    raise ValueError(f"Unsupported VISUAL_PAGE={selected!r}. Allowed values: {allowed}")


def run_all():
    failures = []
    clear_results()
    viewport_names = get_run_viewport_names()
    run_pages = get_run_pages()

    print("=" * 50)
    print("Start Playwright visual regression")
    print("=" * 50)

    for viewport_name in viewport_names:
        set_current_viewport(viewport_name)

        print("\n" + "-" * 50)
        print(f"Viewport: {viewport_name}")
        print("-" * 50)

        for label, page_name, run_func in run_pages:
            failures.extend(run_page(label, page_name, run_func, viewport_name))

    results_file = write_results()
    print(f"\nVisual test results: {results_file}")

    if failures:
        print_failure_summary(failures)
        return 1

    print("\nAll Playwright visual checks completed")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
