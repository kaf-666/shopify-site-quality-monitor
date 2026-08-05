from contextlib import nullcontext

from playwright_checks.core.config_loader import get_runtime_health_config
from playwright_checks.core.paths import current_run_id, relative_to_project
from playwright_checks.core.request_headers import signed_request_profile
from playwright_checks.core.test_results import (
    add_result,
    get_page_visual_status,
)
from playwright_checks.core.viewport import get_current_viewport_name
from playwright_checks.runtime.checks import (
    build_findings,
    collect_health_fingerprint,
    primary_finding,
    runtime_score,
    runtime_status,
)
from playwright_checks.runtime.collector import RuntimeEventCollector
from playwright_checks.runtime.evidence import (
    RuntimeEvidenceStore,
    redact_text,
    sanitize_payload,
)
from playwright_checks.runtime.models import (
    NavigationResult,
    RuntimeFinding,
    utc_timestamp,
)


class RuntimeHealthSession:
    def __init__(
        self,
        page,
        site_config,
        page_config,
        page_name,
        evidence_directory=None,
    ):
        self.page = page
        self.site_config = site_config
        self.page_config = page_config
        self.page_name = page_name
        self.site = site_config.get("site", "unknown")
        self.request_profile = signed_request_profile(site_config)
        self.config = get_runtime_health_config(site_config, page_config)
        self.config["_page_name"] = page_name
        self.navigation = NavigationResult(requested_url=page_config["url"])
        self.collector = RuntimeEventCollector(
            page,
            page_config["url"],
            self.config,
        )
        self.evidence = RuntimeEvidenceStore(
            self.site,
            page_name,
            directory=evidence_directory,
        )
        self._custom_evidence_directory = evidence_directory is not None
        self.pre_visual_health = None
        self.post_visual_state = None
        self.automation_errors = []
        self._finalized = False
        self._summary = None
        self._navigation_sequence = 0

    def start_before_navigation(self):
        try:
            self.collector.start()
        except Exception as error:
            self.collector._record_listener_error("collector.start", error)

    def begin_navigation(self):
        self._navigation_sequence += 1
        self.collector.set_navigation_sequence(self._navigation_sequence)
        return {
            "attempt_offset": len(self.navigation.attempts),
            "navigation_sequence": self._navigation_sequence,
        }

    def phase(self, name):
        return self.collector.phase(name)

    def navigation_attempt_phase(self, sequence_attempt):
        return self.phase(
            "navigation" if sequence_attempt == 1 else "navigation_retry"
        )

    def record_navigation_attempt(self, attempt):
        self.navigation.attempts.append(sanitize_payload(dict(attempt)))

    def complete_navigation(self, result=None, error=None):
        result = result or {}
        if not self.navigation.attempts and result.get("navigation_attempts"):
            self.navigation.attempts.extend(result["navigation_attempts"])
        last_attempt = (
            self.navigation.attempts[-1]
            if self.navigation.attempts
            else {}
        )
        self.navigation.final_url = (
            result.get("final_url")
            or last_attempt.get("final_url")
            or self._safe_page_url()
            or self.navigation.final_url
        )
        self.navigation.status = result.get(
            "status",
            result.get(
                "main_document_status",
                last_attempt.get("status", self.navigation.status),
            ),
        )
        self.navigation.redirected = bool(
            result.get(
                "redirected",
                last_attempt.get(
                    "redirected",
                    self.navigation.redirected,
                ),
            )
        )
        self.navigation.redirect_chain = list(
            result.get("redirect_chain")
            or last_attempt.get("redirect_chain")
            or self.navigation.redirect_chain
        )
        if error is not None:
            self.navigation.error_type = type(error).__name__
            self.navigation.error_message = redact_text(str(error))
        else:
            self.navigation.error_type = result.get("error_type")
            self.navigation.error_message = redact_text(
                result.get("error_message")
            )

    def record_automation_error(self, error, phase):
        self.automation_errors.append(
            {
                "phase": phase,
                "error_type": type(error).__name__,
                "message": redact_text(str(error)),
            }
        )

    def collect_after_ready(self):
        if not self.config.get("enabled", True):
            return None
        try:
            self.pre_visual_health = collect_health_fingerprint(
                self.page,
                self.page_config,
                self.config,
            )
            self.pre_visual_health["final_url"] = self._safe_page_url()
            self.pre_visual_health["main_document_status"] = (
                self.navigation.status
            )
        except Exception as error:
            self.collector._record_listener_error(
                "health_fingerprint",
                error,
            )
            self.pre_visual_health = {
                "probe_error": redact_text(
                    f"{type(error).__name__}: {error}"
                ),
                "critical_elements": [],
                "missing_critical_elements": [],
                "optional_elements": [],
                "missing_optional_elements": [],
            }
        self.pre_visual_health.setdefault(
            "final_url",
            self._safe_page_url(),
        )
        self.pre_visual_health.setdefault(
            "main_document_status",
            self.navigation.status,
        )
        return self.pre_visual_health

    def capture_post_visual_state(self):
        try:
            self.post_visual_state = {
                "url": self._safe_page_url(),
                "title": self.page.title() if self.page_available() else None,
                "event_count": sum(
                    item.get("count", 1)
                    for item in self.collector.snapshot().get("events", [])
                ),
                "page_available": self.page_available(),
            }
        except Exception as error:
            self.collector._record_listener_error(
                "post_visual_state",
                error,
            )
            self.post_visual_state = {
                "page_available": False,
                "probe_error": redact_text(
                    f"{type(error).__name__}: {error}"
                ),
            }
        return self.post_visual_state

    def finalize(self, visual_status="not_run"):
        if self._finalized:
            return self._summary

        enabled = self.config.get("enabled", True)
        if not enabled:
            self._summary = {
                "schema_version": "1.1",
                "result_type": "page_summary",
                "site": self.site,
                "suite": "page_health",
                "run_id": current_run_id(),
                "viewport": get_current_viewport_name(),
                "page": self.page_name,
                "case": "page_summary",
                "status": combine_statuses(visual_status, "disabled"),
                "overall_status": combine_statuses(
                    visual_status,
                    "disabled",
                ),
                "visual_status": visual_status,
                "runtime_status": "disabled",
                "runtime_mode": "disabled",
                "runtime_affects_exit_code": False,
                "runtime_fail_on_failed": False,
                "runtime_fail_on_warning": False,
                "runtime_exit_status": "disabled",
                "runtime_score": 100,
                "primary_failure_reason": None,
                "primary_failure_type": None,
                "runtime_evidence": None,
                "runtime_attempt_evidence": None,
                "attempt": None,
                "attempts": [],
                "initial_runtime_status": "disabled",
                "final_runtime_status": "disabled",
                "worst_runtime_status": "disabled",
                "recovered_after_retry": False,
                "retry_count": 0,
                "findings": [],
            }
            add_result(self._summary)
            self._finalized = True
            self._print_summary()
            return self._summary

        if (
            enabled
            and self.pre_visual_health is None
            and self.page_available()
        ):
            self.collect_after_ready()
        if self.post_visual_state is None:
            self.capture_post_visual_state()

        collector_snapshot = self.collector.snapshot()
        health = self.pre_visual_health or {
            "runtime_health_enabled": enabled,
            "critical_elements": [],
            "missing_critical_elements": [],
            "optional_elements": [],
            "missing_optional_elements": [],
        }
        navigation = self.navigation.to_dict()
        findings = (
            build_findings(
                navigation,
                collector_snapshot,
                health,
                self.config,
            )
            if enabled
            else []
        )
        for item in self.automation_errors:
            findings.append(
                RuntimeFinding(
                    "error",
                    "automation_runtime_error",
                    f"Playwright failed during {item['phase']}.",
                    category="test_environment_error",
                    evidence=item,
                )
            )

        status = runtime_status(findings)
        primary = primary_finding(findings)
        policy = runtime_reporting_policy(self.config)
        evidence_health = dict(health)
        evidence_health.pop("body_text", None)
        if _is_terminal_main_document_status(self.navigation.status):
            evidence_health["terminal_page_evidence"] = {
                "status": self.navigation.status,
                "final_url": self.navigation.final_url,
                "title": health.get("title"),
                "body_text_length": health.get("body_text_length"),
                "dom_node_count": health.get("dom_node_count"),
            }
            if (
                not self._custom_evidence_directory
                and self.page_available()
            ):
                try:
                    from playwright_checks.artifacts.screenshot_manager import (
                        ScreenshotArtifactManager,
                    )

                    terminal_path = ScreenshotArtifactManager(
                        self.site,
                        self.page_name,
                        site_config=self.site_config,
                        page_config=self.page_config,
                    ).capture_terminal_page(
                        self.page,
                        self.navigation.status,
                        self.navigation.final_url,
                    )
                    evidence_health["terminal_page_evidence"][
                        "screenshot"
                    ] = terminal_path
                except Exception as error:
                    self.collector._record_listener_error(
                        "terminal_page_screenshot",
                        error,
                    )
        attempt_payload = {
            "timestamp": utc_timestamp(),
            "site": self.site,
            "page": self.page_name,
            "viewport": get_current_viewport_name(),
            "run_id": current_run_id(),
            "runtime_status": status,
            "runtime_score": runtime_score(findings),
            "runtime_mode": policy["runtime_mode"],
            "runtime_affects_exit_code": policy[
                "runtime_affects_exit_code"
            ],
            "runtime_fail_on_failed": policy["fail_on_failed"],
            "runtime_fail_on_warning": policy["fail_on_warning"],
            "primary_failure_reason": (
                primary.reason_code if primary else None
            ),
            "primary_failure_type": (
                (primary.category or primary.reason_code)
                if primary
                else None
            ),
            "navigation": navigation,
            "pre_visual_health": evidence_health,
            "post_visual_state": self.post_visual_state,
            "events": collector_snapshot.get("events", []),
            "event_counts": collector_snapshot.get("event_counts", {}),
            "dropped_event_counts": collector_snapshot.get(
                "dropped_event_counts",
                {},
            ),
            "collector_errors": collector_snapshot.get(
                "collector_errors",
                [],
            ),
            "automation_errors": self.automation_errors,
            "findings": [finding.to_dict() for finding in findings],
            **self.request_profile,
        }

        attempt_number = None
        attempt_path = None
        summary_path = None
        evidence_summary = {"attempts": []}
        try:
            attempt_number, attempt_path = self.evidence.write_attempt(
                attempt_payload
            )
            summary_path, evidence_summary = self.evidence.write_summary(
                attempt_number,
                attempt_path,
                attempt_payload,
            )
        except Exception as error:
            self.collector._record_listener_error("evidence.write", error)
            evidence_write_finding = RuntimeFinding(
                "warning",
                "runtime_evidence_write_failed",
                "Runtime evidence could not be written.",
                category="test_environment_error",
                evidence={
                    "error": redact_text(
                        f"{type(error).__name__}: {error}"
                    )
                },
            ).to_dict()
            attempt_payload["findings"].append(evidence_write_finding)
            if status == "passed":
                status = "warning"
                attempt_payload["runtime_status"] = status
                attempt_payload["runtime_score"] = 90
                attempt_payload["primary_failure_reason"] = (
                    "runtime_evidence_write_failed"
                )
                attempt_payload["primary_failure_type"] = (
                    "test_environment_error"
                )

        initial_runtime_status = evidence_summary.get(
            "initial_runtime_status",
            status,
        )
        final_runtime_status = evidence_summary.get(
            "final_runtime_status",
            status,
        )
        worst_runtime_status = evidence_summary.get(
            "worst_runtime_status",
            status,
        )
        recovered_after_retry = bool(
            evidence_summary.get("recovered_after_retry", False)
        )
        retry_count = int(evidence_summary.get("retry_count", 0) or 0)
        recovery_status = (
            (self.config.get("retry_policy") or {}).get(
                "recovered_status",
                "warning",
            )
            if recovered_after_retry
            else final_runtime_status
        )
        overall_status = combine_statuses(visual_status, recovery_status)
        self._summary = sanitize_payload({
            "schema_version": "1.1",
            "result_type": "page_summary",
            "site": self.site,
            "suite": "page_health",
            "run_id": current_run_id(),
            "viewport": get_current_viewport_name(),
            "page": self.page_name,
            "case": "page_summary",
            "status": overall_status,
            "overall_status": overall_status,
            "visual_status": visual_status,
            "runtime_status": status,
            "runtime_mode": policy["runtime_mode"],
            "runtime_affects_exit_code": policy[
                "runtime_affects_exit_code"
            ],
            "runtime_fail_on_failed": policy["fail_on_failed"],
            "runtime_fail_on_warning": policy["fail_on_warning"],
            "runtime_exit_status": recovery_status,
            "runtime_score": attempt_payload["runtime_score"],
            "primary_failure_reason": attempt_payload[
                "primary_failure_reason"
            ],
            "primary_failure_type": attempt_payload[
                "primary_failure_type"
            ],
            "runtime_evidence": (
                relative_to_project(summary_path) if summary_path else None
            ),
            "runtime_attempt_evidence": (
                relative_to_project(attempt_path) if attempt_path else None
            ),
            "attempt": attempt_number,
            "attempts": evidence_summary.get("attempts", []),
            "initial_runtime_status": initial_runtime_status,
            "final_runtime_status": final_runtime_status,
            "worst_runtime_status": worst_runtime_status,
            "recovered_after_retry": recovered_after_retry,
            "retry_count": retry_count,
            "findings": attempt_payload["findings"],
            **self.request_profile,
        })
        add_result(self._summary)
        self._finalized = True
        self._print_summary()
        return self._summary

    def page_available(self):
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    def _safe_page_url(self):
        try:
            return self.page.url
        except Exception:
            return None

    def _print_summary(self):
        summary = self._summary
        findings = summary.get("findings", [])
        counts = ", ".join(
            f"{item['reason_code']}={item.get('count', 1)}"
            for item in findings
        ) or "none"
        mode_suffix = (
            " (report-only)"
            if summary.get("runtime_mode") == "report_only"
            else ""
        )
        print(
            f"Runtime Health: {summary['runtime_status'].upper()}"
            f"{mode_suffix}"
        )
        print(f"Findings: {counts}")
        print(
            "Primary: "
            f"type={summary.get('primary_failure_type') or 'none'}, "
            f"reason={summary.get('primary_failure_reason') or 'none'}"
        )
        print(
            "Evidence: "
            f"{summary.get('runtime_evidence') or 'unavailable'}"
        )
        print(f"Visual Result: {summary['visual_status'].upper()}")
        print(f"Overall Result: {summary['status'].upper()}")


