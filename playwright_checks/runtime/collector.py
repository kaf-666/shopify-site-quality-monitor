from collections import OrderedDict, defaultdict
from contextlib import contextmanager
from ipaddress import ip_address
from urllib.parse import urlsplit

from playwright_checks.runtime.evidence import (
    event_fingerprint,
    redact_text,
    redact_url,
)
from playwright_checks.runtime.models import RuntimeEvent


COMMON_TWO_LEVEL_SUFFIXES = {
    "co.uk",
    "com.au",
    "com.br",
    "com.cn",
    "com.hk",
    "com.sg",
    "co.jp",
    "co.nz",
    "co.za",
    "com.mx",
}
RUNTIME_PHASES = {
    "unknown",
    "navigation",
    "navigation_retry",
    "variant_interaction",
    "finalize",
}


def _root_domain(hostname):
    host = (hostname or "").lower().strip(".")
    if not host:
        return ""
    if host in ("localhost", "127.0.0.1", "::1"):
        return host
    try:
        ip_address(host)
        return host
    except ValueError:
        pass

    labels = host.split(".")
    if len(labels) <= 2:
        return host
    suffix = ".".join(labels[-2:])
    if suffix in COMMON_TWO_LEVEL_SUFFIXES and len(labels) >= 3:
        return ".".join(labels[-3:])
    if len(labels[-1]) == 2:
        return None
    return suffix


