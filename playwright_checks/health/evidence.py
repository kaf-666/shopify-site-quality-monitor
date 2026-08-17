import hashlib
import json
from pathlib import Path

from playwright_checks.core.config_loader import PROJECT_ROOT
from playwright_checks.runtime.evidence import sanitize_payload
from playwright_checks.health.models import (
    EVIDENCE_RANK,
    EvidenceItem,
    EvidenceLevel,
    EvidenceType,
)


RUNTIME_EVENT_TYPES = {
    "page_error": {"page_error"},
    "console_error": {"console"},
    "console_warning": {"console"},
    "third_party_error": {"request_failed", "http_error", "console"},
    "network_error": {"request_failed", "http_error"},
    "first_party_server_error": {"http_error"},
    "first_party_request_failed": {"request_failed"},
    "partial_render_failure": {"page_error", "request_failed", "http_error"},
    "navigation_failed": {"request_failed", "http_error"},
    "navigation_retry_recovered": {"request_failed", "http_error"},
}


class HealthEvidenceBuilder:
    def __init__(self, project_root=PROJECT_ROOT):
        self.project_root = Path(project_root).resolve()
        self._attempt_cache = {}

    def load_runtime_attempt(self, page_summary):
        reference = (page_summary or {}).get("runtime_attempt_evidence")
        if not reference:
            return {}
        cache_key = str(reference)
        if cache_key in self._attempt_cache:
            return self._attempt_cache[cache_key]
        path = self._resolve(reference)
        payload = {}
        if path and path.is_file():
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(parsed, dict):
                    payload = parsed
            except (OSError, json.JSONDecodeError):
                payload = {}
        self._attempt_cache[cache_key] = payload
        return payload

    def for_result(self, result, attempt=None):
        evidence = self._structured_evidence(result.get("evidence"))
        attempt = attempt or {}
        navigation = attempt.get("navigation", {}) or {}
        final_url = navigation.get("final_url") or navigation.get("requested_url")
        if final_url:
            evidence.append(
                self._item(
                    EvidenceType.URL,
                    EvidenceLevel.LOW,
                    "navigation",
                    "Observed page URL",
                    details={"url": final_url},
                )
            )

        status = navigation.get("status", navigation.get("main_document_status"))
        status_code = _http_status(status)
        if status_code is not None:
            level = (
                EvidenceLevel.HIGH
                if status_code >= 400
                else EvidenceLevel.MEDIUM
            )
            evidence.append(
                self._item(
                    EvidenceType.HTTP,
                    level,
                    "navigation",
                    f"Main document returned HTTP {status_code}",
                    details={"status": status_code, "url": final_url},
                )
            )

        path_types = (
            ("diff", EvidenceType.VISUAL_DIFF, EvidenceLevel.HIGH, "Visual diff image"),
            ("current", EvidenceType.SCREENSHOT, EvidenceLevel.MEDIUM, "Current screenshot"),
            ("trace", EvidenceType.TRACE, EvidenceLevel.HIGH, "Playwright trace"),
        )
        for key, evidence_type, level, summary in path_types:
            reference = result.get(key)
            if reference and self._reference_exists(reference):
                evidence.append(
                    self._item(
                        evidence_type,
                        level,
                        "result_artifact",
                        summary,
                        reference=str(reference),
                    )
                )

        ratio = result.get("ratio")
        if ratio is not None:
            evidence.append(
                self._item(
                    EvidenceType.METRIC,
                    EvidenceLevel.MEDIUM,
                    "visual_compare",
                    "Measured visual difference",
                    details={
                        "ratio": ratio,
                        "threshold": result.get("threshold"),
                        "warning_threshold": result.get("warning_threshold"),
                    },
                )
            )

        structural_issues = result.get("structural_issues") or []
        if structural_issues:
            evidence.append(
                self._item(
                    EvidenceType.DOM,
                    EvidenceLevel.MEDIUM,
                    "structural_check",
                    "Semantic structure checks failed",
                    details={
                        "issues": structural_issues,
                        "diagnostics": result.get("structural_diagnostics", {}),
                    },
                )
            )

        if result.get("actual_count") is not None:
            evidence.append(
                self._item(
                    EvidenceType.METRIC,
                    EvidenceLevel.MEDIUM,
                    "content_check",
                    "Observed count differs from reference",
                    details={
                        "expected": result.get("reference_count"),
                        "actual": result.get("actual_count"),
                    },
                )
            )

        messages = list(result.get("messages") or [])
        if result.get("error"):
            messages.append(result["error"])
        if messages:
            evidence.append(
                self._item(
                    EvidenceType.LOG,
                    EvidenceLevel.LOW,
                    "deterministic_check",
                    "Deterministic check reported an error",
                    details={"messages": messages[:20]},
                )
            )
        return _dedupe(evidence)

    @staticmethod
    def _structured_evidence(values):
        evidence = []
        for raw in values or []:
            if not isinstance(raw, dict):
                continue
            try:
                evidence_type = EvidenceType(
                    str(raw.get("evidence_type") or "").upper()
                )
                level = EvidenceLevel(str(raw.get("level") or "").upper())
            except ValueError:
                continue
            evidence_id = str(raw.get("evidence_id") or "").strip()
            source = str(raw.get("source") or "").strip()
            summary = str(raw.get("summary") or "").strip()
            if not evidence_id or not source or not summary:
                continue
            evidence.append(
                EvidenceItem(
                    evidence_id=evidence_id,
                    evidence_type=evidence_type,
                    level=level,
                    source=source,
                    summary=summary,
                    reference=(
                        str(raw["reference"])
                        if raw.get("reference") is not None
                        else None
                    ),
                    details=sanitize_payload(dict(raw.get("details") or {})),
                    timestamp=str(raw.get("timestamp") or ""),
                )
            )
        return evidence

    def for_runtime_finding(self, finding, page_summary, attempt=None):
        evidence = []
        attempt = attempt or self.load_runtime_attempt(page_summary)
        navigation = attempt.get("navigation", {}) or {}
        reason = str(finding.get("reason_code") or "unknown")
        finding_evidence = finding.get("evidence", {}) or {}
        final_url = navigation.get("final_url") or navigation.get("requested_url")
        status = finding_evidence.get("status", navigation.get("status"))

        if final_url:
            evidence.append(
                self._item(
                    EvidenceType.URL,
                    EvidenceLevel.LOW,
                    "runtime_navigation",
                    "Runtime observation URL",
                    details={"url": final_url},
                )
            )
        status_code = _http_status(status)
        if status_code is not None:
            evidence.append(
                self._item(
                    EvidenceType.HTTP,
                    EvidenceLevel.HIGH
                    if status_code >= 400
                    else EvidenceLevel.MEDIUM,
                    "runtime_navigation",
                    f"Observed HTTP {status_code}",
                    details={"status": status_code, "url": final_url},
                )
            )

        missing = finding_evidence.get("missing") or []
        if missing:
            critical = (
                (attempt.get("pre_visual_health") or {}).get("critical_elements")
                or []
            )
            matched = [item for item in critical if item.get("name") in missing]
            evidence.append(
                self._item(
                    EvidenceType.SELECTOR,
                    EvidenceLevel.MEDIUM,
                    "runtime_dom_probe",
                    "Configured critical elements were not satisfied",
                    details={"missing": missing, "selector_probes": matched},
                )
            )

        event_types = RUNTIME_EVENT_TYPES.get(reason, set())
        events = [
            event
            for event in (attempt.get("events") or [])
            if event.get("event_type") in event_types
        ]
        if reason == "third_party_error":
            events = [event for event in events if event.get("party") == "third_party"]
        if reason in ("network_error", "first_party_server_error", "first_party_request_failed"):
            events = [event for event in events if event.get("party") != "third_party"]
        for event in events[:8]:
            evidence_type = (
                EvidenceType.CONSOLE
                if event.get("event_type") in ("console", "page_error")
                else EvidenceType.NETWORK
            )
            level = EvidenceLevel.MEDIUM
            event_status = _http_status(event.get("status"))
            if (
                evidence_type == EvidenceType.NETWORK
                and event.get("party") == "first_party"
                and event_status is not None
                and event_status >= 500
            ):
                level = EvidenceLevel.HIGH
            evidence.append(
                self._item(
                    evidence_type,
                    level,
                    "runtime_event",
                    _event_summary(event),
                    details=event,
                )
            )

        terminal = (attempt.get("pre_visual_health") or {}).get(
            "terminal_page_evidence",
            {},
        )
        screenshot = terminal.get("screenshot")
        if screenshot and self._reference_exists(screenshot):
            evidence.append(
                self._item(
                    EvidenceType.SCREENSHOT,
                    EvidenceLevel.HIGH,
                    "terminal_page",
                    "Terminal page screenshot",
                    reference=screenshot,
                    details={key: value for key, value in terminal.items() if key != "screenshot"},
                )
            )

        attempt_reference = (page_summary or {}).get("runtime_attempt_evidence")
        if attempt_reference and self._reference_exists(attempt_reference):
            evidence.append(
                self._item(
                    EvidenceType.RUNTIME_ARTIFACT,
                    EvidenceLevel.LOW,
                    "runtime_evidence",
                    "Redacted runtime attempt artifact",
                    reference=attempt_reference,
                )
            )

        if finding_evidence and not evidence:
            evidence.append(
                self._item(
                    EvidenceType.LOG,
                    EvidenceLevel.LOW,
                    "runtime_finding",
                    "Runtime finding supplied structured evidence",
                    details=finding_evidence,
                )
            )
        return _dedupe(evidence)

    def for_recovered_retry(self, page_summary, attempt=None):
        evidence = self.for_result(page_summary, attempt)
        attempts = list((page_summary or {}).get("attempts") or [])
        retry_count = int((page_summary or {}).get("retry_count", 0) or 0)
        if retry_count or attempts:
            evidence.append(
                self._item(
                    EvidenceType.METRIC,
                    EvidenceLevel.MEDIUM,
                    "runtime_retry_summary",
                    "Runtime retry history shows recovery",
                    details={
                        "retry_count": retry_count,
                        "attempts": attempts[:10],
                    },
                )
            )
        for item in attempts[:3]:
            reference = item.get("evidence") if isinstance(item, dict) else None
            if reference and self._reference_exists(reference):
                evidence.append(
                    self._item(
                        EvidenceType.RUNTIME_ARTIFACT,
                        EvidenceLevel.LOW,
                        "runtime_retry_attempt",
                        "Retained runtime retry attempt",
                        reference=reference,
                    )
                )
        return _dedupe(evidence)

    def _item(
        self,
        evidence_type,
        level,
        source,
        summary,
        reference=None,
        details=None,
    ):
        safe_details = sanitize_payload(dict(details or {}))
        stable = json.dumps(
            {
                "type": evidence_type.value,
                "source": source,
                "summary": summary,
                "reference": reference,
                "details": safe_details,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        evidence_id = "ev-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:12]
        return EvidenceItem(
            evidence_id=evidence_id,
            evidence_type=evidence_type,
            level=level,
            source=source,
            summary=str(summary),
            reference=str(reference) if reference else None,
            details=safe_details,
        )

    def _resolve(self, reference):
        if not reference:
            return None
        path = Path(str(reference))
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    def _reference_exists(self, reference):
        path = self._resolve(reference)
        return bool(path and path.is_file())


def evidence_level(items):
    if not items:
        return EvidenceLevel.NONE
    highest = max((item.level for item in items), key=lambda value: EVIDENCE_RANK[value])
    medium_count = sum(1 for item in items if EVIDENCE_RANK[item.level] >= 2)
    if highest == EvidenceLevel.MEDIUM and medium_count >= 2:
        return EvidenceLevel.HIGH
    return highest


def _http_status(value):
    try:
        status = int(value)
    except (TypeError, ValueError):
        return None
    return status if 100 <= status <= 599 else None


def _event_summary(event):
    event_type = event.get("event_type", "runtime_event")
    if event_type == "http_error":
        return f"{event.get('party', 'unknown')} HTTP {event.get('status')} request failed"
    if event_type == "request_failed":
        return f"{event.get('party', 'unknown')} request failed"
    if event_type == "page_error":
        return "Uncaught page exception"
    return f"Console {event.get('level', 'event')}"


def _dedupe(items):
    selected = {}
    for item in items:
        selected[item.evidence_id] = item
    return list(selected.values())
