from playwright_checks.core.config_loader import get_page_config, load_site_config, locator_map
from playwright_checks.utils.waits import locate_element, open_page_with_retry, wait_for_page_load, wait_for_visible


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

    def open(self):
        open_page_with_retry(
            self.page,
            self.url,
            self.wait_until_ready,
            self.page_name,
        )

    def wait_until_ready(self, page=None):
        target = page or self.page
        wait_for_page_load(target, label=self.page_name)

    def module(self, name):
        return locate_element(self.page, self.modules[name])

    def wait_for_module(self, name, timeout=45000):
        return wait_for_visible(self.page, self.modules[name], timeout=timeout)
