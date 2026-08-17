import hashlib
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from playwright_checks.health.execution_models import RuntimeMode
from playwright_checks.health.file_io import atomic_write_json
from playwright_checks.health.models import serialize, utc_timestamp
from playwright_checks.runtime.evidence import sanitize_payload


RUN_MANIFEST_SCHEMA_VERSION = "1.0"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "key",
    "password",
    "secret",
    "session",
    "signature",
    "sig",
    "token",
}


class SchedulerType(str, Enum):
    MANUAL = "MANUAL"
    CODEX = "CODEX"
    HERMES = "HERMES"
    JENKINS = "JENKINS"
    OTHER = "OTHER"


class TriggerType(str, Enum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    OTHER = "OTHER"


class RunLifecycleStatus(str, Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass
class RunManifest:
    run_id: str
    trigger: TriggerType
    scheduler: SchedulerType
    started_at: str
    finished_at: str | None
    mode: RuntimeMode
    sites: list[str]
    ai_enabled: bool
    transactional_safe_enabled: bool
    baseline_update_enabled: bool
    shadow_executor_enabled: bool
    config_reference: dict[str, Any]
    runtime_metadata: dict[str, Any]
    run_status: RunLifecycleStatus = RunLifecycleStatus.RUNNING
    pinned: bool = False
    artifact_types: list[str] = field(
        default_factory=lambda: [
            "visual-results",
            "health-report",
            "site-profile",
            "test-plan",
            "run-manifest",
            "run-summary",
        ]
    )
    schema_version: str = RUN_MANIFEST_SCHEMA_VERSION

    def validate(self):
        if self.schema_version != RUN_MANIFEST_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported RunManifest schema_version={self.schema_version!r}"
            )
        if not RUN_ID_PATTERN.fullmatch(str(self.run_id or "")):
            raise ValueError("run_id contains unsafe characters")
        if not isinstance(self.trigger, TriggerType):
            raise TypeError("trigger must be TriggerType")
        if not isinstance(self.scheduler, SchedulerType):
            raise TypeError("scheduler must be SchedulerType")
        if not isinstance(self.mode, RuntimeMode):
            raise TypeError("mode must be RuntimeMode")
        if not isinstance(self.run_status, RunLifecycleStatus):
            raise TypeError("run_status must be RunLifecycleStatus")
        if not isinstance(self.sites, list) or not self.sites or any(
            not isinstance(site, str) or not site.strip() for site in self.sites
        ):
            raise ValueError("sites must contain at least one site ID")
        for name in (
            "ai_enabled",
            "transactional_safe_enabled",
            "baseline_update_enabled",
            "shadow_executor_enabled",
            "pinned",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be boolean")
        if self.mode == RuntimeMode.MONITOR and self.baseline_update_enabled:
            raise ValueError("MONITOR forbids baseline updates")
        if not isinstance(self.config_reference, dict):
            raise TypeError("config_reference must be a mapping")
        if set(self.config_reference) - {"path", "sha256"}:
            raise ValueError("config_reference only accepts path and sha256")
        if not isinstance(self.runtime_metadata, dict):
            raise TypeError("runtime_metadata must be a mapping")
        if not isinstance(self.artifact_types, list) or any(
            not isinstance(value, str) or not value.strip()
            for value in self.artifact_types
        ):
            raise TypeError("artifact_types must be a list of strings")
        return self

    def to_dict(self):
        self.validate()
        payload = serialize(self)
        payload["runtime_metadata"] = safe_runtime_metadata(
            payload["runtime_metadata"]
        )
        return sanitize_payload(payload)

    @classmethod
    def from_dict(cls, payload):
        if not isinstance(payload, dict):
            raise TypeError("RunManifest payload must be a mapping")
        manifest = cls(
            run_id=str(payload.get("run_id") or ""),
            trigger=_enum(TriggerType, payload.get("trigger"), "trigger"),
            scheduler=_enum(
                SchedulerType,
                payload.get("scheduler"),
                "scheduler",
            ),
            started_at=str(payload.get("started_at") or ""),
            finished_at=(
                str(payload["finished_at"])
                if payload.get("finished_at") is not None
                else None
            ),
            mode=_enum(RuntimeMode, payload.get("mode"), "mode"),
            sites=[str(value) for value in payload.get("sites") or []],
            ai_enabled=_bool(payload.get("ai_enabled"), "ai_enabled"),
            transactional_safe_enabled=_bool(
                payload.get("transactional_safe_enabled"),
                "transactional_safe_enabled",
            ),
            baseline_update_enabled=_bool(
                payload.get("baseline_update_enabled"),
                "baseline_update_enabled",
            ),
            shadow_executor_enabled=_bool(
                payload.get("shadow_executor_enabled"),
                "shadow_executor_enabled",
            ),
            config_reference=dict(payload.get("config_reference") or {}),
            runtime_metadata=safe_runtime_metadata(
                payload.get("runtime_metadata") or {}
            ),
            run_status=_enum(
                RunLifecycleStatus,
                payload.get("run_status", "RUNNING"),
                "run_status",
            ),
            pinned=_bool(payload.get("pinned", False), "pinned"),
            artifact_types=[
                str(value) for value in payload.get("artifact_types") or []
            ],
            schema_version=str(
                payload.get("schema_version") or RUN_MANIFEST_SCHEMA_VERSION
            ),
        )
        return manifest.validate()

    def finish(self, exit_code, unsupported=False):
        self.finished_at = utc_timestamp()
        if unsupported:
            self.run_status = RunLifecycleStatus.UNSUPPORTED
        elif int(exit_code) == 0:
            self.run_status = RunLifecycleStatus.COMPLETED
        else:
            self.run_status = RunLifecycleStatus.FAILED
        self.runtime_metadata = safe_runtime_metadata(
            {**self.runtime_metadata, "legacy_exit_code": int(exit_code)}
        )
        return self.validate()


class RunManifestStore:
    def __init__(self, run_root):
        self.run_root = Path(run_root).resolve()
        self.path = self.run_root / "run-manifest.json"

    def write(self, manifest):
        if not isinstance(manifest, RunManifest):
            raise TypeError("manifest must be RunManifest")
        self.run_root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, manifest.to_dict())
        return self.path.resolve()


def build_run_manifest(
    run_id,
    site,
    scheduler=SchedulerType.MANUAL,
    trigger=TriggerType.MANUAL,
    mode=RuntimeMode.MONITOR,
    ai_enabled=False,
    transactional_safe_enabled=False,
    shadow_executor_enabled=False,
    config_path=None,
    runtime_metadata=None,
):
    config_reference = config_file_reference(config_path)
    metadata = {
        "python_version": sys.version.split()[0],
        **dict(runtime_metadata or {}),
    }
    artifact_types = [
        "visual-results",
        "health-report",
        "site-profile",
        "test-plan",
        "run-manifest",
        "run-summary",
    ]
    if shadow_executor_enabled:
        artifact_types.extend(
            [
                "shadow-check-results",
                "shadow-observations",
                "shadow-comparison",
                "shadow-history-summary",
            ]
        )
    return RunManifest(
        run_id=str(run_id),
        trigger=_coerce_enum(TriggerType, trigger),
        scheduler=_coerce_enum(SchedulerType, scheduler),
        started_at=utc_timestamp(),
        finished_at=None,
        mode=_coerce_enum(RuntimeMode, mode),
        sites=[str(site)],
        ai_enabled=bool(ai_enabled),
        transactional_safe_enabled=bool(transactional_safe_enabled),
        baseline_update_enabled=False,
        shadow_executor_enabled=bool(shadow_executor_enabled),
        config_reference=config_reference,
        runtime_metadata=safe_runtime_metadata(metadata),
        artifact_types=artifact_types,
    ).validate()


def config_file_reference(path):
    if path is None:
        return {}
    value = Path(path)
    resolved = value.resolve()
    try:
        display_path = resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        display_path = resolved.as_posix()
    if not resolved.is_file():
        return {"path": display_path}
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return {"path": display_path, "sha256": digest}


def safe_runtime_metadata(value):
    if not isinstance(value, dict):
        raise TypeError("runtime_metadata must be a mapping")

    def clean(item):
        if isinstance(item, dict):
            return {
                str(key): clean(child)
                for key, child in item.items()
                if not _sensitive_key(key)
            }
        if isinstance(item, (list, tuple)):
            return [clean(child) for child in item]
        return item

    return sanitize_payload(clean(value))


def _sensitive_key(value):
    normalized = str(value).strip().lower().replace("-", "_")
    if normalized in SENSITIVE_KEYS:
        return True
    parts = {part for part in normalized.split("_") if part}
    return bool(parts & SENSITIVE_KEYS)


def _enum(enum_type, value, path):
    try:
        return _coerce_enum(enum_type, value)
    except (TypeError, ValueError) as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{path} must be one of {allowed}") from error


def _coerce_enum(enum_type, value):
    if isinstance(value, enum_type):
        return value
    return enum_type(str(value).strip().upper())


def _bool(value, path):
    if not isinstance(value, bool):
        raise TypeError(f"{path} must be boolean")
    return value
