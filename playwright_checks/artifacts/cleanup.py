import argparse
import shutil
from pathlib import Path

from playwright_checks.core.paths import artifact_root, current_run_id


def resolved_within(path, root, allow_root=False):
    candidate = Path(path).resolve()
    boundary = Path(root).resolve()
    if candidate == boundary:
        if allow_root:
            return candidate
        raise ValueError(f"Refusing to operate on boundary root: {boundary}")
    try:
        candidate.relative_to(boundary)
    except ValueError:
        raise ValueError(
            f"Refusing to operate outside artifact boundary: {candidate}"
        ) from None
    return candidate


def safe_unlink(path, boundary):
    target = resolved_within(path, boundary)
    try:
        target.unlink(missing_ok=True)
    except TypeError:
        if target.exists():
            target.unlink()


def safe_rmtree(path, boundary):
    target = resolved_within(path, boundary)
    if target.exists():
        shutil.rmtree(target)


def cleanup_current_run_temp(root=None, run_id=None):
    root_path = Path(root or artifact_root()).resolve()
    run_root = resolved_within(
        root_path / (run_id or current_run_id()),
        root_path,
    )
    removed = 0
    errors = []
    if not run_root.exists():
        return {"removed_temp_dirs": 0, "errors": []}

    for temp_dir in list(run_root.rglob(".tmp")):
        try:
            safe_rmtree(temp_dir, run_root)
            removed += 1
        except Exception as error:
            errors.append(
                {
                    "path": temp_dir.relative_to(root_path).as_posix(),
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    for staging in list(run_root.rglob(".staging-*.json")):
        try:
            safe_unlink(staging, run_root)
        except Exception as error:
            errors.append(
                {
                    "path": staging.relative_to(root_path).as_posix(),
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    return {"removed_temp_dirs": removed, "errors": errors}


def cleanup_old_runs(root=None, keep_run_id=None, run_pattern=None):
    root_path = Path(root or artifact_root()).resolve()
    keep = str(keep_run_id or current_run_id())
    removed = []
    if not root_path.exists():
        return removed
    for child in root_path.iterdir():
        if not child.is_dir() or child.name == keep:
            continue
        if run_pattern:
            if not child.match(run_pattern):
                continue
        elif not _looks_like_artifact_run(child):
            continue
        safe_rmtree(child, root_path)
        removed.append(child.name)
    return removed


def _looks_like_artifact_run(path):
    candidate = Path(path)
    return (
        (candidate / "artifact-summary.json").is_file()
        or next(
            candidate.rglob("artifact-manifest.json"),
            None,
        )
        is not None
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Safely clean screenshot artifact workspace data."
    )
    parser.add_argument("--keep-run")
    parser.add_argument("--run-pattern")
    parser.add_argument("--current-run-temp", action="store_true")
    args = parser.parse_args(argv)
    if args.current_run_temp:
        result = cleanup_current_run_temp(run_id=args.keep_run)
        print(
            "artifact_temp_cleanup "
            f"removed={result['removed_temp_dirs']} "
            f"errors={len(result['errors'])}"
        )
        return 0
    removed = cleanup_old_runs(
        keep_run_id=args.keep_run,
        run_pattern=args.run_pattern,
    )
    print(f"artifact_old_run_cleanup removed={len(removed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
