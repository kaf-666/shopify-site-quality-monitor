from playwright_checks.core.config_loader import locator
from playwright_checks.pages.base_page import BasePage
from playwright_checks.utils.waits import selector_for


class CollectionPage(BasePage):
    page_name = "collection"

    @property
    def product_card_locator(self):
        return locator(self.config["product_card"])

    @property
    def expected_count(self):
        return self.config.get("expected_count")

    def product_cards(self):
        return self.page.locator(selector_for(self.product_card_locator))

    def wait_until_ready(self, page=None):
        target = page or self.page
        super().wait_until_ready(target)
        self.wait_for_module("product_grid")
        self.wait_for_module("filter")
        self.product_cards().first.wait_for(state="visible", timeout=45000)
