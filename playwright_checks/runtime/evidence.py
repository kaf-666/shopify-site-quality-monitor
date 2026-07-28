import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright_checks.core.paths import artifact_page_dir, relative_to_project


SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "code",
    "credential",
    "key",
    "password",
    "secret",
    "session",
    "signature",
    "sig",
    "token",
}
URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
AUTH_SCHEME_PATTERN = re.compile(
    r"(?i)(?P<prefix>\bauthorization\s*[:=]\s*)?"
    r"(?P<scheme>bearer|basic)\s+"
    r"(?P<credential>[A-Za-z0-9._~+/=-]+)"
)
SENSITIVE_PAIR_PATTERN = re.compile(
    r"""(?ix)
    (?<![A-Za-z0-9_])
    (?P<prefix>
        ["']?
        (?:access[_-]?token|api[_-]?key|apikey|auth|authorization|
           code|credential|key|password|secret|session|signature|sig|token)
        ["']?
        \s*[:=]\s*
    )
    (?P<quote>["']?)
    (?P<value>[^\s"'`,;&}\]]+)
    (?P=quote)
    """
)


def redact_url(value):
    if not value:
        return value

    try:
        parts = urlsplit(str(value))
    except ValueError:
        return str(value)

    if not parts.scheme or not parts.netloc:
        return str(value)

    redacted_query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        if key.strip().lower() in SENSITIVE_QUERY_KEYS:
            redacted_query.append((key, "[REDACTED]"))
        else:
            redacted_query.append((key, item))

    raw_hostname = parts.hostname or ""
    hostname = (
        f"[{raw_hostname}]"
        if ":" in raw_hostname and not raw_hostname.startswith("[")
        else raw_hostname
    )
    try:
        port = parts.port
    except ValueError:
        port = None
    host_port = f"{hostname}:{port}" if port is not None else hostname
    if parts.username is not None or parts.password is not None:
        host_port = f"[REDACTED]@{host_port}"

    return urlunsplit(
        (
            parts.scheme,
            host_port,
            parts.path,
            urlencode(redacted_query, doseq=True),
            "",
        )
    )


def redact_text(value):
    if value is None:
        return None

    text = str(value)
    text = URL_PATTERN.sub(lambda match: redact_url(match.group(0)), text)
    text = AUTH_SCHEME_PATTERN.sub(
        lambda match: (
            f"{match.group('prefix') or ''}"
            f"{match.group('scheme')} [REDACTED]"
        ),
        text,
    )
    return SENSITIVE_PAIR_PATTERN.sub(
        lambda match: (
            f"{match.group('prefix')}"
            f"{match.group('quote')}[REDACTED]{match.group('quote')}"
        ),
        text,
    )


def sanitize_payload(value):
    if isinstance(value, dict):
        return {str(key): sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def event_fingerprint(event):
    stable = {
        "event_type": event.get("event_type"),
        "level": event.get("level"),
        "message": event.get("message"),
        "url": event.get("url") or event.get("source_url"),
        "status": event.get("status"),
        "failure": event.get("failure"),
        "method": event.get("method"),
        "resource_type": event.get("resource_type"),
        "party": event.get("party"),
        "noise_reason": event.get("noise_reason"),
    }
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


class RuntimeEvidenceStore:
    def __init__(self, site, page_name, directory=None):
        self.site = site
        self.page_name = page_name
        self.directory = Path(
            directory or (artifact_page_dir(site, page_name) / "runtime")
        )

    def next_attempt_number(self):
        attempts = []
        if self.directory.exists():
            for path in self.directory.glob("attempt-*.json"):
                try:
                    attempts.append(int(path.stem.split("-")[-1]))
                except ValueError:
                    continue
        return max(attempts, default=0) + 1

    def write_attempt(self, payload):
        self.directory.mkdir(parents=True, exist_ok=True)
        attempt_number = self.next_attempt_number()
        path = self.directory / f"attempt-{attempt_number}.json"
        safe_payload = sanitize_payload(
            {
                **payload,
                "attempt": attempt_number,
            }
        )
        path.write_text(
            json.dumps(safe_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return attempt_number, path

    def write_summary(self, attempt_number, attempt_path, payload):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "summary.json"
        previous = {}
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                previous = {}

        attempts = [
            item
            for item in previous.get("attempts", [])
            if item.get("attempt") != attempt_number
        ]
        attempts.append(
            {
                "attempt": attempt_number,
                "status": payload.get("runtime_status"),
                "timestamp": payload.get("timestamp"),
                "primary_failure_type": payload.get("primary_failure_type"),
                "primary_failure_reason": payload.get("primary_failure_reason"),
                "evidence": relative_to_project(attempt_path),
            }
        )
        attempts.sort(key=lambda item: item.get("attempt", 0))
        statuses = [item.get("status") for item in attempts]
        initial_runtime_status = (
            attempts[0].get("status") if attempts else payload.get("runtime_status")
        )
        final_runtime_status = payload.get("runtime_status")
        worst_runtime_status = _worst_status(statuses)
        recovered_after_retry = bool(
            len(attempts) > 1
            and _status_rank(worst_runtime_status)
            > _status_rank(final_runtime_status)
        )

        safe_payload = sanitize_payload(
            {
                "schema_version": "1.1",
                "site": self.site,
                "page": self.page_name,
                "updated_at": payload.get("timestamp"),
                "runtime_status": payload.get("runtime_status"),
                "initial_runtime_status": initial_runtime_status,
                "final_runtime_status": final_runtime_status,
                "worst_runtime_status": worst_runtime_status,
                "recovered_after_retry": recovered_after_retry,
                "retry_count": max(0, len(attempts) - 1),
                "runtime_score": payload.get("runtime_score"),
                "runtime_mode": payload.get("runtime_mode"),
                "runtime_affects_exit_code": payload.get(
                    "runtime_affects_exit_code",
                    False,
                ),
                "runtime_fail_on_failed": payload.get(
                    "runtime_fail_on_failed",
                    True,
                ),
                "runtime_fail_on_warning": payload.get(
                    "runtime_fail_on_warning",
                    False,
                ),
                "primary_failure_type": payload.get("primary_failure_type"),
                "primary_failure_reason": payload.get("primary_failure_reason"),
                "event_counts": payload.get("event_counts", {}),
                "dropped_event_counts": payload.get(
                    "dropped_event_counts",
                    {},
                ),
                "findings": payload.get("findings", []),
                "request_header_injection": payload.get(
                    "request_header_injection",
                ),
                "http_cache_mode": payload.get("http_cache_mode"),
                "run_profile": payload.get("run_profile"),
                "attempts": attempts,
            }
        )
        path.write_text(
            json.dumps(safe_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path, safe_payload


def _worst_status(statuses):
    order = {"passed": 0, "warning": 1, "failed": 2}
    values = [status for status in statuses if status in order]
    if not values:
        return "passed"
    return max(values, key=order.get)


def _status_rank(status):
    return {"disabled": 0, "passed": 0, "warning": 1, "failed": 2}.get(
        status,
        0,
    )
