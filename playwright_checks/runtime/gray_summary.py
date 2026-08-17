import argparse
import json
import os
from pathlib import Path

from playwright_checks.core.config_loader import PROJECT_ROOT
from playwright_checks.runtime.session import runtime_failure_messages


SCHEMA_VERSION = "1.0"
SITE_KEY = "mondressy_US"
EXPECTED_SCOPES = tuple(
    (viewport, page)
    for viewport in ("desktop", "mobile")
    for page in ("home", "collection", "product")
)
DEFAULT_REQUEST_PROFILE = {
    "request_header_injection": "route",
    "http_cache_mode": "disabled_by_routing",
    "run_profile": "intercepted_cold_context",
}
TOTAL_KEYS = (
    "runtime_passed_scope_count",
    "runtime_warning_scope_count",
    "runtime_failed_scope_count",
    "runtime_findings_count",
    "runtime_gated_failure_count",
    "visual_failure_count",
    "content_changed_count",
    "execution_error_count",
    "console_event_count",
    "network_anomaly_count",
    "loading_anomaly_count",
    "first_party_event_count",
    "third_party_event_count",
)
SCOPE_OUTPUT_KEYS = (
    "site",
    "viewport",
    "page",
    "evidence_available",
    "page_summary_available",
    "attempt",
    "runtime_status",
    "runtime_affects_exit_code",
    "findings_count",
    "runtime_gated_failure_count",
    "visual_failure_count",
    "content_changed_count",
    "execution_error_count",
    "console_event_count",
    "network_anomaly_count",
    "loading_anomaly_count",
    "first_party_event_count",
    "third_party_event_count",
    "collector_error_count",
    "automation_error_count",
)


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
    results = _load_results(root, run_id)
    scopes = []
    missing_scopes = []
    validation_errors = []

    for viewport, page in EXPECTED_SCOPES:
        expected = {
            "site": SITE_KEY,
            "viewport": viewport,
            "page": page,
        }
        runtime_dir = (
            root
            / "artifacts"
            / run_id
            / SITE_KEY
            / viewport
            / page
            / "runtime"
        )
        attempt, attempt_number = _load_latest_attempt(
            runtime_dir,
            expected,
        )
        page_summary = _page_summary(results, expected)
        scope_results = _scope_results(results, expected)
        scope = _summarize_scope(
            expected,
            attempt,
            attempt_number,
            page_summary,
            scope_results,
        )
        scopes.append(scope)

        missing = []
        if attempt is None:
            missing.append("runtime_attempt")
        if not page_summary:
            missing.append("page_summary")
        if missing:
            missing_scopes.append({**expected, "missing": missing})
            validation_errors.append(
                f"{viewport}/{page}: missing {', '.join(missing)}"
            )

        if attempt is not None:
            for key, expected_value in DEFAULT_REQUEST_PROFILE.items():
                if attempt.get(key) != expected_value:
                    validation_errors.append(
                        f"{viewport}/{page}: unexpected {key}"
                    )
        if scope["runtime_affects_exit_code"]:
            validation_errors.append(
                f"{viewport}/{page}: runtime exit gate is enabled"
            )
        if scope["runtime_gated_failure_count"]:
            validation_errors.append(
                f"{viewport}/{page}: runtime failures are gated"
            )

    totals = _aggregate_totals(scopes)
    if (
        totals["execution_error_count"] == 0
        and int(python_exit_code) != 0
        and not results
    ):
        totals["execution_error_count"] = 1

    expected_scopes = [
        {"site": SITE_KEY, "viewport": viewport, "page": page}
        for viewport, page in EXPECTED_SCOPES
    ]
    completed_scope_count = len(EXPECTED_SCOPES) - len(missing_scopes)
    summary_valid = not validation_errors
    jenkins_result = (
        "SUCCESS"
        if int(python_exit_code) == 0 and summary_valid
        else "FAILURE"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "site": SITE_KEY,
        "expected_scopes": expected_scopes,
        "expected_scope_count": len(EXPECTED_SCOPES),
        "completed_scope_count": completed_scope_count,
        "missing_scope_count": len(missing_scopes),
        "missing_scopes": missing_scopes,
        "totals": totals,
        "scopes": scopes,
        "python_exit_code": int(python_exit_code),
        "summary_valid": summary_valid,
        "report_only": not any(
            scope["runtime_affects_exit_code"] for scope in scopes
        ),
        "jenkins_result": jenkins_result,
        "validation_errors": validation_errors,
    }


def print_summary(summary):
    ordered_keys = (
        "schema_version",
        "run_id",
        "site",
        "expected_scope_count",
        "completed_scope_count",
        "missing_scope_count",
        "missing_scopes",
    )
    for key in ordered_keys:
        print(f"{key}={_display_value(summary.get(key))}")

    totals = summary.get("totals", {})
    for key in TOTAL_KEYS:
        print(f"{key}={_display_value(totals.get(key, 0))}")

    for scope in summary.get("scopes", []):
        prefix = f"scope.{scope['viewport']}.{scope['page']}"
        for key in SCOPE_OUTPUT_KEYS:
            print(f"{prefix}.{key}={_display_value(scope.get(key))}")

    for key in (
        "python_exit_code",
        "summary_valid",
        "report_only",
        "jenkins_result",
        "validation_errors",
    ):
        print(f"{key}={_display_value(summary.get(key))}")


