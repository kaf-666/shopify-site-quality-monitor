import os
import sys

from playwright_checks.checks.collection_check import run as run_collection
from playwright_checks.checks.home_check import run as run_home
from playwright_checks.checks.product_check import run as run_product
from playwright_checks.core.test_results import (
    clear_results,
    drop_results,
    get_results,
    write_results,
)
from playwright_checks.core.config_loader import load_site_config
from playwright_checks.core.viewport import get_run_viewport_names, set_current_viewport
from playwright_checks.health.reporting import write_health_reports_fail_open
from playwright_checks.health.shadow_runtime import run_shadow_pipeline_fail_open


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
    if any(
        "TerminalMainDocumentError" in failure
        for failure in failures
    ):
        return False

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

    shadow = run_shadow_pipeline_fail_open(
        get_results(),
        load_site_config(),
        viewport_names,
        selected_page_ids=[page[1] for page in run_pages],
        scheduler=os.environ.get("HEALTH_SCHEDULER", "MANUAL"),
        scheduler_metadata={
            "trigger": os.environ.get("HEALTH_TRIGGER", "MANUAL"),
            "mode": os.environ.get("HEALTH_RUNTIME_MODE", "MONITOR"),
        },
        legacy_gate_failed=bool(failures),
    )
    if shadow.error:
        print(
            "WARN shadow executor unavailable; legacy gate remains unchanged: "
            f"{shadow.error}"
        )
    elif shadow.enabled:
        print(f"Shadow check results: {shadow.check_results_path}")
        print(f"Shadow observations: {shadow.observations_path}")
        print(f"Shadow comparison: {shadow.comparison_path}")
        print(f"Shadow history summary: {shadow.history_summary_path}")
        if shadow.history_error:
            print(
                "WARN shadow history unavailable; shadow execution and "
                f"legacy gate remain unchanged: {shadow.history_error}"
            )

    results_file = write_results()
    print(f"\nVisual test results: {results_file}")

    health_paths = write_health_reports_fail_open(get_results())
    if health_paths.get("error"):
        print(
            "WARN health report generation was unavailable; "
            f"legacy results remain valid: {health_paths['error']}"
        )
    elif health_paths.get("json"):
        print(f"Website health report: {health_paths['json']}")
        if health_paths.get("html"):
            print(f"Website health dashboard: {health_paths['html']}")
        if health_paths.get("site_profile"):
            print(f"Site profile: {health_paths['site_profile']}")
        if health_paths.get("test_plan"):
            print(f"Deterministic test plan: {health_paths['test_plan']}")

    if failures:
        print_failure_summary(failures)
        return 1

    print("\nAll Playwright visual checks completed")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
