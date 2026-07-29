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
- `playwright_checks/artifacts/`: screenshot lifecycle, retention policy,
  manifests, quota enforcement, and run-scoped cleanup.
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

### Screenshot lifecycle and retained evidence

Screenshots now follow one shared lifecycle:

```text
Capture into artifacts/<run>/<site>/<viewport>/<page>/.tmp/attempt-<n>/
  -> Compare with the reviewed baseline
  -> Run dynamic-region and structural checks
  -> Classify PASS / CONTENT_CHANGED / WARNING / FAILED
  -> Retain only useful evidence
  -> Enforce page, site, and run quotas
  -> Write page manifest and run summary
  -> Remove current-run temporary files
```

This replaces the old flow in which each check selected its own temporary
location and permanently stored every current/diff image. Temporary filenames
include the full run context, retry attempt, and a UUID. They remain on the
artifact filesystem. If a move nevertheless crosses filesystems, the manager
handles only `EXDEV` by copying metadata and content before removing the source;
all other move failures still propagate.

The default local mode is `standard`. Jenkins explicitly selects
`evidence_only`; `debug` is opt-in:

| Result | evidence_only | standard | debug |
| --- | --- | --- | --- |
| PASS | Delete current/diff | Keep global and first-screen context only | Keep all |
| CONTENT_CHANGED | JSON only | Optional representative evidence | Keep all |
| WARNING | Keep current + diff | Keep current + diff | Keep all |
| FAILED | Keep current + diff and failure context | Keep current + diff and context | Keep all |
| BASELINE_MISSING | One representative current | One representative current | Keep all |
| TERMINAL_PAGE | One terminal screenshot | One terminal screenshot | One terminal screenshot |

`CONTENT_CHANGED` is non-blocking and records structured changes without
changing the visual thresholds. A failed structural check always remains
`FAILED`. Deleted image fields in `visual-results.json` are `null`; manifests
are normalized so they never reference a deleted, missing, or out-of-root
file.

Configure the policy under `artifacts.screenshot_retention` in
`configs/settings.yaml`. Every field has a safe default. These environment
variables can override the mode and quotas:

- `SCREENSHOT_RETENTION_MODE`
- `SCREENSHOT_MAX_IMAGES_PER_PAGE`
- `SCREENSHOT_MAX_MB_PER_PAGE`
- `SCREENSHOT_MAX_MB_PER_SITE`
- `SCREENSHOT_MAX_MB_PER_RUN`

The default quotas are 12 images or 50 MB per page, 200 MB per site, and
1000 MB per run. Quota eviction keeps evidence in this order: terminal page,
global failure, first-screen failure, failed module, warning module, baseline
missing, content-change representative, then debug evidence. PASS images
never consume the long-term quota. Quota cleanup is best-effort and recorded
instead of terminating the test run.

Each checked page writes:

```text
artifacts/<run-id>/<site>/<viewport>/<page>/artifact-manifest.json
```

Example:

```json
{
  "schema_version": "1.0",
  "run_id": "build-123",
  "site": "mondressy_US",
  "viewport": "desktop",
  "page": "home",
  "retention_mode": "evidence_only",
  "total_files": 2,
  "total_bytes": 183420,
  "retained_images": [
    {
      "case": "featured_collection",
      "artifact_type": "module",
      "visual_status": "failed",
      "relative_path": "build-123/mondressy_US/desktop/home/current/featured_collection.png",
      "size_bytes": 104210,
      "retention_reason": "visual_failure",
      "priority": 4
    }
  ],
  "deleted_passed_images": [],
  "content_changes": [],
  "dropped_by_quota": [],
  "temporary_cleanup_errors": []
}
```

The run-level `artifacts/<run-id>/artifact-summary.json` aggregates only live
retained files:

