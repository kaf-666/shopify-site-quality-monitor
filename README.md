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
- `screenshots/`: captured baseline, current, and diff images.
- `reports/`: JSON test result output.
- `baselines/`: reserved for future baseline storage conventions.

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

Run an explicit side-effect flow:

```powershell
.\.venv\Scripts\python.exe -m playwright_checks.flows.add_to_cart_flow
```
