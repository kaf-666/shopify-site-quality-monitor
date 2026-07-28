import argparse
import json
import os
from pathlib import Path

from playwright_checks.core.config_loader import PROJECT_ROOT
from playwright_checks.runtime.session import runtime_failure_messages


SITE_KEY = "mondressy_US"
PAGE_NAME = "home"
VIEWPORT_NAME = "desktop"
DEFAULT_REQUEST_PROFILE = {
    "request_header_injection": "route",
    "http_cache_mode": "disabled_by_routing",
    "run_profile": "intercepted_cold_context",
}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Print the Mondressy US Runtime gray-validation summary."
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--python-exit-code", required=True, type=int)
    parser.add_argument(
        "--project-root",
        default=str(PROJECT_ROOT),
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def summarize(run_id, python_exit_code, project_root=PROJECT_ROOT):
    root = Path(project_root)
    runtime_dir = (
        root
        / "artifacts"
        / run_id
        / SITE_KEY
        / VIEWPORT_NAME
        / PAGE_NAME
        / "runtime"
    )
    attempt = _load_latest_attempt(runtime_dir)
    results = _load_results(root, run_id)
    page_summary = _page_summary(results)
    validation_errors = []

    if attempt is None:
        validation_errors.append("runtime attempt evidence is unavailable")
        attempt = {}
    if not results:
        validation_errors.append("visual results evidence is unavailable")

    _validate_scope(attempt, validation_errors)

    findings = attempt.get("findings")
    if not isinstance(findings, list):
        findings = (
            page_summary.get("findings", [])
            if isinstance(page_summary.get("findings"), list)
            else []
        )
    events = attempt.get("events", [])
    if not isinstance(events, list):
        events = []

    runtime_exit_gate = bool(
        page_summary.get(
            "runtime_affects_exit_code",
            attempt.get("runtime_affects_exit_code", False),
        )
    )
    runtime_gated_failure_count = (
        len(runtime_failure_messages(page_summary))
        if page_summary
        else 0
    )
    visual_failure_count = _visual_failure_count(results)
    execution_error_count = _execution_error_count(
        results,
        attempt,
        python_exit_code,
    )
    profile = {
        key: attempt.get(key) or page_summary.get(key) or default
        for key, default in DEFAULT_REQUEST_PROFILE.items()
    }
    if runtime_exit_gate:
        validation_errors.append("runtime exit gate is enabled")
    if runtime_gated_failure_count:
        validation_errors.append("runtime failures are gated")
    for key, expected in DEFAULT_REQUEST_PROFILE.items():
        if attempt.get(key) != expected:
            validation_errors.append(f"unexpected {key}")
    summary_valid = not validation_errors or python_exit_code != 0
    jenkins_result = (
        "SUCCESS"
        if python_exit_code == 0 and summary_valid
        else "FAILURE"
    )

    return {
        "site_key": SITE_KEY,
        "page": "Home",
        "viewport": VIEWPORT_NAME,
        "report_only": not runtime_exit_gate,
        "runtime_findings_count": len(findings),
        "runtime_gated_failure_count": runtime_gated_failure_count,
        "visual_failure_count": visual_failure_count,
        "execution_error_count": execution_error_count,
        "runtime_exit_gate": runtime_exit_gate,
        "console_event_count": _event_count(events, {"console"}),
        "network_anomaly_count": _event_count(
            events,
            {"request_failed", "http_error"},
            blocking_only=True,
        ),
        "loading_anomaly_count": _loading_anomaly_count(attempt),
        "first_party_event_count": _party_count(events, "first_party"),
        "third_party_event_count": _party_count(events, "third_party"),
        **profile,
        "python_exit_code": int(python_exit_code),
        "jenkins_result": jenkins_result,
        "_summary_valid": summary_valid,
    }


def print_summary(summary):
    ordered_keys = (
        "site_key",
        "page",
        "viewport",
        "report_only",
        "runtime_findings_count",
        "runtime_gated_failure_count",
        "visual_failure_count",
        "execution_error_count",
        "runtime_exit_gate",
        "console_event_count",
        "network_anomaly_count",
        "loading_anomaly_count",
        "first_party_event_count",
        "third_party_event_count",
        "request_header_injection",
        "http_cache_mode",
        "run_profile",
        "python_exit_code",
        "jenkins_result",
    )
    for key in ordered_keys:
        print(f"{key}={_display_value(summary[key])}")


def main(argv=None):
    args = parse_args(argv)
    summary = summarize(
        args.run_id,
        args.python_exit_code,
        project_root=args.project_root,
    )
    print_summary(summary)
    return 0 if summary["_summary_valid"] else 1


def _load_latest_attempt(runtime_dir):
    candidates = sorted(
        runtime_dir.glob("attempt-*.json"),
        key=_attempt_number,
    )
    if not candidates:
        return None
    return _load_json(candidates[-1])


def _load_results(root, run_id):
    candidates = (
        root / "artifacts" / run_id / "visual-results.json",
        root / "reports" / "visual-results.json",
    )
    for path in candidates:
        payload = _load_json(path)
        if isinstance(payload, list):
            return payload
    return []


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _attempt_number(path):
    try:
        return int(path.stem.rsplit("-", 1)[-1])
    except ValueError:
        return -1


def _page_summary(results):
    for result in reversed(results):
        if (
            result.get("result_type") == "page_summary"
            and result.get("site") == SITE_KEY
            and result.get("viewport") == VIEWPORT_NAME
            and result.get("page") == PAGE_NAME
        ):
            return result
    return {}


def _validate_scope(attempt, validation_errors):
    expected = {
        "site": SITE_KEY,
        "page": PAGE_NAME,
        "viewport": VIEWPORT_NAME,
    }
    for key, value in expected.items():
        actual = attempt.get(key)
        if actual is not None and actual != value:
            validation_errors.append(f"unexpected {key}")


def _visual_failure_count(results):
    strict_warnings = _visual_strict_warnings()
    failure_statuses = {"failed"}
    if strict_warnings:
        failure_statuses.add("warning")
    return sum(
        1
        for result in results
        if result.get("result_type", "visual") == "visual"
        and result.get("case") != "runtime"
        and result.get("status") in failure_statuses
    )


def _visual_strict_warnings():
    value = os.environ.get("VISUAL_STRICT_WARNINGS")
    if value is not None:
        return value.strip().lower() in ("1", "true", "yes", "on")
    return (
        os.environ.get("CI", "").strip().lower() in ("1", "true", "yes", "on")
        or bool(os.environ.get("JENKINS_URL"))
    )


def _execution_error_count(results, attempt, python_exit_code):
    runtime_result_errors = sum(
        1
        for result in results
        if result.get("result_type", "visual") == "visual"
        and result.get("case") == "runtime"
        and result.get("status") == "failed"
    )
    collector_errors = attempt.get("collector_errors", [])
    collector_error_count = (
        len(collector_errors) if isinstance(collector_errors, list) else 0
    )
    automation_errors = attempt.get("automation_errors", [])
    automation_error_count = (
        len(automation_errors) if isinstance(automation_errors, list) else 0
    )
    execution_count = (
        runtime_result_errors
        + collector_error_count
        + (automation_error_count if runtime_result_errors == 0 else 0)
    )
    navigation = attempt.get("navigation", {})
    if (
        execution_count == 0
        and isinstance(navigation, dict)
        and navigation.get("error_type")
    ):
        execution_count = 1
    if execution_count == 0 and int(python_exit_code) != 0 and not results:
        execution_count = 1
    return execution_count


def _event_count(events, event_types, blocking_only=False):
    return sum(
        _occurrences(event)
        for event in events
        if event.get("event_type") in event_types
        and (not blocking_only or event.get("blocking", True))
    )


def _party_count(events, party):
    return sum(
        _occurrences(event)
        for event in events
        if event.get("party") == party
    )


def _loading_anomaly_count(attempt):
    health = attempt.get("pre_visual_health", {})
    if not isinstance(health, dict):
        return 0
    value = health.get(
        "loading_visible_count",
        health.get("loading_element_count", 0),
    )
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _occurrences(event):
    try:
        return max(1, int(event.get("count", 1)))
    except (TypeError, ValueError):
        return 1


def _display_value(value):
    if isinstance(value, bool):
        return str(value).lower()
    return value


if __name__ == "__main__":
    raise SystemExit(main())
