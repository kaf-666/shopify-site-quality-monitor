import argparse
import os
import subprocess
import sys
from pathlib import Path


os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


SITE_CONFIG_ENV = "VISUAL_SITE_CONFIG"
VIEWPORT_ENV = "VISUAL_VIEWPORT"
PAGE_ENV = "VISUAL_PAGE"
ALL_VALUE = "all"
VIEWPORT_CHOICES = ("desktop", "mobile", ALL_VALUE)
PAGE_CHOICES = ("home", "collection", "product", ALL_VALUE)


def site_config_choices():
    sites_dir = Path(__file__).resolve().parent / "configs" / "sites"
    if not sites_dir.exists():
        return []

    names = {
        path.stem
        for pattern in ("*.yaml", "*.yml")
        for path in sites_dir.glob(pattern)
    }
    return sorted(names)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Run Playwright visual regression checks."
    )
    site_kwargs = {
        "help": "Site config name under configs/sites, for example mondressy_US."
    }
    choices = site_config_choices()
    if choices:
        site_kwargs["choices"] = choices

    parser.add_argument("--site", **site_kwargs)
    parser.add_argument(
        "--viewport",
        choices=VIEWPORT_CHOICES,
        help="Viewport to run. Use all to run the configured default viewport list.",
    )
    parser.add_argument(
        "--page",
        choices=PAGE_CHOICES,
        help="Page suite to run. Use all to run home, collection, and product.",
    )
    return parser.parse_args(argv)


def apply_cli_args(args):
    if args.site:
        os.environ[SITE_CONFIG_ENV] = args.site

    if args.viewport:
        if args.viewport == ALL_VALUE:
            os.environ.pop(VIEWPORT_ENV, None)
        else:
            os.environ[VIEWPORT_ENV] = args.viewport

    if args.page and args.page != ALL_VALUE:
        os.environ[PAGE_ENV] = args.page
    else:
        os.environ.pop(PAGE_ENV, None)


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


def main(argv=None):
    args = parse_args(argv)
    apply_cli_args(args)

    if not run_stage("Playwright visual regression", ".", "playwright_checks.runner.main"):
        sys.exit(1)

    print("\n" + "=" * 60)
    print("All checks passed")
    print("=" * 60)


if __name__ == "__main__":
    main()
