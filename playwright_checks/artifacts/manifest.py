import json
import os
import threading
import uuid
from pathlib import Path

from playwright_checks.artifacts.cleanup import resolved_within


_MANIFEST_LOCK = threading.RLock()


def empty_manifest(run_id, site, viewport, page, retention_mode):
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "site": site,
        "viewport": viewport,
        "page": page,
        "retention_mode": retention_mode,
        "total_files": 0,
        "total_bytes": 0,
        "retained_images": [],
        "deleted_passed_images": [],
        "content_changes": [],
        "dropped_by_quota": [],
        "temporary_cleanup_errors": [],
    }


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def atomic_write_json(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".staging-{uuid.uuid4().hex}.json"
    try:
        staging.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(staging, target)
    finally:
        try:
            staging.unlink(missing_ok=True)
        except TypeError:
            if staging.exists():
                staging.unlink()


class ArtifactManifest:
    def __init__(
        self,
        artifact_root,
        run_id,
        site,
        viewport,
        page,
        retention_mode,
    ):
        self.artifact_root = Path(artifact_root).resolve()
        self.page_root = resolved_within(
            self.artifact_root / run_id / site / viewport / page,
            self.artifact_root,
        )
        self.path = self.page_root / "artifact-manifest.json"
        self.identity = (run_id, site, viewport, page, retention_mode)

    def read(self):
        payload = load_json(self.path)
        if not isinstance(payload, dict):
            payload = empty_manifest(*self.identity)
        return self._normalize(payload)

    def update_case(
        self,
        case,
        retained_images,
        deleted_images,
        content_changes,
        dropped=None,
        cleanup_errors=None,
    ):
        with _MANIFEST_LOCK:
            payload = self.read()
            payload["retained_images"] = [
                item
                for item in payload["retained_images"]
                if item.get("case") != case
            ]
            payload["retained_images"].extend(retained_images)
            payload["deleted_passed_images"].extend(deleted_images)
            payload["content_changes"] = _dedupe(
                payload["content_changes"] + list(content_changes or [])
            )
            payload["dropped_by_quota"].extend(dropped or [])
            payload["temporary_cleanup_errors"].extend(
                cleanup_errors or []
            )
            payload = self._normalize(payload)
            atomic_write_json(self.path, payload)
            return payload

    def write(self, payload):
        with _MANIFEST_LOCK:
            normalized = self._normalize(dict(payload))
            atomic_write_json(self.path, normalized)
            return normalized

    def _normalize(self, payload):
        retained = []
        for item in payload.get("retained_images", []):
            relative = item.get("relative_path")
            if not relative or Path(relative).is_absolute():
                continue
            absolute = (self.artifact_root / relative).resolve()
            try:
                absolute.relative_to(self.artifact_root)
            except ValueError:
                continue
            if not absolute.is_file():
                continue
            normalized = dict(item)
            normalized["relative_path"] = absolute.relative_to(
                self.artifact_root
            ).as_posix()
            normalized["size_bytes"] = absolute.stat().st_size
            retained.append(normalized)
        payload["retained_images"] = retained
        payload["total_files"] = len(retained)
        payload["total_bytes"] = sum(
            int(item.get("size_bytes", 0) or 0)
            for item in retained
        )
        payload.setdefault("deleted_passed_images", [])
        payload.setdefault("content_changes", [])
        payload.setdefault("dropped_by_quota", [])
        payload.setdefault("temporary_cleanup_errors", [])
        return payload


def build_artifact_summary(artifact_root, run_id):
    root = Path(artifact_root).resolve()
    run_root = resolved_within(root / run_id, root)
    manifests = []
    if run_root.exists():
        for path in run_root.rglob("artifact-manifest.json"):
            payload = load_json(path)
            if isinstance(payload, dict):
                store = ArtifactManifest(
                    root,
                    payload.get("run_id", run_id),
                    payload.get("site", "unknown"),
                    payload.get("viewport", "unknown"),
                    payload.get("page", "unknown"),
                    payload.get("retention_mode", "standard"),
                )
                manifests.append(store.write(store.read()))

    sites = {}
    pages = {}
    status_counts = {
        "passed": 0,
        "content_changed": 0,
        "warning": 0,
        "failed": 0,
        "terminal_page": 0,
    }
    total_files = 0
    total_bytes = 0
    deleted_passed = 0
    content_changes = 0
    dropped = 0
    for manifest in manifests:
        site = manifest.get("site", "unknown")
        page_key = "/".join(
            (
                site,
                manifest.get("viewport", "unknown"),
                manifest.get("page", "unknown"),
            )
        )
        page_bytes = int(manifest.get("total_bytes", 0) or 0)
        sites[site] = sites.get(site, 0) + page_bytes
        pages[page_key] = pages.get(page_key, 0) + page_bytes
        total_files += int(manifest.get("total_files", 0) or 0)
        total_bytes += page_bytes
        deleted_passed += len(
            manifest.get("deleted_passed_images", [])
        )
        content_changes += len(manifest.get("content_changes", []))
        dropped += len(manifest.get("dropped_by_quota", []))
        for item in manifest.get("retained_images", []):
            status = str(item.get("visual_status") or "").lower()
            if status in status_counts:
                status_counts[status] += 1

    largest_page = max(pages, key=pages.get) if pages else None
    largest_site = max(sites, key=sites.get) if sites else None
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "total_images": total_files,
        "total_bytes": total_bytes,
        "site_bytes": sites,
        "page_bytes": pages,
        "retained_passed": status_counts["passed"],
        "retained_content_changed": status_counts["content_changed"],
        "retained_warning": status_counts["warning"],
        "retained_failed": status_counts["failed"],
        "retained_terminal_page": status_counts["terminal_page"],
        "deleted_passed_images": deleted_passed,
        "content_change_count": content_changes,
        "dropped_by_quota": dropped,
        "largest_page": (
            {"page": largest_page, "size_bytes": pages[largest_page]}
            if largest_page
            else None
        ),
        "largest_site": (
            {"site": largest_site, "size_bytes": sites[largest_site]}
            if largest_site
            else None
        ),
    }


def _dedupe(values):
    seen = set()
    result = []
    for value in values:
        key = json.dumps(value, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
