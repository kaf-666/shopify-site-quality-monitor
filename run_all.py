import os
import subprocess
import sys


os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def run_stage(label, folder, script):
    """Run one test stage and return whether it passed."""
    print("\n" + "=" * 60)
    print(f"{' STAGE: ' + label + ' ':=^60}")
    print("=" * 60 + "\n")
    sys.stdout.flush()

    result = subprocess.run(
        [sys.executable, "-u", "-m", script],
        cwd=folder,
        env=os.environ.copy(),
    )

    if result.returncode != 0:
        print(f"\nFAILED: {label}")
        return False

    print(f"\nPASSED: {label}")
    return True


def main():
    if not run_stage("Playwright visual regression", ".", "playwright_checks.runner.main"):
        sys.exit(1)

    print("\n" + "=" * 60)
    print("All checks passed")
    print("=" * 60)


if __name__ == "__main__":
    main()
