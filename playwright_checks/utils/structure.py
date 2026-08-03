from playwright_checks.artifacts.dynamic import audit_dynamic_region
from playwright_checks.core.test_results import add_result
from playwright_checks.core.visual_policy import screenshot_case_policy
from playwright_checks.utils.capture import (
    wait_for_layout_stable,
    wait_for_visible_images,
)
from playwright_checks.utils.waits import locate_element
from playwright_checks.utils.visual import build_result


def run_structure_checks(ctx, page):
    failures = []
    results = []
    regions = ctx.page_config.get("dynamic_regions", []) or []

    if regions:
        print("\nStructure-only checks")

    for region in regions:
        if not isinstance(region, dict):
            continue
        name = region.get("name") or region.get("module")
        if not name:
            continue
        policy = screenshot_case_policy(
            ctx.page_name,
            name,
            site_config=ctx.site_config,
            page_config=ctx.page_config,
        )
        if not policy["enabled"] or policy["purpose"] != "structure_only":
            continue

        module_name = region.get("module")
        locator = ctx.modules.get(module_name) if module_name else None
        try:
            if locator is None:
                raise ValueError(
                    f"structure region {name!r} has no configured module locator"
                )
            element = locate_element(page, locator)
            element.scroll_into_view_if_needed(timeout=10000)
            wait_for_visible_images(element, timeout=10)
            if not wait_for_layout_stable(element, timeout=10):
                raise AssertionError(
                    f"structure region {name!r} layout is not stable"
                )
            audit = audit_dynamic_region(
                element,
                region,
                page_config=ctx.page_config,
            )
            structural_status = audit.get("structural_status", "passed")
            issues = list(audit.get("structural_issues", []))
            failed = structural_status != "passed"
            status = "failed" if failed else "passed"
            if failed:
                message = f"structure [{name}] failed: {', '.join(issues)}"
                print(f"FAIL [{name}] {', '.join(issues)}")
                failures.append(message)
            else:
                print(f"OK [{name}] structure")
            details = {
                **audit,
                "screenshot_purpose": "structure_only",
                "pixel_compare_skipped": True,
                "affects_exit_code": failed,
            }
            result = build_result(
                ctx.site,
                ctx.suite,
                ctx.page_name,
                policy["report_case"],
                status,
                None,
                error=(", ".join(issues) if failed else None),
                details=details,
            )
        except Exception as error:
            message = (
                f"structure [{name}] error: "
                f"{type(error).__name__}: {error}"
            )
            print(f"FAIL [{name}] structure error: {error}")
            failures.append(message)
            result = build_result(
                ctx.site,
                ctx.suite,
                ctx.page_name,
                policy["report_case"],
                "failed",
                None,
                error=message,
                details={
                    "screenshot_purpose": "structure_only",
                    "structural_status": "failed",
                    "structural_issues": ["structure_check_error"],
                    "pixel_compare_skipped": True,
                    "affects_exit_code": True,
                },
            )
        add_result(result)
        results.append(result)

    return failures, results
