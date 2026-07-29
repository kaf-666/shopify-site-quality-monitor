# Shopify Site Quality Monitor

Playwright-based visual regression and runtime-health monitoring for Shopify
storefronts. The runner checks Home, collection (PLP), and product (PDP) pages
across configured desktop and mobile viewports.

Current site configurations:

- `gracins_US`
- `lavetir_US`
- `mondressy_UK`
- `mondressy_US` (the default)
- `nafori_US`
- `shirees_US`

## Project layout

- `configs/settings.yaml`: shared browser, viewport, visual-comparison, path,
  and runtime-health defaults.
- `configs/sites/*.yaml`: site URLs, selectors, capture rules, and per-site or
  per-page overrides.
- `playwright_checks/core/`: configuration, browser setup, request-header
  injection, paths, logging, and result writing.
- `playwright_checks/pages/`: Home, PLP, and PDP page objects.
- `playwright_checks/checks/`: read-only structural and visual checks.
- `playwright_checks/runtime/`: console, page, network, loading, rendering,
  retry, scoring, evidence, and gray-summary logic.
- `playwright_checks/utils/`: capture, stabilization, DOM, dynamic-content,
  and image-comparison helpers.
- `playwright_checks/flows/`: explicitly invoked business flows. Only
  `add_to_cart_flow` currently performs a real flow; search, cart, and login
  modules are placeholders.
- `playwright_checks/tests/`: unit, Playwright fixture, integration, Jenkins,
  signed-header, and visual-comparison tests.
- `baselines/`: reviewed baseline images.
- `artifacts/<run-id>/`: current images, diffs, runtime evidence, and a copy of
  the JSON results for one run.
- `reports/visual-results.json`: latest combined visual and page-health results.
- `screenshots/`: legacy baseline location, still readable when fallback is
  enabled.

## Setup

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

`configs/settings.yaml` defaults to the installed Chrome channel and headed
mode, although a site configuration can override this. To use Playwright's
bundled Chromium in headless mode:

```powershell
$env:PLAYWRIGHT_BROWSER_CHANNEL="chromium"
$env:PLAYWRIGHT_HEADED="0"
```

## Validate configuration

Validate YAML, required page configuration, selector shapes, runtime-health
settings, and baseline directories without opening a browser:

```powershell
.\.venv\Scripts\python.exe run_all.py --validate-config --site mondressy_US --viewport desktop
```

Missing baseline directories are reported as non-blocking validation warnings.

## Run checks

Prefer the command-line options exposed by `run_all.py`:

```powershell
# All configured viewports and all three page suites for the default site
.\.venv\Scripts\python.exe run_all.py

# One site, viewport, and page suite
.\.venv\Scripts\python.exe run_all.py --site lavetir_US --viewport mobile --page product

# Explicitly select every configured viewport and page suite
.\.venv\Scripts\python.exe run_all.py --viewport all --page all
```

The first and third examples use the default `mondressy_US` configuration and
therefore require the signed-request environment variables described below.

Supported values:

- `--site`: any name under `configs/sites/`.
- `--viewport`: `desktop`, `mobile`, or `all`.
- `--page`: `home`, `collection`, `product`, or `all`.

The equivalent environment variables remain supported:

- `VISUAL_SITE_CONFIG`
- `VISUAL_VIEWPORT`
- `VISUAL_PAGE`
- `VISUAL_RUN_ID` (optional stable artifact directory name)

By default, viewports come from `configs/settings.yaml`:

```yaml
run_viewports:
  - desktop
  - mobile
```

### Mondressy US signed requests

An actual `mondressy_US` browser run requires all three environment variables
below. Configuration validation and runs for the other sites do not require
them.

```powershell
$env:MONDRESSY_US_SHOPIFY_SIGNATURE="<secret>"
$env:MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT="<secret>"
$env:MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT="<secret>"
.\.venv\Scripts\python.exe run_all.py --site mondressy_US --viewport desktop --page home
```

The values are injected as `Signature`, `Signature-Input`, and
`Signature-Agent` only for exact hosts listed in `signed_request_hosts`.
The current `mondressy_US` production entry and only signed host is
`mondressy.com`; redirects are recorded but `www.mondressy.com` is not
automatically signed. Do not commit the credential values.

### Mondressy 429 diagnostic

The standalone diagnostic compares curl, Playwright `APIRequestContext`, and
Chromium against both Mondressy hosts. It performs one logical request per
probe/host combination, follows and records redirects, and never runs the DOM,
plugin, screenshot, baseline, cart, or checkout suites:

