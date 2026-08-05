import os
import sys
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from playwright_checks.core.config_loader import (  # noqa: E402
    get_access_denied_patterns,
    get_page_config,
    load_settings,
    load_site_config,
    locator,
    locator_map,
)
from playwright_checks.core.paths import legacy_screenshot_root
from playwright_checks.core.viewport import get_current_viewport_name


class AccessBlockedError(Exception):
    """Raised when the browser lands on an anti-bot or access-denied page."""


class TerminalMainDocumentError(AccessBlockedError):
    """Raised when the main document status makes visual checks unsafe."""

    def __init__(self, status, url):
        self.status = status
        self.url = url
        super().__init__(
            _redact_runtime_error(
                f"Terminal main document response: status={status}, url={url}"
            )
        )


TRANSIENT_VERIFICATION_PATTERNS = [
    "just a moment",
    "connection needs to be verified",
    "verification successful. waiting",
]


def screenshot_root(site):
    return str(legacy_screenshot_root(site))


def selector_for(locator_value):
    method, value = locator_value
    if method == "css":
        return value
    if method == "xpath":
        return f"xpath={value}"
    raise ValueError(f"Unsupported locator method: {method!r}")


def locate_element(page, locator_value):
    element = page.locator(selector_for(locator_value)).first
    element.wait_for(state="visible", timeout=10000)
    return element


def wait_for_visible(page, locator_value, timeout=45000):
    element = page.locator(selector_for(locator_value)).first
    element.wait_for(state="visible", timeout=timeout)
    return element


def wait_for_page_load(page, timeout=15000, label="page"):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        state = page.evaluate("document.readyState").strip()
        print(
            f"{label} readyState wait skipped, state={state}, "
            f"url={_redact_runtime_error(page.url)}"
        )
        return False


def assert_page_not_blocked(page, timeout=20):
    end_time = time.time() + timeout
    patterns = get_access_denied_patterns()
    transient_seen = None

    while time.time() < end_time:
        title = page.title() or ""
        body_text = page.locator("body").inner_text(timeout=1000)[:2000]
        haystack = f"{title}\n{body_text}".lower()

        for pattern in TRANSIENT_VERIFICATION_PATTERNS:
            if pattern in haystack:
                transient_seen = pattern
                time.sleep(1)
                break
        else:
            transient_seen = None

            for pattern in patterns:
                if pattern.lower() in haystack:
                    raise AccessBlockedError(
                        _redact_runtime_error(
                            "Access blocked by verification page: "
                            f"{pattern!r}, url={page.url}, title={title!r}"
                        )
                    )

            if body_text.strip():
                return

        time.sleep(0.2)

    if transient_seen:
        raise AccessBlockedError(
            _redact_runtime_error(
                "Verification page did not finish: "
                f"{transient_seen!r}, url={page.url}, "
                f"title={page.title()!r}"
            )
        )


def open_page_with_retry(
    page,
    url,
    wait_until_ready,
    label="page",
    attempts=3,
    delay=2,
    on_navigation_attempt=None,
    attempt_offset=0,
    navigation_sequence=1,
    navigation_attempt_phase=None,
):
    navigation_attempts = []
    for sequence_attempt in range(1, attempts + 1):
        attempt = attempt_offset + sequence_attempt
        response = None
        phase_context = (
            navigation_attempt_phase(sequence_attempt)
            if navigation_attempt_phase
            else nullcontext()
        )
        with phase_context:
            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                status = response.status if response else None
                if is_terminal_main_document_status(status):
                    raise TerminalMainDocumentError(status, page.url)
                assert_page_not_blocked(page)
                wait_until_ready(page)
                time.sleep(1)
                assert_page_not_blocked(page)
                wait_until_ready(page)
                final_url = page.url
                redirect_chain = _response_redirect_chain(response, url)
                attempt_result = {
                    "attempt": attempt,
                    "navigation_sequence": navigation_sequence,
                    "sequence_attempt": sequence_attempt,
                    "requested_url": url,
                    "state": "succeeded",
                    "final_url": final_url,
                    "status": status,
                    "redirected": final_url.rstrip("/") != url.rstrip("/"),
                    "redirect_chain": redirect_chain,
                    "timestamp": time.time(),
                }
                navigation_attempts.append(attempt_result)
                if on_navigation_attempt:
                    _safe_navigation_callback(
                        on_navigation_attempt,
                        attempt_result,
                    )
                return {
                    "requested_url": url,
                    "final_url": final_url,
                    "status": status,
                    "main_document_status": status,
                    "redirected": attempt_result["redirected"],
                    "redirect_chain": redirect_chain,
                    "navigation_attempts": navigation_attempts,
                    "navigation_error": None,
                }
            except AccessBlockedError as error:
                navigation_attempts.append(
                    _report_navigation_attempt(
                        on_navigation_attempt,
                        attempt,
                        navigation_sequence,
                        sequence_attempt,
                        url,
                        page,
                        response,
                        error,
                    )
                )
                raise
            except Exception as error:
                navigation_attempts.append(
                    _report_navigation_attempt(
                        on_navigation_attempt,
                        attempt,
                        navigation_sequence,
                        sequence_attempt,
                        url,
                        page,
                        response,
                        error,
                    )
                )
                if sequence_attempt == attempts:
                    raise
                print(
                    f"{label} page not ready, retry "
                    f"{sequence_attempt}/{attempts}"
                )
                time.sleep(delay)


