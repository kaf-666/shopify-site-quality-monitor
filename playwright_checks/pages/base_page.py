from playwright_checks.core.config_loader import get_page_config, load_site_config, locator_map
from playwright_checks.runtime.session import (
    FailOpenRuntimeHealthSession,
    RuntimeHealthSession,
)
from playwright_checks.runtime.evidence import redact_text
from playwright_checks.utils.waits import locate_element, open_page_with_retry, wait_for_page_load, wait_for_visible
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError


TRANSIENT_PAGE_READ_ERRORS = (
    "execution context was destroyed",
    "most likely because of a navigation",
    "target closed",
    "target page, context or browser has been closed",
)


class BasePage:
    page_name = None

    def __init__(self, page, site_config=None):
        if not self.page_name:
            raise ValueError("Page objects must define page_name")

        self.page = page
        self.site_config = site_config or load_site_config()
        self.config = get_page_config(self.page_name, self.site_config)
        self.url = self.config["url"]
        self.modules = locator_map(self.config.get("modules", {}))
        self.dom_presence = locator_map(self.config.get("dom_presence", {}))
        try:
            self.runtime = RuntimeHealthSession(
                page=self.page,
                site_config=self.site_config,
                page_config=self.config,
                page_name=self.page_name,
            )
        except Exception as error:
            self.runtime = FailOpenRuntimeHealthSession(
                page=self.page,
                site_config=self.site_config,
                page_name=self.page_name,
                error=error,
            )
        self.runtime.start_before_navigation()

    def open(self):
        try:
            navigation_context = self.runtime.begin_navigation()
        except Exception as runtime_error:
            navigation_context = {
                "attempt_offset": 0,
                "navigation_sequence": 1,
            }
            print(
                "Runtime navigation sequence degraded without changing "
                f"navigation: {redact_text(str(runtime_error))}"
            )
        try:
            result = open_page_with_retry(
                self.page,
                self.url,
                self.wait_until_ready,
                self.page_name,
                on_navigation_attempt=self.runtime.record_navigation_attempt,
                attempt_offset=navigation_context.get("attempt_offset", 0),
                navigation_sequence=navigation_context.get(
                    "navigation_sequence",
                    1,
                ),
                navigation_attempt_phase=(
                    self.runtime.navigation_attempt_phase
                ),
            )
        except Exception as error:
            try:
                self.runtime.complete_navigation(error=error)
            except Exception as runtime_error:
                print(
                    "Runtime navigation finalization degraded without "
                    f"changing navigation: {type(runtime_error).__name__}: "
                    f"{redact_text(str(runtime_error))}"
                )
            raise
        try:
            self.runtime.complete_navigation(result)
        except Exception as runtime_error:
            print(
                "Runtime navigation finalization degraded without changing "
                f"navigation: {type(runtime_error).__name__}: "
                f"{redact_text(str(runtime_error))}"
            )
        return result

    def wait_until_ready(self, page=None):
        target = page or self.page
        wait_for_page_load(target, label=self.page_name)
        self.assert_expected_keywords(target)

    def assert_expected_keywords(self, page=None):
        keywords = self.config.get("expected_keywords", [])
        if isinstance(keywords, str):
            keywords = [keywords]

        keywords = [str(keyword).strip().lower() for keyword in keywords if keyword]
        if not keywords:
            return

        target = page or self.page
        title, body_text = self._read_page_identity(target)
        haystack = f"{target.url}\n{title}\n{body_text}".lower()

        if any(keyword in haystack for keyword in keywords):
            return

        raise AssertionError(
            f"{self.page_name} page identity check failed; "
            f"expected one of {keywords!r}, url={target.url!r}, title={title!r}"
        )

    def _read_page_identity(self, target, attempts=3):
        last_error = None

        for attempt in range(1, attempts + 1):
            try:
                self._wait_for_identity_readiness(target)
                title = target.title() or ""
                body = target.locator("body")
                body.wait_for(state="attached", timeout=5000)
                body_text = body.inner_text(timeout=3000)[:5000]
                return title, body_text
            except (PlaywrightError, PlaywrightTimeoutError) as error:
                last_error = error
                if (
                    not self._is_transient_page_read_error(error)
                    or attempt == attempts
                ):
                    raise

                print(
                    f"{self.page_name} page identity read interrupted by "
                    f"navigation, retry {attempt}/{attempts - 1}"
                )
                self._wait_for_identity_readiness(target)

        raise last_error

    @staticmethod
    def _is_transient_page_read_error(error):
        message = str(error).lower()
        return any(pattern in message for pattern in TRANSIENT_PAGE_READ_ERRORS)

    @staticmethod
    def _wait_for_identity_readiness(target):
        try:
            target.wait_for_load_state("domcontentloaded", timeout=10000)
        except PlaywrightTimeoutError:
            pass

        try:
            target.locator("body").wait_for(state="attached", timeout=5000)
        except PlaywrightTimeoutError:
            pass

    def module(self, name):
        return locate_element(self.page, self.modules[name])

    def wait_for_module(self, name, timeout=45000):
        return wait_for_visible(self.page, self.modules[name], timeout=timeout)
