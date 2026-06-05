from playwright_checks.pages.base_page import BasePage


class HomePage(BasePage):
    page_name = "home"

    def wait_until_ready(self, page=None):
        target = page or self.page
        super().wait_until_ready(target)
        self.wait_for_module("header_1")
        self.wait_for_module("banner")