def _is_terminal_main_document_status(status):
    return status in {401, 403, 429} or (
        isinstance(status, int) and status >= 500
    )


class FailOpenRuntimeHealthSession:
    """No-op fallback used when runtime instrumentation cannot initialize."""

    def __init__(self, page, site_config, page_name, error):
        self.page = page
        self.site = site_config.get("site", "unknown")
        self.page_name = page_name
        self.error = redact_text(f"{type(error).__name__}: {error}")
        self._summary = None
        self._navigation_sequence = 0
        self.config = {
            "enabled": True,
            "reporting": {
                "report_only": True,
                "affect_exit_code": False,
                "fail_on_failed": False,
                "fail_on_warning": False,
            },
        }

    def start_before_navigation(self):
        return None

    def record_navigation_attempt(self, _attempt):
        return None

    def begin_navigation(self):
        self._navigation_sequence += 1
        return {
            "attempt_offset": 0,
            "navigation_sequence": self._navigation_sequence,
        }

    @staticmethod
    def phase(_name):
        return nullcontext()

    def navigation_attempt_phase(self, _sequence_attempt):
        return self.phase("unknown")

    def complete_navigation(self, result=None, error=None):
        return None

    def record_automation_error(self, error, phase):
        return None

    def collect_after_ready(self):
        return None

    def capture_post_visual_state(self):
        return None

    def page_available(self):
        try:
            return not self.page.is_closed()
        except Exception:
            return False

    def finalize(self, visual_status="not_run"):
        if self._summary is not None:
            return self._summary
        self._summary = {
            "schema_version": "1.1",
            "result_type": "page_summary",
            "site": self.site,
            "suite": "page_health",
            "run_id": current_run_id(),
            "viewport": get_current_viewport_name(),
            "page": self.page_name,
            "case": "page_summary",
            "status": combine_statuses(visual_status, "warning"),
            "overall_status": combine_statuses(visual_status, "warning"),
            "visual_status": visual_status,
            "runtime_status": "warning",
            "runtime_mode": "report_only",
            "runtime_affects_exit_code": False,
            "runtime_fail_on_failed": False,
            "runtime_fail_on_warning": False,
            "runtime_exit_status": "warning",
            "runtime_score": 90,
            "primary_failure_reason": "runtime_collector_error",
            "primary_failure_type": "test_environment_error",
            "runtime_evidence": None,
            "runtime_attempt_evidence": None,
            "attempt": None,
            "attempts": [],
            "initial_runtime_status": "warning",
            "final_runtime_status": "warning",
            "worst_runtime_status": "warning",
            "recovered_after_retry": False,
            "retry_count": 0,
            "findings": [
                {
                    "category": "test_environment_error",
                    "severity": "warning",
                    "reason_code": "runtime_collector_error",
                    "message": "Runtime instrumentation could not initialize.",
                    "source": "runtime_health",
                    "timestamp": utc_timestamp(),
                    "count": 1,
                    "evidence": {"error": self.error},
                }
            ],
        }
        add_result(self._summary)
        print(
            "Runtime Health: WARNING (report-only) "
            f"(collector fail-open: {self.error})"
        )
        return self._summary