def _report_navigation_attempt(
    callback,
    attempt,
    navigation_sequence,
    sequence_attempt,
    requested_url,
    page,
    response,
    error,
):
    try:
        final_url = page.url
    except Exception:
        final_url = None
    attempt_result = {
        "attempt": attempt,
        "navigation_sequence": navigation_sequence,
        "sequence_attempt": sequence_attempt,
        "requested_url": requested_url,
        "state": "failed",
        "final_url": final_url,
        "status": response.status if response else None,
        "redirected": bool(
            final_url
            and final_url.rstrip("/") != requested_url.rstrip("/")
        ),
        "redirect_chain": _response_redirect_chain(
            response,
            requested_url,
        ),
        "error_type": type(error).__name__,
        "error_message": _redact_runtime_error(error),
        "timestamp": time.time(),
    }
    if callback:
        _safe_navigation_callback(
            callback,
            attempt_result,
        )
    return attempt_result


def is_terminal_main_document_status(status):
    return status in {401, 403, 429} or (
        isinstance(status, int) and status >= 500
    )


def _response_redirect_chain(response, requested_url):
    if response is None:
        return []

    chain = []
    try:
        request = response.request
    except Exception:
        request = None

    while request is not None:
        try:
            request_response = (
                response
                if request is response.request
                else request.response()
            )
            status = (
                request_response.status
                if request_response is not None
                else None
            )
        except Exception:
            status = None
        chain.append(
            {
                "url": getattr(request, "url", requested_url),
                "status": status,
            }
        )
        try:
            request = request.redirected_from
        except Exception:
            request = None

    if chain:
        chain.reverse()
        return chain

    return [
        {
            "url": getattr(response, "url", requested_url),
            "status": getattr(response, "status", None),
        }
    ]


def _safe_navigation_callback(callback, attempt_result):
    try:
        callback(attempt_result)
    except Exception as error:
        print(
            "Runtime navigation observer degraded without changing "
            f"navigation: {type(error).__name__}: "
            f"{_redact_runtime_error(error)}"
        )


def _redact_runtime_error(error):
    from playwright_checks.runtime.evidence import redact_text

    return redact_text(str(error))


def create_dirs(*dirs):
    for directory in dirs:
        os.makedirs(directory, exist_ok=True)


def build_paths(current_dir, baseline_dir, diff_dir, name, legacy_baseline_dir=None):
    baseline = os.path.join(baseline_dir, f"{name}.png")
    legacy_baseline = (
        os.path.join(legacy_baseline_dir, f"{name}.png")
        if legacy_baseline_dir
        else None
    )

    active_baseline = baseline
    if (
        legacy_baseline
        and not os.path.exists(baseline)
        and os.path.exists(legacy_baseline)
    ):
        active_baseline = legacy_baseline

    attempt = _attempt_from_path(current_dir)
    token = uuid.uuid4().hex
    return {
        "attempt": attempt,
        "current": os.path.join(
            current_dir,
            f"{name}-attempt-{attempt}-{token}.png",
        ),
        "baseline": active_baseline,
        "target_baseline": baseline,
        "legacy_baseline": legacy_baseline,
        "diff": os.path.join(
            diff_dir,
            f"{name}-attempt-{attempt}-{token}-diff.png",
        ),
    }


def _attempt_from_path(value):
    for part in reversed(Path(value).parts):
        if part.startswith("attempt-"):
            suffix = part.removeprefix("attempt-")
            if suffix.isdigit():
                return int(suffix)
    return 1
