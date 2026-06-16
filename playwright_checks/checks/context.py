from dataclasses import dataclass, field

from playwright_checks.core.config_loader import (
    get_page_config,
    load_site_config,
    locator,
    locator_map,
)
from playwright_checks.core.paths import page_paths


@dataclass
class PageCheckContext:
    page_name: str
    suite: str = "visual"
    site_config: dict = field(default_factory=load_site_config)

    def __post_init__(self):
        self.page_config = get_page_config(self.page_name, self.site_config)
        self.url = self.page_config["url"]
        self.site = self.site_config["site"]
        paths = page_paths(self.site, self.page_name)
        self.run_id = paths["run_id"]
        self.root_dir = paths["root_dir"]
        self.page_dir = paths["page_dir"]
        self.baseline_dir = paths["baseline_dir"]
        self.legacy_baseline_dir = paths["legacy_baseline_dir"]
        self.current_dir = paths["current_dir"]
        self.diff_dir = paths["diff_dir"]
        self.modules = locator_map(self.page_config.get("modules", {}))
        self.capture_exclude = set(self.page_config.get("capture_exclude", []))

    def module_locators_for_capture(self):
        return {
            name: module_locator
            for name, module_locator in self.modules.items()
            if name not in self.capture_exclude
        }

    def locator(self, key, default=None):
        value = self.page_config.get(key, default)
        return locator(value) if value else None

    def locator_map(self, key):
        return locator_map(self.page_config.get(key, {}))