def collect_runtime_health_fail_open(session):
    try:
        return session.collect_after_ready()
    except Exception as error:
        print(
            "Runtime health collection degraded without stopping visual "
            f"checks: {_redacted_error(error)}"
        )
        return None


def record_runtime_error_fail_open(session, error, phase):
    try:
        session.record_automation_error(error, phase)
    except Exception as collector_error:
        print(
            "Runtime health error recording degraded without stopping "
            f"visual checks: {_redacted_error(collector_error)}"
        )


def finalize_runtime_health_fail_open(session, site, page_name, viewport):
    visual_status = "not_run"
    try:
        phase = getattr(session, "phase", None)
        phase_context = phase("finalize") if phase else nullcontext()
        with phase_context:
            session.capture_post_visual_state()
            visual_status = get_page_visual_status(
                site,
                viewport,
                page_name,
            )
            summary = session.finalize(visual_status)
        return runtime_failure_messages(summary)
    except Exception as error:
        summary = _finalize_fallback_summary(
            session,
            site,
            page_name,
            viewport,
            visual_status,
            error,
        )
        print(
            "Runtime health finalization degraded without changing the "
            f"visual result: {_redacted_error(error)}"
        )
        return runtime_failure_messages(summary)


def combine_statuses(visual_status, runtime_health_status):
    if visual_status == "failed" or runtime_health_status == "failed":
        return "failed"
    if visual_status == "not_run":
        return "warning" if runtime_health_status != "failed" else "failed"
    if visual_status == "warning" or runtime_health_status == "warning":
        return "warning"
    if visual_status == "content_changed":
        return "content_changed"
    return "passed"


