import json
import os
import re
from pathlib import Path

from playwright_checks.health.models import utc_timestamp
from playwright_checks.health.shadow_comparison import apply_maturity
from playwright_checks.health.shadow_maturity import ShadowMaturityPolicy


SHADOW_HISTORY_SCHEMA_VERSION = "1.0"
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")


class ShadowHistoryStore:
    """Append-only, summary-only JSONL evidence for shadow maturity."""

    def __init__(self, history_root):
        self.history_root = Path(history_root).resolve()

    def path_for(self, site_id):
        site = str(site_id or "").strip()
        if not site or not _SAFE_SEGMENT.fullmatch(site) or site in (".", ".."):
            raise ValueError(f"Unsafe shadow history site_id: {site_id!r}")
        path = (self.history_root / "shadow" / f"{site}.jsonl").resolve()
        try:
            path.relative_to(self.history_root)
        except ValueError as error:
            raise ValueError("Shadow history path escapes history root") from error
        return path

    def read(self, site_id):
        path = self.path_for(site_id)
        if not path.is_file():
            return []
        records = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and value.get("site_id") == site_id:
                    records.append(value)
        return records

    def append(self, record):
        if not isinstance(record, dict):
            raise TypeError("shadow history record must be a mapping")
        path = self.path_for(record.get("site_id"))
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        descriptor = os.open(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0),
            0o600,
        )
        try:
            os.write(descriptor, encoded)
        finally:
            os.close(descriptor)
        return path


def record_shadow_history(
    comparison,
    page_ids,
    viewport_names,
    policy=None,
    history_root=None,
    scheduler="MANUAL",
    scheduler_metadata=None,
    legacy_gate_failed=False,
    timestamp=None,
):
    policy = policy or ShadowMaturityPolicy()
    store = ShadowHistoryStore(
        history_root or Path(__file__).resolve().parents[2] / "history"
    )
    scope = shadow_scope(page_ids, viewport_names)
    record = build_history_record(
        comparison,
        scope,
        scheduler=scheduler,
        scheduler_metadata=scheduler_metadata,
        legacy_gate_failed=legacy_gate_failed,
        timestamp=timestamp,
    )
    existing = [
        value
        for value in store.read(comparison.site_id)
        if (value.get("scope") or {}).get("scope_key") == scope["scope_key"]
    ]
    previous_fingerprint = (
        existing[-1].get("mapping_fingerprint") if existing else None
    )
    mapping_consistent = bool(
        previous_fingerprint is None
        or previous_fingerprint == record["mapping_fingerprint"]
    )
    record["mapping_consistent"] = mapping_consistent
    record["stable"] = policy.stable_record(
        record,
        mapping_consistent=mapping_consistent,
    )
    scoped_records = [*existing, record]
    stability = summarize_stability(scoped_records)
    apply_maturity(comparison, policy, stability=stability)
    record.update(
        {
            "migration_status": comparison.migration_status.value,
            "recommended_readiness": comparison.recommended_readiness.value,
            "maturity_stage": comparison.maturity_stage.value,
            "consecutive_stable_runs": comparison.consecutive_stable_runs,
        }
    )
    history_path = store.append(record)
    summary = {
        "schema_version": SHADOW_HISTORY_SCHEMA_VERSION,
        "site_id": comparison.site_id,
        "scope": scope,
        "history_path": str(history_path.resolve()),
        "history_record_count": len(scoped_records),
        "latest": _window_item(record),
        **stability,
        "migration_status": comparison.migration_status.value,
        "recommended_readiness": comparison.recommended_readiness.value,
        "maturity_stage": comparison.maturity_stage.value,
        "readiness_targets": policy.to_dict(),
    }
    return history_path, record, summary


