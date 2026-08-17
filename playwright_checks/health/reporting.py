import html
import json
from pathlib import Path

from playwright_checks.core.config_loader import (
    PROJECT_ROOT,
    load_site_config,
)
from playwright_checks.core.paths import artifact_root
from playwright_checks.health.config import get_health_check_config
from playwright_checks.health.engine import HealthEngine
from playwright_checks.health.file_io import atomic_write_text
from playwright_checks.health.models import HealthStatus, Severity
from playwright_checks.health.profile_artifacts import (
    build_profile_bundle,
    write_profile_bundle,
)
from playwright_checks.runtime.evidence import redact_text


def write_health_reports_fail_open(*args, **kwargs):
    """Generate the sidecar report without changing legacy test outcomes."""

    try:
        return write_health_reports(*args, **kwargs)
    except Exception as error:
        return {
            "report": None,
            "json": None,
            "html": None,
            "error": redact_text(
                f"{type(error).__name__}: {error}"
            ),
        }


def write_health_reports(
    results,
    site_config=None,
    config=None,
    ai_analyzer=None,
    output_dir=None,
    project_root=PROJECT_ROOT,
):
    resolved_config = config or get_health_check_config(site_config)
    if not resolved_config.get("enabled", True):
        return {"report": None, "json": None, "html": None}

    report = HealthEngine(
        results,
        site_config=site_config,
        config=resolved_config,
        ai_analyzer=ai_analyzer,
        project_root=project_root,
    ).build()
    report_config = resolved_config.get("report", {}) or {}
    configured_output = output_dir or report_config.get("output_dir", "reports")
    output_root = Path(configured_output)
    if not output_root.is_absolute():
        output_root = Path(project_root) / output_root
    run_root = artifact_root() / report.run_id
    output_root.mkdir(parents=True, exist_ok=True)
    run_root.mkdir(parents=True, exist_ok=True)

    paths = {
        "report": report,
        "json": None,
        "html": None,
        "site_profile": None,
        "test_plan": None,
    }
    profile_site_config = site_config or _load_profile_site_config(report.site)
    if profile_site_config:
        try:
            bundle = build_profile_bundle(
                profile_site_config,
                health_config=resolved_config,
                observations=results,
                generated_at=report.generated_at,
            )
            bundle = write_profile_bundle(bundle, run_root)
            report.site_profile_reference = _relative_reference(
                bundle.profile_path,
                project_root,
            )
            report.site_profile_summary = {
                "status": "AVAILABLE",
                **bundle.profile.summary(),
            }
            report.test_plan_reference = _relative_reference(
                bundle.plan_path,
                project_root,
            )
            report.test_plan_summary = {
                "status": "AVAILABLE",
                **bundle.plan.summary(),
            }
            paths["site_profile"] = str(bundle.profile_path)
            paths["test_plan"] = str(bundle.plan_path)
        except Exception as error:
            safe_error = redact_text(
                f"{type(error).__name__}: {error}"
            )
            report.site_profile_summary = {
                "status": "UNAVAILABLE",
                "error": safe_error,
            }
            report.test_plan_summary = {
                "status": "UNAVAILABLE",
                "error": "site_profile_unavailable",
            }
    else:
        report.site_profile_summary = {
            "status": "UNAVAILABLE",
            "error": "site_config_unavailable",
        }
        report.test_plan_summary = {
            "status": "UNAVAILABLE",
            "error": "site_profile_unavailable",
        }

    payload = report.to_dict()
    if report_config.get("json", True):
        json_text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        primary_json = output_root / "health-report.json"
        run_json = run_root / "health-report.json"
        atomic_write_text(primary_json, json_text)
        atomic_write_text(run_json, json_text)
        paths["json"] = str(primary_json.resolve())
        paths["artifact_json"] = str(run_json.resolve())

    if report_config.get("html", True):
        html_text = render_health_dashboard(report)
        primary_html = output_root / "health-report.html"
        run_html = run_root / "health-report.html"
        atomic_write_text(primary_html, html_text)
        atomic_write_text(run_html, html_text)
        paths["html"] = str(primary_html.resolve())
        paths["artifact_html"] = str(run_html.resolve())
    return paths


