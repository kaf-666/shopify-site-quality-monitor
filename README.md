# Visual Regression Test Standard

This project is a Playwright-based visual regression test scaffold for Shopify sites.

## Structure

- `configs/settings.yaml`: shared runtime settings.
- `configs/sites/*.yaml`: site-specific URLs, modules, plugin selectors, and dynamic-hide rules.
- `playwright_checks/core/`: config loading, browser driver, logging, and result writing.
- `playwright_checks/pages/`: page object skeletons for home, collection, and product pages.
- `playwright_checks/checks/`: safe page checks and visual checks for each page type.
- `playwright_checks/flows/`: explicit business flows with real user actions and side effects.
- `playwright_checks/utils/`: screenshot capture, DOM helpers, waits, visual comparison, and dynamic element handling.
- `playwright_checks/runner/main.py`: Playwright check runner.
- `baselines/`: reviewed visual baseline images for the new storage convention.
- `artifacts/`: per-run current and diff images.
- `screenshots/`: legacy captured baseline/current/diff images; existing baselines are still read for compatibility.
- `reports/`: JSON test result output.

## Run

```powershell
.\.venv\Scripts\python.exe run_all.py
```

By default the runner executes the viewports listed in `configs/settings.yaml`:

```yaml
run_viewports:
  - desktop
  - mobile
```

Switch site config with PowerShell:

```powershell
$env:VISUAL_SITE_CONFIG="mondressy_UK"; .\.venv\Scripts\python.exe run_all.py
```

Run only one viewport:

```powershell
$env:VISUAL_VIEWPORT="mobile"; .\.venv\Scripts\python.exe run_all.py
```

Initialize missing baselines for a new viewport:

```powershell
$env:VISUAL_VIEWPORT="mobile"; $env:ALLOW_BASELINE_INIT="1"; .\.venv\Scripts\python.exe run_all.py
```

New baseline initialization writes to `baselines/`. Existing legacy baselines under
`screenshots/**/baseline` remain readable while the baseline set is migrated.

Run an explicit side-effect flow:

```powershell
$env:ALLOW_SIDE_EFFECT_FLOW="1"; .\.venv\Scripts\python.exe -m playwright_checks.flows.add_to_cart_flow
```

Side-effect flows are disabled by default so visual regression does not perform
real add-to-cart, checkout, or login actions.

## CI / Jenkins

When `CI=true` is detected, or when `JENKINS_URL` is present, visual warnings are
treated as failures by default. Local runs keep warnings non-blocking by default.
To make local warnings fail the run, set:

```powershell
$env:VISUAL_STRICT_WARNINGS="1"; .\.venv\Scripts\python.exe run_all.py
```

CI/Jenkins runs do not automatically initialize missing baselines, even when
`ALLOW_BASELINE_INIT=1` is set. To force baseline initialization in CI, both
variables must be set:

```powershell
$env:ALLOW_BASELINE_INIT="1"; $env:FORCE_BASELINE_INIT="1"; .\.venv\Scripts\python.exe run_all.py
```

Automatic baseline updates in CI are not recommended. Baseline changes should be
reviewed manually and committed under `baselines/`.

Recommended Jenkins archive paths:

- `artifacts/**`
- `reports/visual-results.json`