def write_summary(summary, project_root=PROJECT_ROOT):
    output_path = (
        Path(project_root)
        / "artifacts"
        / summary["run_id"]
        / "gray-summary.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def main(argv=None):
    args = parse_args(argv)
    summary = summarize(
        args.run_id,
        args.python_exit_code,
        project_root=args.project_root,
    )
    try:
        write_summary(summary, args.project_root)
    except (OSError, TypeError, ValueError):
        summary["validation_errors"].append(
            "gray summary artifact could not be written"
        )
        summary["summary_valid"] = False
        summary["jenkins_result"] = "FAILURE"
    print_summary(summary)
    return 0 if summary["summary_valid"] else 1


def _load_latest_attempt(runtime_dir, expected):
    candidates = sorted(
        runtime_dir.glob("attempt-*.json"),
        key=_attempt_number,
        reverse=True,
    )
    for path in candidates:
        attempt_number = _attempt_number(path)
        if attempt_number < 0:
            continue
        payload = _load_json(path)
        if not isinstance(payload, dict) or not payload:
            continue
        if not _matches_scope(payload, expected, allow_missing=True):
            continue
        return payload, attempt_number
    return None, None


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


def _page_summary(results, expected):
    for result in reversed(results):
        if (
            isinstance(result, dict)
            and result.get("result_type") == "page_summary"
            and _matches_scope(result, expected)
        ):
            return result
    return {}


def _scope_results(results, expected):
    return [
        result
        for result in results
        if isinstance(result, dict) and _matches_scope(result, expected)
    ]


def _matches_scope(payload, expected, allow_missing=False):
    for key, expected_value in expected.items():
        actual = payload.get(key)
        if allow_missing and actual is None:
            continue
        if actual != expected_value:
            return False
    return True


def _summarize_scope(
    expected,
    attempt,
    attempt_number,
    page_summary,
    results,
):
    attempt = attempt or {}
    page_summary = page_summary or {}
    findings = attempt.get("findings")
    if not isinstance(findings, list):
        findings = page_summary.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    events = attempt.get("events", [])
    if not isinstance(events, list):
        events = []

    runtime_affects_exit_code = bool(
        attempt.get("runtime_affects_exit_code", False)
        or page_summary.get("runtime_affects_exit_code", False)
    )
    runtime_gated_failure_count = _runtime_gated_failure_count(page_summary)
    collector_error_count = _list_count(attempt.get("collector_errors"))
    automation_error_count = _list_count(attempt.get("automation_errors"))
    execution_error_count = _execution_error_count(
        results,
        attempt,
        collector_error_count,
        automation_error_count,
    )

    return {
        **expected,
        "evidence_available": bool(attempt),
        "page_summary_available": bool(page_summary),
        "attempt": attempt_number,
        "runtime_status": (
            attempt.get("runtime_status")
            or page_summary.get("runtime_status")
            or "unavailable"
        ),
        "runtime_affects_exit_code": runtime_affects_exit_code,
        "findings_count": len(findings),
        "runtime_gated_failure_count": runtime_gated_failure_count,
        "visual_failure_count": _visual_failure_count(results),
        "content_changed_count": _content_changed_count(results),
        "execution_error_count": execution_error_count,
        "console_event_count": _event_count(events, {"console"}),
        "network_anomaly_count": _event_count(
            events,
            {"request_failed", "http_error"},
            blocking_only=True,
        ),
        "loading_anomaly_count": _loading_anomaly_count(attempt),
        "first_party_event_count": _party_count(events, "first_party"),
        "third_party_event_count": _party_count(events, "third_party"),
        "collector_error_count": collector_error_count,
        "automation_error_count": automation_error_count,
    }


def _aggregate_totals(scopes):
    totals = {key: 0 for key in TOTAL_KEYS}
    for scope in scopes:
        status_key = {
            "passed": "runtime_passed_scope_count",
            "warning": "runtime_warning_scope_count",
            "failed": "runtime_failed_scope_count",
        }.get(scope["runtime_status"])
        if status_key:
            totals[status_key] += 1
        totals["runtime_findings_count"] += scope["findings_count"]
        for key in TOTAL_KEYS[4:]:
            totals[key] += scope[key]
    return totals


def _runtime_gated_failure_count(page_summary):
    if not page_summary:
        return 0
    normalized = dict(page_summary)
    findings = normalized.get("findings")
    normalized["findings"] = (
        [item for item in findings if isinstance(item, dict)]
        if isinstance(findings, list)
        else []
    )
    return len(runtime_failure_messages(normalized))


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
        and result.get("affects_exit_code", True)
    )


def _content_changed_count(results):
    return sum(
        1
        for result in results
        if result.get("result_type", "visual") == "visual"
        and result.get("case") != "runtime"
        and result.get("status") == "content_changed"
    )


def _visual_strict_warnings():
    value = os.environ.get("VISUAL_STRICT_WARNINGS")
    if value is not None:
        return value.strip().lower() in ("1", "true", "yes", "on")
    return os.environ.get("CI", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _execution_error_count(
    results,
    attempt,
    collector_error_count,
    automation_error_count,
):
    runtime_result_errors = sum(
        1
        for result in results
        if result.get("result_type", "visual") == "visual"
        and result.get("case") == "runtime"
        and result.get("status") == "failed"
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
    return execution_count


def _list_count(value):
    return len(value) if isinstance(value, list) else 0


def _event_count(events, event_types, blocking_only=False):
    return sum(
        _occurrences(event)
        for event in events
        if isinstance(event, dict)
        and event.get("event_type") in event_types
        and (not blocking_only or event.get("blocking", True))
    )


def _party_count(events, party):
    return sum(
        _occurrences(event)
        for event in events
        if isinstance(event, dict) and event.get("party") == party
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
    if value is None:
        return "none"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


if __name__ == "__main__":
    raise SystemExit(main())
