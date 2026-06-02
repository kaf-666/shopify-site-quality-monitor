import os
import time

from selenium.webdriver.support.ui import WebDriverWait

from common.driver import init_driver

from common.utils import (
    create_dirs,
    locate_element,
    build_paths
)

from common.checks import (
    dom_check,
    hide_dynamic_elements,

)

from common.capture import capture_modules

from common.visual import process_results


URL = "https://www.mondressy.com"

ROOT_DIR = "screenshots/mondressy_US"

PAGE_DIR = os.path.join(ROOT_DIR, "home")

BASELINE_DIR = os.path.join(PAGE_DIR, "baseline")
CURRENT_DIR = os.path.join(PAGE_DIR, "current")
DIFF_DIR = os.path.join(PAGE_DIR, "diff")


MODULES = {
    "header_1": ("xpath", "(//*[contains(@class, 'header-group')])[1]"),
    "header_2": ("xpath", "(//*[contains(@class, 'header-group')])[2]"),
    "banner": ("xpath", "//*[contains(@class,'slideshow__slide') and contains(@class,'is-selected')]"),
    "collections": ("xpath", "(//*[contains(@class, 'shopify-section index-section')])[2]"),
    "collections_1": ("xpath", "(//*[contains(@class, 'shopify-section index-section')])[3]"),
    "user_btn": ("xpath", "//*[contains(@class, 'icon icon-user')]"),
    "cart_btn": ("xpath", "//*[contains(@class, 'icon icon-bag-minimal')]"),
    "search_btn": ("xpath", "//*[contains(@class, 'site-nav small--hide')]")

}


PLUGINS = {

    "wishlist": (
        "xpath",
        "//button[@aria-label='Open Wishlist Details']"
    ),

    "currency": (
        "xpath",
        "//*[@id='wssccSelected']"
    )
}


def check_plugins(driver):

    print("\n🔌 插件检测")

    for name, locator in PLUGINS.items():

        try:

            el = locate_element(driver, locator)

            visible = el.is_displayed()

            print(
                f"✅ {name} visible={visible}"
            )

        except Exception as e:

            print(f"❌ {name}: {e}")


def capture_plugins(driver):

    print("\n📸 插件截图")

    results = {}

    for name, locator in PLUGINS.items():

        paths = build_paths(
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR,
            name
        )

        try:

            el = locate_element(driver, locator)

            el.screenshot(paths["current"])

            results[name] = paths

            print(f"✅ [{name}]")

        except Exception as e:

            print(f"❌ [{name}] {e}")

            results[name] = None

    return results


def stabilize_banner(driver):
    driver.execute_script("""
        document.querySelectorAll('.flickity-enabled').forEach(function(el) {
            // Flickity.data() 是官方静态方法，比 el.flickity 可靠
            var flkty = Flickity.data(el);
            if (flkty) {
                flkty.stopPlayer();
                flkty.select(0, true);  // true = 瞬间切换，无过渡动画
            }
        });
    """)
    time.sleep(1)


def run():

    create_dirs(
        BASELINE_DIR,
        CURRENT_DIR,
        DIFF_DIR
    )

    driver = init_driver()

    try:

        driver.get(URL)

        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) == "complete"
        )

        time.sleep(2)

        dom_check(driver, MODULES)

        check_plugins(driver)

        plugin_results = capture_plugins(driver)

        hide_dynamic_elements(driver)

        stabilize_banner(driver)

        module_results = capture_modules(
            driver,
            MODULES,
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR
        )

        process_results(module_results)

        process_results(plugin_results)

    finally:

        driver.quit()


if __name__ == "__main__":

    run()