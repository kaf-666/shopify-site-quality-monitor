import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


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
REQUIRED_PAGES = ("home", "collection", "product")


PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = PROJECT_ROOT / "configs"
SITE_CONFIG_DIR = CONFIG_DIR / "sites"
BASELINE_ROOT = PROJECT_ROOT / "baselines"


def site_config_choices():
    if not SITE_CONFIG_DIR.exists():
        return []

    names = {
        path.stem
        for pattern in ("*.yaml", "*.yml")
        for path in SITE_CONFIG_DIR.glob(pattern)
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
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate local config and baseline directories without opening a browser.",
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


def site_config_path(site_name):
    if not site_name:
        return None
    return SITE_CONFIG_DIR / f"{site_name}.yaml"


def is_valid_url(value):
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def is_selector(value):
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and all(isinstance(part, str) and part.strip() for part in value)
    )


def validate_selector(errors, path, value):
    if not is_selector(value):
        errors.append(f"{path} must be a non-empty [method, selector] pair")


def validate_module_selectors(errors, page_name, page_config):
    modules = page_config.get("modules")
    if not isinstance(modules, dict) or not modules:
        errors.append(f"pages.{page_name}.modules must be a non-empty mapping")
        return

    for module_name, selector in modules.items():
        validate_selector(errors, f"pages.{page_name}.modules.{module_name}", selector)


def validate_settings(errors):
    from playwright_checks.core.config_loader import load_settings

    settings = load_settings()
    viewports = settings.get("viewports")
    run_viewports = settings.get("run_viewports")

    if not isinstance(viewports, dict) or not viewports:
        errors.append("configs/settings.yaml viewports must be a non-empty mapping")
        return settings, []

    if not isinstance(run_viewports, list) or not run_viewports:
        errors.append("configs/settings.yaml run_viewports must be a non-empty list")
        return settings, []

    valid_run_viewports = []
    for viewport in run_viewports:
        if not isinstance(viewport, str) or not viewport.strip():
            errors.append("configs/settings.yaml run_viewports contains an empty value")
            continue
        if viewport not in viewports:
            errors.append(
                f"configs/settings.yaml run_viewports contains unknown viewport: {viewport}"
            )
            continue
        valid_run_viewports.append(viewport)

    return settings, valid_run_viewports


def selected_site_name(args, settings):
    return (
        args.site
        or os.environ.get(SITE_CONFIG_ENV)
        or settings.get("default_site")
        or "mondressy_US"
    )


def baseline_viewports(args, settings_run_viewports):
    if args.viewport and args.viewport != ALL_VALUE:
        return [args.viewport]
    return settings_run_viewports


def validate_config(args):
    from playwright_checks.core.config_loader import get_page_config, load_site_config

    errors = []
    warnings = []

    settings, run_viewports = validate_settings(errors)
    site_name = selected_site_name(args, settings)
    path = site_config_path(site_name)

    print("Config validation")
    print(f"site: {site_name}")
    if run_viewports:
        print(f"run_viewports: {', '.join(run_viewports)}")

    if not path or not path.exists():
        errors.append(f"site yaml not found: {path}")
        return finish_validation(errors, warnings)

    print(f"site yaml: {path.relative_to(PROJECT_ROOT)}")

    try:
        site_config = load_site_config(site_name)
    except Exception as exc:
        errors.append(f"failed to load site yaml: {type(exc).__name__}: {exc}")
        return finish_validation(errors, warnings)

    pages = site_config.get("pages")
    if not isinstance(pages, dict):
        errors.append("pages must be a mapping")
        return finish_validation(errors, warnings)

    base_url = site_config.get("base_url")
    base_url_source = "base_url"
    if not base_url:
        base_url = pages.get("home", {}).get("url") if isinstance(pages.get("home"), dict) else None
        base_url_source = "pages.home.url"

    if not is_valid_url(base_url):
        errors.append("base_url is missing or invalid; expected top-level base_url or valid pages.home.url")
    else:
        print(f"base_url: {base_url} ({base_url_source})")

    for page_name in REQUIRED_PAGES:
        if page_name not in pages:
            errors.append(f"pages.{page_name} is missing")
            continue

        try:
            page_config = get_page_config(page_name, site_config)
        except Exception as exc:
            errors.append(
                f"pages.{page_name} failed to load: {type(exc).__name__}: {exc}"
            )
            continue

        if not is_valid_url(page_config.get("url")):
            errors.append(f"pages.{page_name}.url is missing or invalid")
        validate_module_selectors(errors, page_name, page_config)

        if page_name == "collection":
            validate_selector(errors, "pages.collection.product_card", page_config.get("product_card"))
        if page_name == "product":
            validate_selector(errors, "pages.product.variant_inputs", page_config.get("variant_inputs"))

    if not errors:
        print("pages: home, collection, product")
        print("selectors: OK")

    baseline_warnings_before = len(warnings)
    for viewport in baseline_viewports(args, run_viewports):
        for page_name in REQUIRED_PAGES:
            baseline_dir = BASELINE_ROOT / site_name / viewport / page_name
            if not baseline_dir.exists():
                warnings.append(
                    "baseline directory missing: "
                    f"{baseline_dir.relative_to(PROJECT_ROOT)}"
                )
            elif not any(baseline_dir.glob("*.png")):
                warnings.append(
                    "baseline directory has no png files: "
                    f"{baseline_dir.relative_to(PROJECT_ROOT)}"
                )

    if len(warnings) == baseline_warnings_before:
        print("baseline directories: OK")

    return finish_validation(errors, warnings)


def finish_validation(errors, warnings):
    for warning in warnings:
        print(f"WARNING: {warning}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print("Config validation FAILED")
        return 1

    print("OK config validation passed")
    if warnings:
        print(
            "Baseline warnings are non-blocking; create or review baselines before CI visual runs."
        )
    return 0


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

    if args.validate_config:
        sys.exit(validate_config(args))

    if not run_stage("Playwright visual regression", ".", "playwright_checks.runner.main"):
        sys.exit(1)

    print("\n" + "=" * 60)
    print("All checks passed")
    print("=" * 60)


if __name__ == "__main__":
    main()
