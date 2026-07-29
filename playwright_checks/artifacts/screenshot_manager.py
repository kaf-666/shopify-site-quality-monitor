import errno
import os
import shutil
import uuid
from pathlib import Path

from playwright_checks.artifacts.cleanup import (
    cleanup_current_run_temp,
    safe_unlink,
)
from playwright_checks.artifacts.manifest import (
    ArtifactManifest,
    atomic_write_json,
    build_artifact_summary,
)
from playwright_checks.artifacts.quota import enforce_quotas
from playwright_checks.artifacts.retention import (
    RetentionDecision,
    retention_decision,
    screenshot_retention_config,
)
from playwright_checks.core.config_loader import (
    get_page_config,
    load_site_config,
)
from playwright_checks.core.paths import artifact_root, current_run_id
from playwright_checks.core.viewport import get_current_viewport_name


def safe_move(source, target):
    source_path = Path(source)
    target_path = Path(target)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source_path, target_path)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        try:
            shutil.copy2(source_path, target_path)
        except Exception:
            raise error from None
        source_path.unlink()
    return target_path


class ScreenshotArtifactManager:
    def __init__(
        self,
        site,
        page,
        viewport=None,
        run_id=None,
        site_config=None,
        page_config=None,
        root=None,
    ):
        self.root = Path(root or artifact_root()).resolve()
        self.run_id = str(run_id or current_run_id())
        self.site = site
        self.viewport = viewport or get_current_viewport_name()
        self.page = page
        if site_config is None:
            try:
                site_config = load_site_config(site)
            except (FileNotFoundError, KeyError):
                site_config = {"site": site}
        if page_config is None:
            try:
                page_config = get_page_config(page, site_config)
            except (KeyError, ValueError):
                page_config = {}
        self.site_config = site_config
        self.page_config = page_config
        self.config = screenshot_retention_config(
            site_config,
            page_config,
        )
        self.page_root = (
            self.root
            / self.run_id
            / self.site
            / self.viewport
            / self.page
        )
        self.temp_root = self.page_root / ".tmp"
        self.manifest = ArtifactManifest(
            self.root,
            self.run_id,
            self.site,
            self.viewport,
            self.page,
            self.config["mode"],
        )
        self._failure_context_candidates = {}

    def temporary_path(self, case, artifact_type="capture", attempt=1):
        safe_case = _safe_name(case)
        safe_type = _safe_name(artifact_type)
        directory = self.temp_root / f"attempt-{int(attempt)}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / (
            f"{safe_case}-attempt-{int(attempt)}-"
            f"{uuid.uuid4().hex}-{safe_type}.png"
        )

    def compare(
        self,
        comparator,
        baseline,
        current,
        diff,
        **options,
    ):
        return comparator(
            str(baseline),
            str(current),
            str(diff),
            **options,
        )

    def capture_page(self, page, path, full_page=False):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target), full_page=full_page)
        return target

    def capture_element(self, element, path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        element.screenshot(path=str(target))
        return target

    def finalize_result(
        self,
        case,
        status,
        paths=None,
        artifact_type=None,
        content_changes=None,
        structural_status="passed",
    ):
        values = dict(paths or {})
        artifact_type = artifact_type or _artifact_type(case)
        decision = retention_decision(
            self.config,
            status,
            artifact_type,
            case,
        )
        if (
            str(status).lower() == "baseline_missing"
            and any(
                item.get("visual_status") == "baseline_missing"
                for item in self.manifest.read().get(
                    "retained_images",
                    [],
                )
            )
        ):
            decision = RetentionDecision(
                False,
                False,
                "baseline_missing_already_retained",
                6,
            )
        if self._should_defer_failure_context(
            case,
            status,
            artifact_type,
            values,
        ):
            self._failure_context_candidates[case] = {
                "artifact_type": artifact_type,
                "status": str(status).lower(),
                "current": values.get("current"),
                "diff": values.get("diff"),
            }
            values["current"] = None
            values["diff"] = None
            self.manifest.update_case(
                case,
                [],
                [],
                content_changes or [],
            )
            return values, {
                "retained": False,
                "retention_reason": (
                    "content_change_recorded"
                    if str(status).lower() == "content_changed"
                    else "passed_cleanup"
                ),
                "structural_status": structural_status,
                "content_changes": list(content_changes or []),
                "affects_exit_code": False,
                "dropped_by_quota": 0,
            }
        retained = []
        deleted = []
        cleanup_errors = []

        current = values.get("current")
        final_current = self._retain_or_delete(
            case,
            "current",
            current,
            decision.keep_current,
            status,
            artifact_type,
            decision,
            retained,
            deleted,
            cleanup_errors,
        )
        diff = values.get("diff")
        final_diff = self._retain_or_delete(
            case,
            "diff",
            diff,
            decision.keep_diff,
            status,
            artifact_type,
            decision,
            retained,
            deleted,
            cleanup_errors,
        )
        values["current"] = str(final_current) if final_current else None
        values["diff"] = str(final_diff) if final_diff else None
        self.manifest.update_case(
            case,
            retained,
            deleted,
            content_changes or [],
            cleanup_errors=cleanup_errors,
        )

        dropped_count = 0
        try:
            dropped_count = enforce_quotas(
                self.root,
                self.run_id,
                self.config,
            )
        except Exception as error:
            cleanup_errors.append(
                {
                    "path": None,
                    "error": f"quota: {type(error).__name__}: {error}",
                }
            )
            payload = self.manifest.read()
            payload.setdefault("temporary_cleanup_errors", []).extend(
                cleanup_errors
            )
            self.manifest.write(payload)
        for key in ("current", "diff"):
            value = values.get(key)
            if value and not Path(value).is_file():
                values[key] = None

        metadata = {
            "retained": bool(values.get("current") or values.get("diff")),
            "retention_reason": decision.reason,
            "structural_status": structural_status,
            "content_changes": list(content_changes or []),
            "affects_exit_code": str(status).lower() == "failed",
            "dropped_by_quota": dropped_count,
        }
        return values, metadata

    def finalize_page(self, has_failure=False):
        candidates = dict(self._failure_context_candidates)
        self._failure_context_candidates.clear()
        if not candidates:
            return

        selected = None
        if has_failure:
            for preferred in ("global", "first_screen"):
                if preferred in candidates:
                    selected = preferred
                    break

        for case, candidate in candidates.items():
            source = Path(candidate.get("current") or "")
            diff = Path(candidate.get("diff") or "")
            if case == selected and source.is_file():
                destination = (
                    self.page_root
                    / "current"
                    / f"{_safe_name(case)}-failure-context.png"
                )
                safe_move(source, destination)
                self.manifest.update_case(
                    f"{case}_failure_context",
                    [
                        {
                            "case": f"{case}_failure_context",
                            "artifact_type": candidate["artifact_type"],
                            "visual_status": "failed",
                            "relative_path": _relative(
                                destination,
                                self.root,
                            ),
                            "size_bytes": destination.stat().st_size,
                            "retention_reason": "failure_context",
                            "priority": (
                                2 if case == "global" else 3
                            ),
                        }
                    ],
                    [],
                    [],
                )
            else:
                self._delete_deferred_pass(
                    case,
                    candidate["artifact_type"],
                    source,
                    candidate["status"] in ("pass", "passed"),
                )
            if diff.is_file():
                self._delete_deferred_pass(
                    case,
                    "diff",
                    diff,
                    candidate["status"] in ("pass", "passed"),
                )

        try:
            enforce_quotas(self.root, self.run_id, self.config)
        except Exception as error:
            payload = self.manifest.read()
            payload.setdefault("temporary_cleanup_errors", []).append(
                {
                    "path": None,
                    "error": (
                        "failure_context_quota: "
                        f"{type(error).__name__}: {error}"
                    ),
                }
            )
            self.manifest.write(payload)

    def cleanup_temporary(self):
        from playwright_checks.artifacts.cleanup import safe_rmtree

        if not self.temp_root.exists():
            return
        safe_rmtree(self.temp_root, self.page_root)

    def discard_temporary(self, path):
        if not path:
            return True
        try:
            safe_unlink(path, self.page_root)
            return True
        except Exception as error:
            payload = self.manifest.read()
            payload.setdefault("temporary_cleanup_errors", []).append(
                {
                    "path": _relative_if_within(
                        path,
                        self.root,
                    ),
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            self.manifest.write(payload)
            return False

    def _should_defer_failure_context(
        self,
        case,
        status,
        artifact_type,
        values,
    ):
        if self.config["mode"] != "evidence_only":
            return False
        if str(status).lower() not in (
            "pass",
            "passed",
            "content_changed",
        ):
            return False
        context = self.config.get("keep_context_on_failure") or {}
        is_global = artifact_type == "global" or case == "global"
        is_first = (
            artifact_type == "first_screen"
            or case == "first_screen"
        )
        enabled = (
            (is_global and context.get("global", True))
            or (is_first and context.get("first_screen", True))
        )
        return bool(enabled and values.get("current"))

    def _delete_deferred_pass(
        self,
        case,
        artifact_type,
        path,
        record_pass=True,
    ):
        if not path.is_file():
            return
        relative = _relative(path, self.root)
        errors = []
        deleted = []
        try:
            safe_unlink(path, self.root)
            if record_pass:
                deleted.append(
                    {
                        "case": case,
                        "artifact_type": artifact_type,
                        "relative_path": relative,
                        "retention_reason": "passed_cleanup",
                    }
                )
        except Exception as error:
            errors.append(
                {
                    "path": relative,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
        self.manifest.update_case(
            f"{case}_deferred_cleanup",
            [],
            deleted,
            [],
            cleanup_errors=errors,
        )

    def capture_terminal_page(self, page, status, final_url=None):
        existing = [
            item
            for item in self.manifest.read().get("retained_images", [])
            if item.get("visual_status") == "terminal_page"
        ]
        if existing:
            return existing[0].get("relative_path")
        temp = self.temporary_path(
            "terminal-page",
            "terminal",
            attempt=1,
        )
        self.capture_page(page, temp, full_page=False)
        values, _metadata = self.finalize_result(
            "terminal_page",
            "terminal_page",
            {"current": str(temp), "diff": None},
            artifact_type="terminal_page",
            structural_status="failed",
        )
        current = values.get("current")
        if not current:
            return None
        return Path(current).resolve().relative_to(self.root).as_posix()

    def _retain_or_delete(
        self,
        case,
        kind,
        value,
        keep,
        status,
        artifact_type,
        decision,
        retained,
        deleted,
        cleanup_errors,
    ):
        if not value:
            return None
        source = Path(value)
        if not source.is_file():
            return None
        relative_source = _relative(source, self.root)
        if not keep:
            try:
                safe_unlink(source, self.root)
                if str(status).lower() == "passed":
                    deleted.append(
                        {
                            "case": case,
                            "artifact_type": kind,
                            "relative_path": relative_source,
                            "retention_reason": decision.reason,
                        }
                    )
            except Exception as error:
                cleanup_errors.append(
                    {
                        "path": relative_source,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
            return None

        destination = (
            self.page_root
            / kind
            / f"{_safe_name(case)}.png"
        )
        if source.resolve() != destination.resolve():
            safe_move(source, destination)
        retained.append(
            {
                "case": case,
                "artifact_type": (
                    artifact_type if kind == "current" else "diff"
                ),
                "visual_status": str(status).lower(),
                "relative_path": _relative(destination, self.root),
                "size_bytes": destination.stat().st_size,
                "retention_reason": decision.reason,
                "priority": decision.priority,
            }
        )
        return destination


def finalize_artifact_run(root=None, run_id=None, has_failure=None):
    root_path = Path(root or artifact_root()).resolve()
    selected_run = str(run_id or current_run_id())
    cleanup_config = screenshot_retention_config()
    preserve_debug_temp = cleanup_config["mode"] == "debug"
    cleanup_key = (
        "remove_temp_on_failure"
        if has_failure
        else "remove_temp_on_success"
    )
    remove_temp = bool(
        (cleanup_config.get("cleanup") or {}).get(
            cleanup_key,
            True,
        )
    )
    if preserve_debug_temp or not remove_temp:
        cleanup = {"removed_temp_dirs": 0, "errors": []}
    else:
        cleanup = cleanup_current_run_temp(root_path, selected_run)
    summary = build_artifact_summary(root_path, selected_run)
    summary["temporary_cleanup_errors"] = cleanup["errors"]
    summary["removed_temp_dirs"] = cleanup["removed_temp_dirs"]
    summary_path = root_path / selected_run / "artifact-summary.json"
    atomic_write_json(summary_path, summary)
    print(
        "Artifact retention: "
        f"retained={summary['total_images']} "
        f"bytes={summary['total_bytes']} "
        f"deleted_pass={summary['deleted_passed_images']} "
        f"dropped_by_quota={summary['dropped_by_quota']}"
    )
    return summary_path, summary


def _artifact_type(case):
    if case == "global":
        return "global"
    if case == "first_screen":
        return "first_screen"
    if case == "terminal_page":
        return "terminal_page"
    if str(case).startswith("hover_"):
        return "hover"
    return "module"


def _relative(path, root):
    resolved = Path(path).resolve()
    return resolved.relative_to(Path(root).resolve()).as_posix()


def _relative_if_within(path, root):
    try:
        return _relative(path, root)
    except ValueError:
        return None


def _safe_name(value):
    text = "".join(
        character
        if character.isalnum() or character in ("-", "_")
        else "-"
        for character in str(value)
    )
    return text.strip("-") or "artifact"