class RuntimeEventCollector:
    def __init__(self, page, requested_url, config=None):
        self.page = page
        self.requested_url = requested_url
        self.config = config or {}
        self.max_events = int(self.config.get("max_events_per_category", 100))
        self.http_error_status = int(self.config.get("http_error_status", 400))
        self._events = OrderedDict()
        self._stored_by_type = defaultdict(int)
        self._dropped_by_type = defaultdict(int)
        self._listener_errors = []
        self._started = False
        self._phase = "unknown"
        self._navigation_sequence = None
        self.page_crashed = False

    @contextmanager
    def phase(self, name):
        if name not in RUNTIME_PHASES:
            raise ValueError(f"Unsupported Runtime phase: {name!r}")
        previous = self._phase
        self._phase = name
        try:
            yield self
        finally:
            self._phase = previous

    def set_navigation_sequence(self, sequence):
        self._navigation_sequence = sequence

    def start(self):
        if self._started or not self.config.get("enabled", True):
            return

        listeners = {
            "pageerror": self._on_page_error,
            "console": self._on_console,
            "requestfailed": self._on_request_failed,
            "response": self._on_response,
            "dialog": self._on_dialog,
            "crash": self._on_crash,
        }
        for event_name, callback in listeners.items():
            try:
                self.page.on(
                    event_name,
                    lambda event, handler=callback, name=event_name: self._safe_handler(
                        name,
                        handler,
                        event,
                    ),
                )
            except Exception as error:
                self._record_listener_error(event_name, error)
        self._started = True

    def _safe_handler(self, event_name, handler, event):
        try:
            handler(event)
        except Exception as error:
            self._record_listener_error(event_name, error)

    def _record_listener_error(self, event_name, error):
        if len(self._listener_errors) >= self.max_events:
            self._dropped_by_type["collector_error"] += 1
            return
        message = redact_text(f"{type(error).__name__}: {error}")[:4000]
        self._listener_errors.append(
            {
                "event": event_name,
                "message": message,
            }
        )

    def _add(self, event):
        payload = event.to_dict()
        payload["message"] = _limited_text(payload.get("message"), 4000)
        payload["stack"] = _limited_text(payload.get("stack"), 8000)
        payload["failure"] = _limited_text(payload.get("failure"), 4000)
        for key in ("url", "source_url"):
            value = redact_url(payload.get(key))
            payload[key] = value[:4000] if value else value

        fingerprint = event_fingerprint(payload)
        if fingerprint in self._events:
            stored = self._events[fingerprint]
            stored["count"] += 1
            stored["last_seen"] = payload["timestamp"]
            self._add_phase_occurrence(stored, payload["timestamp"])
            return

        event_type = payload["event_type"]
        if self._stored_by_type[event_type] >= self.max_events:
            self._dropped_by_type[event_type] += 1
            return

        payload["fingerprint"] = fingerprint
        payload["phase"] = self._phase
        payload["navigation_sequence"] = self._navigation_sequence
        payload["first_seen"] = payload["timestamp"]
        payload["last_seen"] = payload["timestamp"]
        payload["phase_occurrences"] = [
            self._new_phase_occurrence(payload["timestamp"])
        ]
        self._events[fingerprint] = payload
        self._stored_by_type[event_type] += 1

    def _add_phase_occurrence(self, stored, timestamp):
        for occurrence in stored["phase_occurrences"]:
            if (
                occurrence["phase"] == self._phase
                and occurrence["navigation_sequence"]
                == self._navigation_sequence
            ):
                occurrence["count"] += 1
                occurrence["last_seen"] = timestamp
                return
        stored["phase_occurrences"].append(
            self._new_phase_occurrence(timestamp)
        )

    def _new_phase_occurrence(self, timestamp):
        return {
            "phase": self._phase,
            "navigation_sequence": self._navigation_sequence,
            "first_seen": timestamp,
            "last_seen": timestamp,
            "count": 1,
        }

    def _on_page_error(self, error):
        self._add(
            RuntimeEvent(
                event_type="page_error",
                level="error",
                message=str(error),
                stack=getattr(error, "stack", None),
            )
        )

    def _on_console(self, message):
        level = getattr(message, "type", None)
        if level not in ("warning", "error"):
            return
        location = getattr(message, "location", None) or {}
        source_url = location.get("url")
        self._add(
            RuntimeEvent(
                event_type="console",
                level=level,
                message=getattr(message, "text", str(message)),
                source_url=source_url,
                line=location.get("lineNumber"),
                column=location.get("columnNumber"),
                party=self.classify_url(source_url),
            )
        )

    def _on_request_failed(self, request):
        failure = getattr(request, "failure", None)
        if isinstance(failure, dict):
            failure = failure.get("errorText")
        url = getattr(request, "url", None)
        resource_type = getattr(request, "resource_type", None)
        party = self.classify_url(url)
        noise_reason = self._request_noise_reason(
            url,
            resource_type,
            failure,
            party,
        )
        self._add(
            RuntimeEvent(
                event_type="request_failed",
                level="info" if noise_reason else "error",
                url=url,
                method=getattr(request, "method", None),
                resource_type=resource_type,
                failure=failure or "request failed",
                party=party,
                noise_reason=noise_reason,
                blocking=not bool(noise_reason),
            )
        )

    def _on_response(self, response):
        status = int(getattr(response, "status", 0) or 0)
        request = getattr(response, "request", None)
        resource_type = getattr(request, "resource_type", None)
        url = getattr(response, "url", None)
        is_document = resource_type == "document"
        if status < self.http_error_status and not is_document:
            return
        noise_reason = None
        if (
            status >= self.http_error_status
            and resource_type != "document"
            and self._noise_config("ignore_favicon", True)
            and _is_favicon_url(url)
        ):
            noise_reason = "favicon_http_error"
        self._add(
            RuntimeEvent(
                event_type=(
                    "http_error"
                    if status >= self.http_error_status
                    else "main_document_response"
                ),
                level=(
                    "info"
                    if noise_reason or status < self.http_error_status
                    else "error"
                ),
                url=url,
                method=getattr(request, "method", None),
                resource_type=resource_type,
                status=status,
                party=self.classify_url(url),
                noise_reason=noise_reason,
                blocking=not bool(noise_reason),
            )
        )

    def _on_dialog(self, dialog):
        self._add(
            RuntimeEvent(
                event_type="dialog",
                level="warning",
                message=getattr(dialog, "message", None),
                dialog_type=getattr(dialog, "type", None),
            )
        )
        try:
            dialog.dismiss()
        except Exception as error:
            self._record_listener_error("dialog.dismiss", error)

    def _on_crash(self, _page):
        self.page_crashed = True
        self._add(
            RuntimeEvent(
                event_type="page_crash",
                level="critical",
                message="Playwright page crashed",
            )
        )

    def classify_url(self, value):
        if not value:
            return "unknown"

        url = str(value)
        event_host = _hostname(url)
        if not event_host:
            return "unknown"

        for pattern in self.config.get("first_party_patterns", []) or []:
            if _hostname_matches(event_host, pattern):
                return "first_party"
        for pattern in self.config.get("third_party_patterns", []) or []:
            if _hostname_matches(event_host, pattern):
                return "third_party"

        requested_host = _hostname(self.requested_url)
        if not requested_host:
            return "unknown"
        if (
            requested_host == event_host
            or event_host.endswith(f".{requested_host}")
            or requested_host.endswith(f".{event_host}")
        ):
            return "first_party"
        requested_root = _root_domain(requested_host)
        event_root = _root_domain(event_host)
        if requested_root and event_root:
            return (
                "first_party"
                if requested_root == event_root
                else "third_party"
            )
        return "unknown"

    def _request_noise_reason(
        self,
        url,
        resource_type,
        failure,
        party,
    ):
        lowered_failure = str(failure or "").lower()
        aborted = any(
            marker in lowered_failure
            for marker in (
                "err_aborted",
                "ns_binding_aborted",
                "aborted",
                "cancelled",
                "canceled",
                "blocked_by_client",
                "blockedbyclient",
            )
        )
        if (
            resource_type != "document"
            and self._noise_config("ignore_favicon", True)
            and _is_favicon_url(url)
        ):
            return "favicon_request_failed"
        if (
            aborted
            and party == "third_party"
            and self._noise_config("ignore_third_party_aborted", True)
        ):
            return "third_party_aborted"
        if (
            aborted
            and resource_type == "image"
            and self._noise_config("ignore_image_aborted", True)
        ):
            return "image_aborted"
        return None

    def _noise_config(self, key, default):
        return bool(
            (self.config.get("network_noise") or {}).get(key, default)
        )

    def snapshot(self):
        return {
            "events": list(self._events.values()),
            "event_counts": dict(self._stored_by_type),
            "dropped_event_counts": dict(self._dropped_by_type),
            "collector_errors": list(self._listener_errors),
            "page_crashed": self.page_crashed,
        }


def _limited_text(value, limit):
    if value is None:
        return None
    return redact_text(value)[:limit]


def _hostname(value):
    try:
        return (urlsplit(str(value)).hostname or "").lower().strip(".")
    except ValueError:
        return ""


def _hostname_matches(hostname, pattern):
    normalized = str(pattern or "").lower().strip().strip(".")
    if normalized.startswith("*."):
        normalized = normalized[2:]
    return bool(
        normalized
        and (
            hostname == normalized
            or hostname.endswith(f".{normalized}")
        )
    )


def _is_favicon_url(value):
    try:
        return "favicon" in (urlsplit(str(value or "")).path or "").lower()
    except ValueError:
        return False
