from playwright_checks.pages.base_page import BasePage
from playwright_checks.core.config_loader import locator_map


class HomePage(BasePage):
    page_name = "home"

    @property
    def plugins(self):
        return locator_map(self.config.get("plugins", {}))

    def wait_until_ready(self, page=None):
        target = page or self.page
        super().wait_until_ready(target)
        self.wait_for_module("header_1")
        self.wait_for_module("banner")
