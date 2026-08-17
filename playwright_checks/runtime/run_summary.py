import json
from dataclasses import dataclass
from pathlib import Path

from playwright_checks.health.file_io import atomic_write_json
from playwright_checks.health.models import utc_timestamp


RUN_SUMMARY_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class MachineRunSummary:
    run_id: str
    overall_status: str
    sites_total: int
    sites_healthy: int
    sites_warn: int
    sites_failed: int
    blocked: int
    unverified: int
    critical_findings: int
    report_path: str | None
    manifest_path: str
    shadow_comparison_path: str | None
    shadow_history_summary_path: str | None
    legacy_exit_code: int
    generated_at: str
    schema_version: str = RUN_SUMMARY_SCHEMA_VERSION

    def to_dict(self):
        return dict(self.__dict__)


def build_machine_run_summary(
    run_id,
    legacy_exit_code,
    health_report_path,
    manifest_path,
    shadow_comparison_path=None,
    shadow_history_summary_path=None,
    overall_status_override=None,
):
    report = _read_json(health_report_path)
    overall = str(
        overall_status_override
        or report.get("overall_health")
        or report.get("status")
        or ("CRITICAL" if legacy_exit_code else "UNVERIFIED")
    ).upper()
    health_status = str(report.get("status") or "UNVERIFIED").upper()
    summary = report.get("summary") or {}
    severity = summary.get("severity_counts") or {}
    healthy = int(overall == "HEALTHY" and legacy_exit_code == 0)
    failed = int(legacy_exit_code != 0 or overall == "CRITICAL")
    warned = int(not healthy and not failed)
    return MachineRunSummary(
        run_id=str(run_id),
        overall_status=overall,
        sites_total=1,
        sites_healthy=healthy,
        sites_warn=warned,
        sites_failed=failed,
        blocked=int(summary.get("blocked_scope_count", 0) or 0),
        unverified=int(summary.get("unverified_scope_count", 0) or 0)
        + int(health_status == "UNVERIFIED"),
        critical_findings=int(severity.get("CRITICAL", 0) or 0),
        report_path=str(health_report_path) if health_report_path else None,
        manifest_path=str(manifest_path),
        shadow_comparison_path=(
            str(shadow_comparison_path) if shadow_comparison_path else None
        ),
        shadow_history_summary_path=(
            str(shadow_history_summary_path)
            if shadow_history_summary_path
            else None
        ),
        legacy_exit_code=int(legacy_exit_code),
        generated_at=utc_timestamp(),
    )


def write_machine_run_summary(summary, run_root):
    if not isinstance(summary, MachineRunSummary):
        raise TypeError("summary must be MachineRunSummary")
    path = Path(run_root).resolve() / "run-summary.json"
    atomic_write_json(path, summary.to_dict())
    return path.resolve()


def stdout_contract(summary, summary_path):
    return [
        "HEALTH_RUN_COMPLETE",
        f"run_id={summary.run_id}",
        f"status={summary.overall_status}",
        f"sites={summary.sites_total}",
        f"failed={summary.sites_failed}",
        f"warnings={summary.sites_warn}",
        f"report={summary.report_path or 'unavailable'}",
        f"manifest={summary.manifest_path}",
        f"summary={summary_path}",
        f"shadow={summary.shadow_comparison_path or 'disabled'}",
        f"history={summary.shadow_history_summary_path or 'disabled'}",
    ]


def _read_json(path):
    if path is None:
        return {}
    value = Path(path)
    if not value.is_file():
        return {}
    try:
        payload = json.loads(value.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}