def runtime_failure_messages(summary):
    if not summary.get("runtime_affects_exit_code", False):
        return []

    status = summary.get("runtime_exit_status") or summary.get(
        "runtime_status"
    )
    reason = summary.get("primary_failure_reason") or "unknown"
    reason_codes = {
        finding.get("reason_code")
        for finding in summary.get("findings", [])
    }
    if reason_codes and reason_codes.issubset(
        {
            "runtime_collector_error",
            "runtime_evidence_write_failed",
        }
    ):
        return []
    if status == "failed" and summary.get("runtime_fail_on_failed", True):
        return [f"runtime health failed: {reason}"]
    if status == "warning" and summary.get(
        "runtime_fail_on_warning",
        False,
    ):
        return [f"runtime health warning treated as failure: {reason}"]
    return []


def runtime_reporting_policy(config):
    reporting = (
        config.get("reporting", {})
        if isinstance(config, dict)
        else {}
    )
    reporting = reporting if isinstance(reporting, dict) else {}
    report_only = bool(reporting.get("report_only", True))
    affect_exit_code = bool(reporting.get("affect_exit_code", False))
    runtime_affects_exit_code = bool(
        not report_only and affect_exit_code
    )
    return {
        "runtime_mode": (
            "enforced" if runtime_affects_exit_code else "report_only"
        ),
        "runtime_affects_exit_code": runtime_affects_exit_code,
        "fail_on_failed": bool(reporting.get("fail_on_failed", True)),
        "fail_on_warning": bool(reporting.get("fail_on_warning", False)),
    }


