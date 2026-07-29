import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/webp,*/*;q=0.8"
)
ACCEPT_LANGUAGE = "en-US,en;q=0.9"
CACHE_CONTROL = "no-cache"
REFERER = "https://www.google.com"
TARGETS = (
    ("mondressy.com", "https://mondressy.com/"),
    ("www.mondressy.com", "https://www.mondressy.com/"),
)
ALLOWED_TARGET_HOSTS = frozenset(host for host, _url in TARGETS)
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 10
REQUEST_TIMEOUT_MS = 45_000
LOCAL_RATE_LIMIT_MARKER = b"local_rate_limited"

CREDENTIALS = (
    ("MONDRESSY_US_SHOPIFY_SIGNATURE", "Signature"),
    ("MONDRESSY_US_SHOPIFY_SIGNATURE_INPUT", "Signature-Input"),
    ("MONDRESSY_US_SHOPIFY_SIGNATURE_AGENT", "Signature-Agent"),
)
SENSITIVE_QUERY_KEY = re.compile(
    r"(?:auth|authorization|code|credential|key|secret|signature|token)",
    re.IGNORECASE,
)
SENSITIVE_TEXT = re.compile(
    r"(?i)\b(authorization|cookie|set-cookie|signature(?:-input|-agent)?)"
    r"\s*[:=]\s*([^\s,;<]+|\"[^\"]*\")"
)
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
IPV4 = re.compile(
    r"(?<!\d)(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?!\d)"
)
LONG_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{24,}(?![A-Za-z0-9])")


class DiagnosticConfigurationError(RuntimeError):
    """Configuration failure whose message never contains a credential value."""


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Run one curl, APIRequestContext, and Chromium request against "
            "each Mondressy host without invoking visual checks."
        )
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON result path; only redacted previews are written.",
    )
    return parser.parse_args(argv)


def read_credentials():
    values = {}
    presence = {}
    invalid = []

    for env_name, header_name in CREDENTIALS:
        value = os.environ.get(env_name) or ""
        is_present = bool(value.strip())
        presence[env_name] = is_present
        if not is_present:
            continue
        if "\r" in value or "\n" in value:
            invalid.append(env_name)
            continue
        values[header_name] = value

    missing = [name for name, is_present in presence.items() if not is_present]
    if missing or invalid:
        parts = []
        if missing:
            parts.append("missing=" + ",".join(missing))
        if invalid:
            parts.append("invalid_header_value=" + ",".join(invalid))
        raise DiagnosticConfigurationError(
            "Credential validation failed; " + "; ".join(parts)
        )

    return presence, values


def analyze_signature_input(value):
    covered_section = re.search(r"\((?P<components>[^)]*)\)", value or "")
    covered_components = []
    if covered_section:
        covered_components = re.findall(
            r'"([^"]+)"',
            covered_section.group("components"),
        )

    return {
        "created_present": bool(
            re.search(r"(?:^|;)\s*created\s*=", value or "", re.IGNORECASE)
        ),
        "expires_present": bool(
            re.search(r"(?:^|;)\s*expires\s*=", value or "", re.IGNORECASE)
        ),
        "covered_components": covered_components,
    }


def common_request_headers(signature_headers):
    return {
        "User-Agent": USER_AGENT,
        "Accept": ACCEPT,
        "Accept-Language": ACCEPT_LANGUAGE,
        "Cache-Control": CACHE_CONTROL,
        "Referer": REFERER,
        **signature_headers,
    }


def chromium_extra_http_headers(signature_headers):
    return {
        "Accept": ACCEPT,
        "Accept-Language": ACCEPT_LANGUAGE,
        "Cache-Control": CACHE_CONTROL,
        **signature_headers,
    }


def _is_external_http_url(value):
    try:
        parsed = urlsplit(str(value))
    except ValueError:
        return False
    return bool(
        parsed.scheme.lower() in ("http", "https")
        and (parsed.hostname or "").lower() not in ALLOWED_TARGET_HOSTS
    )


def _install_external_signature_stripping(context):
    signed_header_names = {
        header_name.lower() for _env_name, header_name in CREDENTIALS
    }

    def strip_signature_headers(route):
        original_headers = route.request.all_headers()
        safe_headers = {
            key: value
            for key, value in original_headers.items()
            if key.lower() not in signed_header_names
        }
        route.continue_(headers=safe_headers)

    context.route(_is_external_http_url, strip_signature_headers)


def _sanitize_text(value, secrets=(), limit=None):
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = SENSITIVE_TEXT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = EMAIL.sub("[REDACTED_EMAIL]", text)
    text = IPV4.sub("[REDACTED_IP]", text)
    text = LONG_TOKEN.sub("[REDACTED_TOKEN]", text)
    text = " ".join(text.split())
    if limit is not None:
        text = text[:limit]
    return text


def _safe_url(value, secrets=()):
    redacted = _sanitize_text(value, secrets=secrets, limit=2_000)
    try:
        parsed = urlsplit(redacted)
    except ValueError:
        return redacted

    hostname = parsed.hostname or ""
    if not parsed.scheme or not hostname:
        return redacted

    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{hostname}{port}"
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        query.append(
            (key, "[REDACTED]" if SENSITIVE_QUERY_KEY.search(key) else item)
        )
    return urlunsplit(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            urlencode(query, doseq=True),
            "",
        )
    )


def _selected_response_headers(headers, secrets=()):
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    return {
        "server": _sanitize_text(normalized.get("server"), secrets, 500)
        or None,
        "cf_ray": _sanitize_text(normalized.get("cf-ray"), secrets, 500)
        or None,
        "retry_after": _sanitize_text(
            normalized.get("retry-after"),
            secrets,
            500,
        )
        or None,
        "content_type": _sanitize_text(
            normalized.get("content-type"),
            secrets,
            500,
        )
        or None,
    }


def _decode_body_prefix(body, content_type):
    charset_match = re.search(
        r"charset\s*=\s*[\"']?([^;\"'\s]+)",
        content_type or "",
        re.IGNORECASE,
    )
    encodings = [charset_match.group(1)] if charset_match else []
    encodings.extend(["utf-8", "latin-1"])
    for encoding in encodings:
        try:
            return body[:4_096].decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body[:4_096].decode("utf-8", errors="replace")


def _body_fields(body, status, content_type, secrets):
    lowered = body.lower()
    if LOCAL_RATE_LIMIT_MARKER in lowered:
        category = "local_rate_limited"
    elif status is not None and status < 400:
        category = "normal"
    else:
        category = "http_error"

    decoded_prefix = _decode_body_prefix(body, content_type)
    return {
        "body_preview": _sanitize_text(
            decoded_prefix,
            secrets=secrets,
            limit=100,
        ),
        "body_length": len(body),
        "body_category": category,
    }


def _chain_entry(url, status, headers, secrets):
    selected = _selected_response_headers(headers, secrets)
    return {
        "url": _safe_url(url, secrets),
        "status": status,
        "location": _safe_url(headers.get("location"), secrets)
        if headers.get("location")
        else None,
        **selected,
    }


def _new_result(probe, host, initial_url):
    return {
        "probe": probe,
        "host": host,
        "status": None,
        "initial_url": initial_url,
        "final_url": None,
        "redirect_chain": [],
        "duration_ms": None,
        "server": None,
        "cf_ray": None,
        "retry_after": None,
        "content_type": None,
        "body_preview": "",
        "body_length": 0,
        "body_category": "probe_error",
        "error": None,
    }


def _finish_result(result, status, final_url, chain, duration_ms, headers, body, secrets):
    selected = _selected_response_headers(headers, secrets)
    result.update(
        {
            "status": status,
            "final_url": _safe_url(final_url, secrets),
            "redirect_chain": chain,
            "duration_ms": round(duration_ms, 1),
            **selected,
            **_body_fields(body, status, selected["content_type"], secrets),
        }
    )
    return result


def _record_error(result, error, secrets):
    result["error"] = (
        f"{type(error).__name__}: "
        f"{_sanitize_text(error, secrets=secrets, limit=500)}"
    )
    return result


def _curl_escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _curl_header_config(headers):
    return "\n".join(
        f'header = "{_curl_escape(name)}: {_curl_escape(value)}"'
        for name, value in headers.items()
    )


def _parse_write_out(stdout):
    values = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _parse_curl_header_blocks(data):
    text = data.decode("iso-8859-1", errors="replace")
    blocks = []
    current = None

    for raw_line in text.splitlines():
        if raw_line.startswith("HTTP/"):
            if current:
                blocks.append(current)
            parts = raw_line.split(None, 2)
            try:
                status = int(parts[1])
            except (IndexError, ValueError):
                current = None
                continue
            if "connection established" in raw_line.lower():
                current = None
                continue
            current = {"status": status, "headers": {}}
            continue
        if current is None or not raw_line or ":" not in raw_line:
            continue
        name, value = raw_line.split(":", 1)
        current["headers"][name.strip().lower()] = value.strip()

    if current:
        blocks.append(current)
    return blocks


def run_curl_probe(host, initial_url, headers, secrets):
    result = _new_result("curl", host, initial_url)
    curl = shutil.which("curl")
    if not curl:
        return _record_error(
            result,
            RuntimeError("curl executable was not found"),
            secrets,
        )

    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="mondressy-429-curl-") as temp_dir:
            temp_path = Path(temp_dir)
            response_headers_path = temp_path / "response-headers.txt"
            body_path = temp_path / "response-body.bin"
            cookie_jar_path = temp_path / "cookies.txt"
            current_url = initial_url
            chain = []
            curl_duration_ms = 0.0

            for redirect_index in range(MAX_REDIRECTS + 1):
                command = [
                    curl,
                    "--silent",
                    "--show-error",
                    "--connect-timeout",
                    "15",
                    "--max-time",
                    str(REQUEST_TIMEOUT_MS // 1_000),
                    "--request",
                    "GET",
                    "--cookie",
                    str(cookie_jar_path),
                    "--cookie-jar",
                    str(cookie_jar_path),
                    "--url",
                    current_url,
                    "--dump-header",
                    str(response_headers_path),
                    "--output",
                    str(body_path),
                    "--write-out",
                    (
                        "status=%{http_code}\n"
                        "final_url=%{url_effective}\n"
                        "duration=%{time_total}\n"
                    ),
                    "--config",
                    "-",
                ]
                completed = subprocess.run(
                    command,
                    input=_curl_header_config(headers),
                    text=True,
                    capture_output=True,
                    timeout=(REQUEST_TIMEOUT_MS // 1_000) + 5,
                    check=False,
                )
                write_out = _parse_write_out(completed.stdout)
                header_blocks = (
                    _parse_curl_header_blocks(
                        response_headers_path.read_bytes()
                    )
                    if response_headers_path.exists()
                    else []
                )
                body = body_path.read_bytes() if body_path.exists() else b""

                if completed.returncode != 0:
                    raise RuntimeError(
                        "curl failed with exit_code="
                        f"{completed.returncode}; stderr="
                        f"{_sanitize_text(completed.stderr, secrets, 300)}"
                    )
                if not header_blocks:
                    raise RuntimeError(
                        "curl returned no parseable response headers"
                    )

                response_headers = header_blocks[-1]["headers"]
                status = int(
                    write_out.get("status") or header_blocks[-1]["status"]
                )
                response_url = write_out.get("final_url") or current_url
                if write_out.get("duration"):
                    curl_duration_ms += float(write_out["duration"]) * 1_000
                chain.append(
                    _chain_entry(
                        response_url,
                        status,
                        response_headers,
                        secrets,
                    )
                )
                location = response_headers.get("location")

                if status in REDIRECT_STATUSES and location:
                    if redirect_index >= MAX_REDIRECTS:
                        raise RuntimeError("curl redirect limit exceeded")
                    current_url = urljoin(response_url, location)
                    if _is_external_http_url(current_url):
                        raise RuntimeError(
                            "curl external redirect blocked before credentials "
                            f"could be sent; target={_safe_url(current_url)}"
                        )
                    continue

                duration_ms = curl_duration_ms or (
                    (time.perf_counter() - started) * 1_000
                )
                return _finish_result(
                    result,
                    status,
                    response_url,
                    chain,
                    duration_ms,
                    response_headers,
                    body,
                    secrets,
                )
            raise RuntimeError("curl did not reach a final response")
    except Exception as error:
        result["duration_ms"] = round((time.perf_counter() - started) * 1_000, 1)
        return _record_error(result, error, secrets)


def run_api_request_probe(playwright, host, initial_url, headers, secrets):
    result = _new_result("APIRequest", host, initial_url)
    started = time.perf_counter()
    request_context = None
    current_url = initial_url
    chain = []

    try:
        request_context = playwright.request.new_context(
            extra_http_headers=headers,
        )
        for redirect_index in range(MAX_REDIRECTS + 1):
            response = request_context.get(
                current_url,
                timeout=REQUEST_TIMEOUT_MS,
                fail_on_status_code=False,
                max_redirects=0,
                max_retries=0,
            )
            response_headers = {
                str(key).lower(): str(value)
                for key, value in (response.headers or {}).items()
            }
            status = response.status
            response_url = response.url or current_url
            chain.append(
                _chain_entry(response_url, status, response_headers, secrets)
            )
            location = response_headers.get("location")

            if status in REDIRECT_STATUSES and location:
                response.dispose()
                if redirect_index >= MAX_REDIRECTS:
                    raise RuntimeError("APIRequest redirect limit exceeded")
                current_url = urljoin(response_url, location)
                if _is_external_http_url(current_url):
                    raise RuntimeError(
                        "APIRequest external redirect blocked before "
                        "credentials could be sent; "
                        f"target={_safe_url(current_url)}"
                    )
                continue

            body = response.body()
            response.dispose()
            return _finish_result(
                result,
                status,
                response_url,
                chain,
                (time.perf_counter() - started) * 1_000,
                response_headers,
                body,
                secrets,
            )
        raise RuntimeError("APIRequest did not reach a final response")
    except Exception as error:
        result["redirect_chain"] = chain
        result["duration_ms"] = round((time.perf_counter() - started) * 1_000, 1)
        return _record_error(result, error, secrets)
    finally:
        if request_context:
            request_context.dispose()


def _document_request_audit(request, secrets=()):
    try:
        headers = {
            str(key).lower(): str(value)
            for key, value in request.all_headers().items()
        }
    except Exception:
        headers = {
            str(key).lower(): str(value)
            for key, value in request.headers.items()
        }

    return {
        "signature_present": bool((headers.get("signature") or "").strip()),
        "signature_input_present": bool(
            (headers.get("signature-input") or "").strip()
        ),
        "signature_agent_present": bool(
            (headers.get("signature-agent") or "").strip()
        ),
        "referer_present": bool((headers.get("referer") or "").strip()),
        "accept_language_present": bool(
            (headers.get("accept-language") or "").strip()
        ),
        "user_agent": headers.get("user-agent"),
        "requested_url": _safe_url(request.url, secrets),
        "resource_type": request.resource_type,
    }


def run_chromium_probe(browser, host, initial_url, signature_headers, secrets):
    result = _new_result("Chromium", host, initial_url)
    result["main_document_requests"] = []
    started = time.perf_counter()
    context = None

    try:
        context = browser.new_context(
            viewport={"width": 1600, "height": 4000},
            locale="en-US",
            user_agent=USER_AGENT,
            extra_http_headers=chromium_extra_http_headers(signature_headers),
            service_workers="block",
        )
        _install_external_signature_stripping(context)
        page = context.new_page()
        response_chain = []

        def on_request(request):
            if (
                request.resource_type == "document"
                and request.is_navigation_request()
                and request.frame == page.main_frame
            ):
                result["main_document_requests"].append(
                    _document_request_audit(request, secrets)
                )

        def on_response(response):
            request = response.request
            if (
                request.resource_type == "document"
                and request.is_navigation_request()
                and request.frame == page.main_frame
            ):
                response_headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
                response_chain.append(
                    _chain_entry(
                        response.url,
                        response.status,
                        response_headers,
                        secrets,
                    )
                )

        page.on("request", on_request)
        page.on("response", on_response)
        response = page.goto(
            initial_url,
            wait_until="commit",
            timeout=REQUEST_TIMEOUT_MS,
            referer=REFERER,
        )
        if response is None:
            raise RuntimeError("Chromium navigation returned no response")

        context.set_extra_http_headers(
            {
                "Accept": ACCEPT,
                "Accept-Language": ACCEPT_LANGUAGE,
                "Cache-Control": CACHE_CONTROL,
            }
        )
        response_headers = {
            str(key).lower(): str(value)
            for key, value in response.all_headers().items()
        }
        body = response.body()
        return _finish_result(
            result,
            response.status,
            page.url or response.url,
            response_chain,
            (time.perf_counter() - started) * 1_000,
            response_headers,
            body,
            secrets,
        )
    except Exception as error:
        result["duration_ms"] = round((time.perf_counter() - started) * 1_000, 1)
        return _record_error(result, error, secrets)
    finally:
        if context:
            context.close()


def _print_credential_presence(presence):
    print("Credential presence")
    for env_name, _header_name in CREDENTIALS:
        print(f"{env_name}: present={str(bool(presence.get(env_name))).lower()}")


def _print_result(result):
    print(
        f"probe={result['probe']} host={result['host']} "
        f"status={result['status']} "
        f"body_category={result['body_category']} "
        f"duration_ms={result['duration_ms']}"
    )
    print(f"initial_url={result['initial_url']}")
    print(f"final_url={result['final_url']}")
    print(
        "redirect_chain="
        + json.dumps(result["redirect_chain"], ensure_ascii=True)
    )
    print(
        f"server={result['server']} cf-ray={result['cf_ray']} "
        f"retry-after={result['retry_after']} "
        f"content-type={result['content_type']}"
    )
    print(
        f"body_preview={json.dumps(result['body_preview'], ensure_ascii=True)} "
        f"body_length={result['body_length']}"
    )
    if result.get("main_document_requests") is not None:
        print(
            "main_document_requests="
            + json.dumps(
                result["main_document_requests"],
                ensure_ascii=True,
            )
        )
    if result.get("error"):
        print(f"error={result['error']}")


def _print_matrix(results):
    print("Diagnostic matrix")
    print("| Probe | Host | Status | Body category |")
    print("|---|---|---:|---|")
    for result in results:
        print(
            f"| {result['probe']} | {result['host']} | "
            f"{result['status']} | {result['body_category']} |"
        )


def interpret_results(results):
    by_key = {
        (result["probe"], result["host"]): result.get("status")
        for result in results
    }

    def statuses_for(probe):
        return [by_key.get((probe, host)) for host, _url in TARGETS]

    curl_statuses = statuses_for("curl")
    api_statuses = statuses_for("APIRequest")
    chromium_statuses = statuses_for("Chromium")
    complete = all(
        status is not None
        for statuses in (curl_statuses, api_statuses, chromium_statuses)
        for status in statuses
    )
    host_affects_result = (
        any(
            statuses[0] is not None
            and statuses[1] is not None
            and statuses[0] != statuses[1]
            for statuses in (curl_statuses, api_statuses, chromium_statuses)
        )
        if complete
        else None
    )
    all_429 = (
        all(
            status == 429
            for statuses in (curl_statuses, api_statuses, chromium_statuses)
            for status in statuses
        )
        if complete
        else None
    )
    browser_fingerprint_difference = (
        all(status == 200 for status in curl_statuses)
        and all(status == 200 for status in api_statuses)
        and all(status == 429 for status in chromium_statuses)
        if complete
        else None
    )
    playwright_http_stack_difference = (
        all(status == 200 for status in curl_statuses)
        and all(status == 429 for status in api_statuses)
        and all(status == 429 for status in chromium_statuses)
        if complete
        else None
    )

    existing_route_www_status = 429
    extra_http_headers_www_status = by_key.get(
        ("Chromium", "www.mondressy.com")
    )
    extra_http_headers_resolved = (
        extra_http_headers_www_status == 200
        if extra_http_headers_www_status is not None
        else None
    )

    if not complete:
        assessment = "probe_execution_incomplete"
        next_step = "fix_probe_execution_and_rerun_same_six_combinations"
    elif host_affects_result:
        assessment = "host_authority_or_redirect_signature_difference"
        next_step = "inspect_origin_signature_authority_and_redirect_validation"
    elif all_429:
        assessment = "origin_ip_signature_expiry_or_upstream_rate_limit"
        next_step = "inspect_origin_rate_limit_and_signature_validity"
    elif extra_http_headers_resolved:
        assessment = "original_route_injection_implementation_difference"
        next_step = "review_request_code_change_without_applying_it_automatically"
    elif browser_fingerprint_difference:
        assessment = "browser_fingerprint_client_hints_or_cookie_difference"
        next_step = "inspect_origin_browser_fingerprint_and_client_hints"
    elif playwright_http_stack_difference:
        assessment = "playwright_headers_host_signature_or_http_stack_difference"
        next_step = "inspect_playwright_header_and_signature_validation"
    else:
        assessment = "mixed_result_requires_matrix_review"
        next_step = "review_redirect_and_response_metadata_by_probe_and_host"

    return {
        "complete": complete,
        "host_affects_result": host_affects_result,
        "all_429": all_429,
        "browser_fingerprint_difference": browser_fingerprint_difference,
        "playwright_http_stack_difference": playwright_http_stack_difference,
        "existing_route_www_status": existing_route_www_status,
        "extra_http_headers_www_status": extra_http_headers_www_status,
        "extra_http_headers_resolved_existing_www_429": (
            extra_http_headers_resolved
        ),
        "assessment": assessment,
        "next_step": next_step,
    }


def run_diagnostics(output_path=None):
    presence = {
        env_name: bool((os.environ.get(env_name) or "").strip())
        for env_name, _header_name in CREDENTIALS
    }
    _print_credential_presence(presence)
    presence, signature_headers = read_credentials()
    secrets = tuple(signature_headers.values())
    signature_input_analysis = analyze_signature_input(
        signature_headers["Signature-Input"]
    )
    print(
        "signature_input_metadata="
        + json.dumps(signature_input_analysis, ensure_ascii=True)
    )

    headers = common_request_headers(signature_headers)
    results = []

    for host, initial_url in TARGETS:
        result = run_curl_probe(host, initial_url, headers, secrets)
        results.append(result)
        _print_result(result)

    with sync_playwright() as playwright:
        for host, initial_url in TARGETS:
            result = run_api_request_probe(
                playwright,
                host,
                initial_url,
                headers,
                secrets,
            )
            results.append(result)
            _print_result(result)

        browser = playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        try:
            for host, initial_url in TARGETS:
                result = run_chromium_probe(
                    browser,
                    host,
                    initial_url,
                    signature_headers,
                    secrets,
                )
                results.append(result)
                _print_result(result)
        finally:
            browser.close()

    _print_matrix(results)
    interpretation = interpret_results(results)
    print(
        "interpretation="
        + json.dumps(interpretation, ensure_ascii=True)
    )
    report = {
        "credential_presence": presence,
        "signature_input_metadata": signature_input_analysis,
        "request_conditions": {
            "initial_urls": [url for _host, url in TARGETS],
            "user_agent": USER_AGENT,
            "accept": ACCEPT,
            "accept_language": ACCEPT_LANGUAGE,
            "cache_control": CACHE_CONTROL,
            "referer": REFERER,
            "signature_present": True,
            "signature_input_present": True,
            "signature_agent_present": True,
        },
        "results": results,
        "interpretation": interpretation,
    }

    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"redacted_report={output_path}")

    return report


def main(argv=None):
    args = parse_args(argv)
    try:
        report = run_diagnostics(args.output)
    except DiagnosticConfigurationError as error:
        print(f"configuration_error={error}")
        return 2

    return 1 if any(result.get("error") for result in report["results"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