```powershell
.\.venv\Scripts\python.exe -m playwright_checks.diagnostics.mondressy_429 `
  --output artifacts\local-mondressy-429\mondressy-429-diagnostic.json
```

All three signed-request environment variables are required. Console and JSON
output contain only credential-presence booleans, safe `Signature-Input`
metadata, selected response headers, a redacted body preview of at most 100
characters, and body length. Full response bodies, credential values, cookies,
and authorization headers are not retained.

## Baselines and visual policy

Initialize missing local baselines only when intentionally reviewing a new
baseline set:

```powershell
$env:ALLOW_BASELINE_INIT="1"
.\.venv\Scripts\python.exe run_all.py --site lavetir_US --viewport mobile
```

New baselines are written to:

```text
baselines/<site>/<viewport>/<page>/
```

Existing baselines under `screenshots/**/baseline` remain readable while
`paths.legacy_baseline_fallback` (or
`VISUAL_LEGACY_BASELINE_FALLBACK`) is enabled.

Visual warnings are non-blocking locally by default. Make them fail the run
with:

```powershell
$env:VISUAL_STRICT_WARNINGS="1"
.\.venv\Scripts\python.exe run_all.py --site lavetir_US
```

When `CI=true` or `JENKINS_URL` is present, visual warnings are strict by
default. CI baseline initialization requires both explicit flags:

```powershell
$env:ALLOW_BASELINE_INIT="1"
$env:FORCE_BASELINE_INIT="1"
.\.venv\Scripts\python.exe run_all.py --site lavetir_US
```

Baseline updates should normally be reviewed locally and committed under
`baselines/`, rather than generated automatically in CI.

## Runtime-health monitoring

Runtime monitoring is enabled in `configs/settings.yaml`. It records evidence
for signals such as failed requests, HTTP errors, console/page errors, dialogs,
page crashes, blank/error pages, missing critical components, and persistent
loading states. Configuration is merged from shared, site, and page settings.

The current default is report-only:

```yaml
runtime_health:
  enabled: true
  reporting:
    report_only: true
    affect_exit_code: false
```

Runtime findings therefore remain visible in page summaries and evidence but
do not independently fail the process. These environment overrides are
available:

- `RUNTIME_HEALTH_ENABLED`
- `RUNTIME_HEALTH_REPORT_ONLY`
- `RUNTIME_HEALTH_AFFECT_EXIT_CODE`
- `RUNTIME_HEALTH_FAIL_ON_FAILED`
- `RUNTIME_HEALTH_FAIL_ON_WARNING`
- `RUNTIME_HEALTH_RECOVERED_STATUS` (`passed`, `warning`, or `failed`)

To enforce failed runtime-health results:

```powershell
$env:RUNTIME_HEALTH_REPORT_ONLY="0"
$env:RUNTIME_HEALTH_AFFECT_EXIT_CODE="1"
$env:RUNTIME_HEALTH_FAIL_ON_FAILED="1"
.\.venv\Scripts\python.exe run_all.py --site lavetir_US
```

Run evidence is written beneath:

```text
artifacts/<run-id>/<site>/<viewport>/<page>/runtime/
```

## Side-effect flow

Read-only visual checks do not add to cart, log in, or check out. The implemented
add-to-cart flow must be enabled explicitly and should be run only in an
isolated test context:

```powershell
$env:ALLOW_SIDE_EFFECT_FLOW="1"
.\.venv\Scripts\python.exe -m playwright_checks.flows.add_to_cart_flow
```

The flow clears the cart before and after its check, selects available product
options, clicks Add to Cart, and verifies that `/cart.js` item count increases.
It uses the currently selected site configuration.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s playwright_checks/tests -v
```

## Jenkins

The current `Jenkinsfile` is a deliberately narrow, manually triggered
Mondressy US home-page Runtime gray validation. It:

1. Rejects non-user-triggered builds.
2. Validates and then rebinds the three Jenkins string credentials without
   printing their values.
3. Installs dependencies and Playwright Chromium, then smoke-tests browser
   launch.
4. Runs only the `mondressy_US` desktop Home check, whose configured entry is
   `https://mondressy.com`.
5. Preserves the production Route-based signed-header injection and signs only
   `mondressy.com`.
6. Captures the monitor and summary exit codes with `returnStatus`, archives
   evidence, and only then evaluates the result.
7. Keeps baseline initialization and side-effect flows disabled.

The six-probe command above remains available as a standalone diagnostic; it is
not invoked by this Jenkinsfile.
