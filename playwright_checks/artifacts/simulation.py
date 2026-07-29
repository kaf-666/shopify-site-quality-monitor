import json
import tempfile
from pathlib import Path

from playwright_checks.artifacts.screenshot_manager import (
    ScreenshotArtifactManager,
    finalize_artifact_run,
)


IMAGE_BYTES = 256 * 1024


def run_simulation():
    with tempfile.TemporaryDirectory(prefix="artifact-retention-") as value:
        root = Path(value) / "artifacts"
        before = directory_size(root)
        manager = ScreenshotArtifactManager(
            "fixture_site",
            "collection",
            viewport="desktop",
            run_id="fixture-run",
            site_config={
                "site": "fixture_site",
                "artifacts": {
                    "screenshot_retention": {
                        "mode": "evidence_only",
                        "limits": {
                            "max_images_per_page": 6,
                            "max_mb_per_page": 50,
                            "max_mb_per_site": 200,
                            "max_mb_per_run": 1000,
                        },
                    }
                },
            },
            page_config={},
            root=root,
        )
        pending = []
        for status, count in (
            ("passed", 6),
            ("content_changed", 2),
            ("warning", 2),
            ("failed", 2),
        ):
            for index in range(count):
                case = f"{status}-{index}"
                current = manager.temporary_path(case, "current")
                diff = manager.temporary_path(case, "diff")
                _write_fixture(current)
                _write_fixture(diff)
                pending.append((case, status, current, diff))

        peak = directory_size(root)
        for case, status, current, diff in pending:
            manager.finalize_result(
                case,
                status,
                {
                    "current": str(current),
                    "diff": str(diff),
                },
                content_changes=(
                    [f"{case}_recorded"]
                    if status == "content_changed"
                    else []
                ),
            )

        _summary_path, summary = finalize_artifact_run(
            root,
            "fixture-run",
        )
        after = directory_size(root)
        return {
            "before_bytes": before,
            "peak_bytes": peak,
            "retention_bytes": after,
            "deleted_pass_images": summary[
                "deleted_passed_images"
            ],
            "retained_images": summary["total_images"],
            "dropped_by_quota": summary["dropped_by_quota"],
        }


def directory_size(path):
    root = Path(path)
    if not root.exists():
        return 0
    return sum(
        item.stat().st_size
        for item in root.rglob("*")
        if item.is_file()
    )


def _write_fixture(path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"x" * IMAGE_BYTES)


def main():
    result = run_simulation()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
