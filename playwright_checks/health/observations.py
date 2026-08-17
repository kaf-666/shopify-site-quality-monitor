from playwright_checks.core.paths import current_run_id
from playwright_checks.core.test_results import add_result
from playwright_checks.core.viewport import get_current_viewport_name
from playwright_checks.runtime.evidence import redact_text, sanitize_payload


def record_check_observation(
    ctx,
    case,
    dimension,
    failures=None,
    details=None,
    capability=None,
):
    """Persist a non-visual deterministic check in the shared result stream.

    Historically these checks only contributed a process-exit string.  The
    Health layer needs a structured observation so a failure can retain its
    scope and evidence without parsing console output.
    """

    messages = [
        redact_text(str(message))
        for message in (failures or [])
        if str(message).strip()
    ]
    status = "failed" if messages else "passed"
    result = sanitize_payload(
        {
            "schema_version": "1.0",
            "result_type": "deterministic_check",
            "site": ctx.site,
            "suite": "health_observation",
            "run_id": current_run_id(),
            "viewport": get_current_viewport_name(),
            "page": ctx.page_name,
            "case": str(case),
            "dimension": str(dimension),
            "capability": capability,
            "status": status,
            "affects_exit_code": bool(messages),
            "messages": messages,
            "evidence_level": "MEDIUM" if messages else "NONE",
            "details": dict(details or {}),
        }
    )
    add_result(result)
    return result