def build_history_record(
    comparison,
    scope,
    scheduler="MANUAL",
    scheduler_metadata=None,
    legacy_gate_failed=False,
    timestamp=None,
):
    """Build a compact record; raw DOM, screenshots, traces and findings are excluded."""
    return {
        "schema_version": SHADOW_HISTORY_SCHEMA_VERSION,
        "run_id": comparison.run_id,
        "timestamp": timestamp or utc_timestamp(),
        "site_id": comparison.site_id,
        "scope": dict(scope),
        "scheduler": str(scheduler or "MANUAL").upper(),
        "scheduler_metadata": _summary_metadata(scheduler_metadata),
        "legacy_gate_failed": bool(legacy_gate_failed),
        "legacy_checks": comparison.legacy_check_count,
        "applicable_legacy_checks": comparison.applicable_legacy_check_count,
        "planned_checks": comparison.planned_check_count,
        "mapped_checks": comparison.mapped_legacy_check_count,
        "overall_coverage": comparison.overall_coverage_percent,
        "critical_coverage": comparison.critical_coverage_percent,
        "executable_checks": comparison.executable_count,
        "executable_mapped_checks": comparison.executable_mapped_legacy_count,
        "executable_coverage": comparison.executable_coverage_percent,
        "critical_executable_coverage": (
            comparison.critical_executable_coverage_percent
        ),
        "result_parity": comparison.result_parity_percent,
        "result_parity_samples": comparison.result_parity_sample_count,
        "evidence_parity": comparison.evidence_parity_percent,
        "evidence_parity_samples": comparison.evidence_parity_sample_count,
        "policy_regressions": comparison.policy_regression_count,
        "executor_errors": comparison.executor_error_count,
        "executor_timeouts": comparison.executor_timeout_count,
        "unsupported_count": comparison.unsupported_executor_count,
        "flaky_count": comparison.flaky_count,
        "mapping_fingerprint": comparison.mapping_fingerprint,
        "migration_status": comparison.migration_status.value,
        "recommended_readiness": comparison.recommended_readiness.value,
        "maturity_stage": comparison.maturity_stage.value,
    }


def shadow_scope(page_ids, viewport_names):
    pages = sorted({str(value) for value in (page_ids or [])})
    viewports = sorted({str(value) for value in (viewport_names or [])})
    scope_key = "pages=" + ",".join(pages) + "|viewports=" + ",".join(viewports)
    return {"pages": pages, "viewports": viewports, "scope_key": scope_key}


def summarize_stability(records):
    values = [dict(value) for value in records if isinstance(value, dict)]
    consecutive = 0
    for value in reversed(values):
        if not bool(value.get("stable")):
            break
        consecutive += 1
    last_5 = values[-5:]
    last_10 = values[-10:]
    return {
        "last_5_runs": [_window_item(value) for value in last_5],
        "last_10_runs": [_window_item(value) for value in last_10],
        "last_5": _window_summary(last_5),
        "last_10": _window_summary(last_10),
        "consecutive_stable_runs": consecutive,
    }


def _window_item(record):
    return {
        "run_id": record.get("run_id"),
        "timestamp": record.get("timestamp"),
        "stable": bool(record.get("stable")),
        "mapping_consistent": bool(record.get("mapping_consistent")),
        "result_parity": record.get("result_parity"),
        "evidence_parity": record.get("evidence_parity"),
        "policy_regressions": int(record.get("policy_regressions", 0) or 0),
        "executor_errors": int(record.get("executor_errors", 0) or 0),
        "executor_timeouts": int(record.get("executor_timeouts", 0) or 0),
    }


def _window_summary(records):
    count = len(records)
    stable_count = sum(1 for value in records if value.get("stable"))
    return {
        "run_count": count,
        "stable_count": stable_count,
        "stable_percent": round(stable_count * 100 / count, 2) if count else None,
    }


def _summary_metadata(value):
    if not isinstance(value, dict):
        return {}
    allowed = ("trigger", "mode")
    return {
        key: str(value[key])
        for key in allowed
        if value.get(key) is not None
    }
