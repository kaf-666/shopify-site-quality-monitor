from playwright_checks.core.config_loader import locator
from playwright_checks.pages.base_page import BasePage
from playwright_checks.utils.waits import selector_for


class ProductPage(BasePage):
    page_name = "product"

    @property
    def variant_inputs_locator(self):
        return locator(self.config["variant_inputs"])

    def variant_inputs(self):
        return self.page.locator(selector_for(self.variant_inputs_locator))

    def wait_until_ready(self, page=None):
        target = page or self.page
        super().wait_until_ready(target)
        self.wait_for_module("gallery")
        self.wait_for_module("info")
        self.wait_for_module("add_to_cart")