```json
{
  "schema_version": "1.0",
  "run_id": "build-123",
  "total_images": 2,
  "total_bytes": 183420,
  "site_bytes": {"mondressy_US": 183420},
  "page_bytes": {"mondressy_US/desktop/home": 183420},
  "retained_passed": 0,
  "retained_content_changed": 0,
  "retained_warning": 0,
  "retained_failed": 2,
  "retained_terminal_page": 0,
  "deleted_passed_images": 8,
  "content_change_count": 1,
  "dropped_by_quota": 0,
  "largest_page": {
    "page": "mondressy_US/desktop/home",
    "size_bytes": 183420
  },
  "largest_site": {"site": "mondressy_US", "size_bytes": 183420}
}
```

### Dynamic content policy

Dynamic regions support three strategies:

- `mask_content` treats configured product media, titles, prices, badges, and
  similar operational content as changeable while preserving module and card
  structure checks.
- `layout_only` skips strict region pixel equality and validates existence,
  visibility, minimum count, card dimensions/columns, overlap, image success,
  titles, prices, and horizontal overflow.
- `ignore_visual` performs no pixel comparison but still requires minimum
  visibility and usable dimensions.

For `mondressy_US`, Home product collections use `mask_content`; the Collection
product grid uses `layout_only`; the configured Product monitoring item remains
stable and its changeable information uses `mask_content`. Product count,
order, image, title, price, and availability changes can therefore be reported
as `CONTENT_CHANGED`. An empty or missing grid, card overlap, unexpected
columns, mass image failures, horizontal overflow, missing titles/prices,
missing filter/sort controls, an unavailable monitoring product, a blank
gallery, or missing purchase controls remains a failure. The runner does not
switch to a random product or update configuration/baselines automatically.
Declared masks are also projected onto temporary comparison copies of global
and first-screen captures, so the same operational content does not fail those
context images. The original capture and every reviewed baseline remain
unchanged.

Home carousel diagnostics distinguish matched, visible, and hidden cards.
Intentional off-screen/clone cards do not have to be visible simultaneously;
at least one visible card must retain the configured image/title/price
structure. Collection fixed grids continue to enforce their visible-count and
image-success rules independently.

Small, case-specific geometry drift can be configured without changing global
visual thresholds:

```yaml
size_tolerance:
  currency:
    width_px: 2
    height_px: 2
    ratio: 0.03
```

Within tolerance, the current image is padded/cropped to the baseline canvas
using the baseline corner background and pixel comparison continues. Larger
geometry changes still fail.

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

Jenkins uses `SCREENSHOT_RETENTION_MODE=evidence_only` and
`VISUAL_STRICT_WARNINGS=false`. `CONTENT_CHANGED`, visual warnings, and
report-only Runtime findings do not block the gray build; a visual failure
does. Each platform branch writes its `returnStatus` result directly to
`env.GRAY_PYTHON_EXIT_CODE` with `.toString()` and validates the saved value in
the same Jenkins `script` block. Statuses 0, 1, and 2 therefore remain
available even when the monitor command is nonzero.

Build and archived-artifact retention are separate:

- `buildDiscarder(logRotator(...))` keeps builds for 14 days/20 builds and
  archived artifacts for 7 days/10 builds. Adjust these defaults to server
  capacity.
- `archiveArtifacts` is limited to the current run summary, page manifests,
  Runtime/attempt JSON, visual results, and retained current/diff/terminal
  evidence. It excludes `.tmp`, staging, baselines, `.venv`, `__pycache__`,
  PASS images, and old run IDs.
- A reused workspace is cleaned only for old artifact run directories at build
  start. After archiving, cleanup removes only the current run's `.tmp`,
  staging, plugin-probe, and temporary diff remnants. It never calls a blanket
  `deleteDir` and never removes baselines or the active run.

To validate the new behavior, manually trigger the same Jenkins Job that uses
this repository's `Jenkinsfile`. After it finishes, return
`GRAY_PYTHON_EXIT_CODE`, `GRAY_SUMMARY_EXIT_CODE`, Runtime Health, Visual
Result, Content Changed count, `artifact-summary.json`, retained image count
and bytes, deleted PASS image count, `dropped_by_quota`, Jenkins Archive size,
and workspace disk size before/after the build.
