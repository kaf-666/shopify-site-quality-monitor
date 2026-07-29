from pathlib import Path

from playwright_checks.artifacts.cleanup import safe_unlink
from playwright_checks.artifacts.manifest import ArtifactManifest, load_json


def enforce_quotas(artifact_root, run_id, config):
    root = Path(artifact_root).resolve()
    run_root = root / run_id
    if not run_root.exists():
        return 0

    manifests = _load_manifests(root, run_root)
    dropped = 0
    max_page_images = int(config["limits"]["max_images_per_page"])
    max_page_bytes = _mb(config["limits"]["max_mb_per_page"])
    max_site_bytes = _mb(config["limits"]["max_mb_per_site"])
    max_run_bytes = _mb(config["limits"]["max_mb_per_run"])

    for item in manifests:
        dropped += _drop_until(
            [item],
            root,
            max_images=max_page_images,
            max_bytes=max_page_bytes,
            reason="page_quota",
        )

    for site in sorted({item["payload"].get("site") for item in manifests}):
        site_manifests = [
            item
            for item in manifests
            if item["payload"].get("site") == site
        ]
        dropped += _drop_until(
            site_manifests,
            root,
            max_bytes=max_site_bytes,
            reason="site_quota",
        )

    dropped += _drop_until(
        manifests,
        root,
        max_bytes=max_run_bytes,
        reason="run_quota",
    )
    return dropped


def _mb(value):
    return int(float(value) * 1024 * 1024)


def _load_manifests(root, run_root):
    values = []
    for path in run_root.rglob("artifact-manifest.json"):
        payload = load_json(path)
        if not isinstance(payload, dict):
            continue
        values.append(
            {
                "path": path,
                "payload": payload,
                "store": ArtifactManifest(
                    root,
                    payload.get("run_id"),
                    payload.get("site"),
                    payload.get("viewport"),
                    payload.get("page"),
                    payload.get("retention_mode", "standard"),
                ),
            }
        )
    return values


def _drop_until(
    manifests,
    root,
    max_images=None,
    max_bytes=None,
    reason="quota",
):
    dropped = 0
    while True:
        candidates = []
        image_count = 0
        byte_count = 0
        for manifest in manifests:
            payload = manifest["store"].read()
            manifest["payload"] = payload
            for image in payload.get("retained_images", []):
                candidates.append((manifest, image))
                image_count += 1
                byte_count += int(image.get("size_bytes", 0) or 0)
        image_exceeded = (
            max_images is not None and image_count > max_images
        )
        bytes_exceeded = max_bytes is not None and byte_count > max_bytes
        if not image_exceeded and not bytes_exceeded:
            break
        if not candidates:
            break

        manifest, image = max(
            candidates,
            key=lambda item: (
                int(item[1].get("priority", 99)),
                int(item[1].get("size_bytes", 0) or 0),
            ),
        )
        absolute = root / image["relative_path"]
        try:
            safe_unlink(absolute, root)
        except OSError:
            break
        payload = manifest["store"].read()
        payload["retained_images"] = [
            value
            for value in payload.get("retained_images", [])
            if value.get("relative_path") != image.get("relative_path")
        ]
        payload.setdefault("dropped_by_quota", []).append(
            {
                **image,
                "quota_reason": reason,
            }
        )
        manifest["store"].write(payload)
        dropped += 1
    return dropped
