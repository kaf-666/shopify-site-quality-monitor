from playwright_checks.core.config_loader import locator
from playwright_checks.pages.base_page import BasePage
from playwright_checks.utils.waits import selector_for


class ProductPage(BasePage):
    page_name = "product"

    @property
    def variant_inputs_locator(self):
        return locator(self.config["variant_inputs"])

    @property
    def variant_gallery_options_locator(self):
        if not self.config.get("variant_gallery_options"):
            return None
        return locator(self.config["variant_gallery_options"])

    def variant_inputs(self):
        return self.page.locator(selector_for(self.variant_inputs_locator))

    def variant_gallery_options(self):
        if not self.variant_gallery_options_locator:
            return None
        return self.page.locator(selector_for(self.variant_gallery_options_locator))

    def content_locator(self, name):
        configured = self.config.get("content_checks", {}).get(name)
        if configured:
            return locator(configured)

        fallback_selectors = {
            "title": (
                "h1, .product-single__title, .product__title, "
                "[class*='product-title'], [class*='product__title']"
            ),
            "price": (
                ".price, .product__price, .product-single__price, "
                "[class*='price']"
            ),
        }
        return "css", fallback_selectors[name]

    def wait_until_ready(self, page=None):
        target = page or self.page
        super().wait_until_ready(target)
        self.wait_for_module("gallery")
        self.wait_for_module("info")
        self.wait_for_module("add_to_cart")