def render_health_dashboard(report):
    findings = sorted(
        report.findings,
        key=lambda item: (
            _severity_rank(item.severity),
            item.page,
            item.viewport,
        ),
        reverse=True,
    )
    critical = [
        finding
        for finding in findings
        if finding.alert_eligible or finding.severity == Severity.CRITICAL
    ]
    recommendations = list(
        dict.fromkeys(
            finding.recommendation
            for finding in findings
            if finding.recommendation
        )
    )
    if report.ai_analysis.recommendations:
        recommendations.extend(report.ai_analysis.recommendations)
        recommendations = list(dict.fromkeys(recommendations))

    dimension_rows = "".join(
        "<tr>"
        f"<td>{_escape(name.replace('_', ' ').title())}</td>"
        f"<td>{_status_badge(status.value)}</td>"
        "</tr>"
        for name, status in report.dimension_statuses.items()
    )
    page_cards = "".join(_page_card(page) for page in report.pages)
    critical_html = (
        "".join(_finding_card(finding) for finding in critical)
        if critical
        else '<p class="empty">No high-confidence critical finding was produced.</p>'
    )
    findings_html = (
        "".join(_finding_card(finding) for finding in findings)
        if findings
        else '<p class="empty">No abnormal finding was produced.</p>'
    )
    actions_html = (
        "<ol>" + "".join(f"<li>{_escape(value)}</li>" for value in recommendations) + "</ol>"
        if recommendations
        else '<p class="empty">No action required from this run.</p>'
    )
    ai = report.ai_analysis
    ai_summary = ai.summary or ai.reason
    profile_summary = report.site_profile_summary or {}
    plan_summary = report.test_plan_summary or {}
    profile_reference = report.site_profile_reference or "unavailable"
    plan_reference = report.test_plan_reference or "unavailable"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{_escape(report.site)} Website Health</title>
  <style>
    :root {{ color-scheme: light; --bg:#f4f7fb; --card:#fff; --ink:#172033; --muted:#64748b; --line:#dbe3ef; --green:#087f5b; --yellow:#9a6700; --red:#b42318; --blue:#2457c5; --gray:#586174; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 Inter,Segoe UI,Arial,sans-serif; }}
    main {{ max-width:1240px; margin:0 auto; padding:32px 20px 64px; }}
    header {{ display:flex; gap:24px; align-items:flex-start; justify-content:space-between; margin-bottom:24px; }}
    h1 {{ margin:0 0 6px; font-size:30px; }} h2 {{ margin:0 0 16px; font-size:20px; }} h3 {{ margin:0 0 8px; font-size:16px; }}
    .muted,.empty {{ color:var(--muted); }} .grid {{ display:grid; grid-template-columns:repeat(12,1fr); gap:16px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px; box-shadow:0 2px 8px rgba(30,55,90,.04); }}
    .summary {{ grid-column:span 8; }} .dimensions {{ grid-column:span 4; }} .wide {{ grid-column:1/-1; }}
    .metric-row {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:16px; }} .metric {{ min-width:130px; padding:12px; background:#f8fafc; border-radius:8px; }}
    .metric strong {{ display:block; font-size:22px; }} .badge {{ display:inline-block; border-radius:999px; padding:4px 9px; font-weight:700; font-size:12px; }}
    .PASS,.HEALTHY,.EXPECTED_CHANGE {{ color:var(--green); background:#dcfce7; }} .WARN,.FLAKY,.DEGRADED {{ color:var(--yellow); background:#fef3c7; }}
    .FAIL,.CRITICAL {{ color:var(--red); background:#fee2e2; }} .BLOCKED,.UNVERIFIED {{ color:var(--blue); background:#dbeafe; }} .NOT_APPLICABLE {{ color:var(--gray); background:#e5e7eb; }}
    table {{ width:100%; border-collapse:collapse; }} td {{ border-top:1px solid var(--line); padding:8px 4px; }} td:last-child {{ text-align:right; }}
    .page-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; }} .page-title {{ display:flex; justify-content:space-between; gap:10px; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }} .chip {{ padding:3px 7px; border-radius:6px; background:#eef2ff; color:#3730a3; font-size:11px; }}
    .finding {{ border-left:4px solid var(--line); margin:10px 0; }} .finding.HIGH,.finding.CRITICAL {{ border-left-color:var(--red); }} .finding.MEDIUM {{ border-left-color:#d69e00; }}
    .finding-head {{ display:flex; flex-wrap:wrap; justify-content:space-between; gap:8px; }} .labels {{ display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; }}
    details {{ margin-top:10px; }} summary {{ cursor:pointer; color:var(--blue); }} code {{ word-break:break-all; }}
    ol {{ padding-left:22px; }} .alert {{ font-weight:700; }}
    @media (max-width:800px) {{ .summary,.dimensions {{ grid-column:1/-1; }} header {{ display:block; }} }}
  </style>
</head>
<body><main>
  <header>
    <div><h1>Website Health Dashboard</h1><div class="muted">{_escape(report.site)} · run {_escape(report.run_id)} · {_escape(report.generated_at)}</div></div>
    <div>{_status_badge(report.overall_health.value)} {_status_badge(report.status.value)}</div>
  </header>
  <section class="grid">
    <article class="card summary">
      <h2>Overall Health</h2>
      <p class="alert">Alert: {_escape(report.alert.alert_type)} — {_escape(report.alert.reason)}</p>
      <div class="metric-row">
        <div class="metric"><strong>{len(report.pages)}</strong>page scopes</div>
        <div class="metric"><strong>{len(report.findings)}</strong>findings</div>
        <div class="metric"><strong>{report.summary.get('actionable_finding_count', 0)}</strong>actionable</div>
        <div class="metric"><strong>—</strong>score deferred</div>
      </div>
    </article>
    <article class="card dimensions"><h2>Health Dimensions</h2><table>{dimension_rows}</table></article>
    <article class="card wide"><h2>Critical Findings</h2>{critical_html}</article>
    <article class="card wide"><h2>Changes Since Previous Run</h2><p class="empty">History comparison is UNVERIFIED in phase 1; no trend claim is made.</p></article>
    <article class="card wide"><h2>Site Profile &amp; Deterministic Plan</h2><p>{_status_badge(profile_summary.get('status'))} Profile: <code>{_escape(profile_reference)}</code></p><p>{_status_badge(plan_summary.get('status'))} Plan: <code>{_escape(plan_reference)}</code> · ready {plan_summary.get('ready_count', 0)} · policy/coverage blocked {plan_summary.get('non_executable_count', 0)}</p><p class="muted">The profile describes known site capabilities; the plan does not execute browser actions.</p></article>
    <article class="card wide"><h2>Page Health</h2><div class="page-grid">{page_cards}</div></article>
    <article class="card wide"><h2>All Findings &amp; Evidence</h2>{findings_html}</article>
    <article class="card wide"><h2>AI Analysis</h2><p>{_status_badge(ai.status)} {_escape(ai_summary)}</p><p class="muted">Invoked: {str(ai.invoked).lower()} · Provider: {_escape(ai.provider or 'none')}. Selector and baseline changes require explicit approval.</p></article>
    <article class="card wide"><h2>Recommended Actions</h2>{actions_html}</article>
  </section>
</main></body></html>
"""


def _page_card(page):
    capabilities = "".join(
        f'<span class="chip">{_escape(item.name)} · {_escape(item.side_effect_level.value)}</span>'
        for item in page.capabilities.capabilities
    ) or '<span class="muted">No capability detected</span>'
    dimensions = "".join(
        f"<tr><td>{_escape(name)}</td><td>{_status_badge(status.value)}</td></tr>"
        for name, status in page.dimensions.items()
        if status not in (HealthStatus.NOT_APPLICABLE,)
    )
    return (
        '<div class="card">'
        f'<div class="page-title"><h3>{_escape(page.page_type.value)} · {_escape(page.viewport)}</h3>{_status_badge(page.status.value)}</div>'
        f'<div class="muted"><code>{_escape(page.url or "URL unavailable")}</code></div>'
        f'<div class="muted">{page.finding_count} finding(s) · {page.source_result_count} source record(s)</div>'
        f'<div class="chips">{capabilities}</div>'
        f'<details><summary>Dimension coverage</summary><table>{dimensions}</table></details>'
        "</div>"
    )


def _finding_card(finding):
    evidence = "".join(
        "<li>"
        f"<strong>{_escape(item.evidence_type.value)}</strong>: {_escape(item.summary)}"
        + (f" — <code>{_escape(item.reference)}</code>" if item.reference else "")
        + "</li>"
        for item in finding.evidence
    ) or "<li>No retained evidence reference.</li>"
    suppression = (
        f'<div class="muted">Alert suppressed: {_escape(finding.suppression_reason)}</div>'
        if finding.suppression_reason
        else ""
    )
    return (
        f'<div class="card finding {_escape(finding.severity.value)}">'
        f'<div class="finding-head"><h3>{_escape(finding.title)}</h3>{_status_badge(finding.status.value)}</div>'
        '<div class="labels">'
        f'<span class="chip">{_escape(finding.classification.value)}</span>'
        f'<span class="chip">{_escape(finding.severity.value)}</span>'
        f'<span class="chip">evidence {_escape(finding.evidence_level.value)}</span>'
        f'<span class="chip">{_escape(finding.page_type.value)} / {_escape(finding.viewport)}</span>'
        "</div>"
        f'<p>{_escape(finding.summary)}</p><p><strong>Impact:</strong> {_escape(finding.business_impact)}</p>'
        f"{suppression}"
        f'<details><summary>Evidence ({len(finding.evidence)})</summary><ul>{evidence}</ul></details>'
        "</div>"
    )


def _status_badge(value):
    normalized = str(value or "UNKNOWN").upper()
    return f'<span class="badge {_escape(normalized)}">{_escape(normalized)}</span>'


def _severity_rank(value):
    return {
        Severity.NONE: 0,
        Severity.INFO: 1,
        Severity.LOW: 2,
        Severity.MEDIUM: 3,
        Severity.HIGH: 4,
        Severity.CRITICAL: 5,
    }[value]


def _escape(value):
    return html.escape(str(value or ""), quote=True)


def _load_profile_site_config(site):
    try:
        return load_site_config(site)
    except (FileNotFoundError, KeyError, TypeError, ValueError):
        return None


def _relative_reference(path, project_root):
    value = Path(path).resolve()
    try:
        return value.relative_to(Path(project_root).resolve()).as_posix()
    except ValueError:
        return str(value)