def _finalize_fallback_summary(
    session,
    site,
    page_name,
    viewport,
    visual_status,
    error,
):
    policy = runtime_reporting_policy(getattr(session, "config", {}))
    safe_error = _redacted_error(error)
    overall_status = combine_statuses(visual_status, "warning")
    summary = {
        "schema_version": "1.1",
        "result_type": "page_summary",
        "site": site,
        "suite": "page_health",
        "run_id": current_run_id(),
        "viewport": viewport,
        "page": page_name,
        "case": "page_summary",
        "status": overall_status,
        "overall_status": overall_status,
        "visual_status": visual_status,
        "runtime_status": "warning",
        "runtime_mode": policy["runtime_mode"],
        "runtime_affects_exit_code": policy[
            "runtime_affects_exit_code"
        ],
        "runtime_fail_on_failed": policy["fail_on_failed"],
        "runtime_fail_on_warning": policy["fail_on_warning"],
        "runtime_exit_status": "warning",
        "runtime_score": 90,
        "primary_failure_reason": "runtime_finalize_failed",
        "primary_failure_type": "test_environment_error",
        "runtime_evidence": None,
        "runtime_attempt_evidence": None,
        "attempt": None,
        "attempts": [],
        "initial_runtime_status": "warning",
        "final_runtime_status": "warning",
        "worst_runtime_status": "warning",
        "recovered_after_retry": False,
        "retry_count": 0,
        "findings": [
            {
                "category": "test_environment_error",
                "severity": "warning",
                "reason_code": "runtime_finalize_failed",
                "message": "Runtime finalization degraded.",
                "source": "runtime_health",
                "timestamp": utc_timestamp(),
                "count": 1,
                "evidence": {"error": safe_error},
            }
        ],
    }
    summary = sanitize_payload(summary)
    try:
        add_result(summary)
    except Exception as add_error:
        print(
            "Runtime fallback summary could not be added: "
            f"{_redacted_error(add_error)}"
        )
    return summary


def _redacted_error(error):
    return redact_text(f"{type(error).__name__}: {error}")
