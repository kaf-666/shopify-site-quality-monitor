import os
import sys
import time
import tempfile

import numpy as np
from PIL import Image
from selenium.webdriver.support.ui import WebDriverWait

from common.driver import init_driver

from common.utils import (
    create_dirs,
    locate_element,
    build_paths,
    screenshot_root
)

from common.checks import (
    dom_check,
    hide_dynamic_elements,

)

from common.capture import (
    capture_modules,
    wait_for_layout_stable
)

from common.visual import process_results


URL = "https://www.mondressy.com"

ROOT_DIR = screenshot_root("mondressy_US")

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
    failures = []

    for name, locator in PLUGINS.items():

        try:

            el = locate_element(driver, locator)

            visible = el.is_displayed()

            print(
                f"✅ {name} visible={visible}"
            )

            if not visible:
                failures.append(f"插件 [{name}] visible=False")

        except Exception as e:

            print(f"❌ {name} 插件异常（详见失败汇总）")
            failures.append(f"插件 [{name}] 异常: {e}")

    return failures


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

            capture_stable_plugin(driver, name, locator, paths["current"])

            results[name] = paths

            print(f"✅ [{name}]")

        except Exception as e:

            print(f"❌ [{name}] 插件截图失败（详见失败汇总）")

            results[name] = {"error": f"插件截图失败: {e}"}

    return results


def images_close(path1, path2, threshold=0.001):
    img1 = np.array(Image.open(path1).convert("RGB"))
    img2 = np.array(Image.open(path2).convert("RGB"))

    if img1.shape != img2.shape:
        return False

    diff = np.abs(img1.astype(int) - img2.astype(int))
    changed = (diff > 25).any(axis=2)
    return changed.sum() / changed.size <= threshold


def plugin_image_ready(name, path):
    if name != "currency":
        return True

    image = np.array(Image.open(path).convert("RGB"))
    white_ratio = ((image > 235).all(axis=2)).sum() / image.shape[0] / image.shape[1]
    return white_ratio >= 0.4


def normalize_plugin_for_screenshot(driver, name, element):
    if name != "currency":
        return

    driver.execute_script(
        """
        arguments[0].style.setProperty('background', '#fff', 'important');
        arguments[0].style.setProperty('background-color', '#fff', 'important');
        arguments[0].style.setProperty('box-shadow', 'inset 0 0 0 9999px #fff', 'important');
        arguments[0].style.setProperty('color', '#000', 'important');
        arguments[0].querySelectorAll('svg').forEach(function(svg) {
            svg.style.setProperty('fill', '#000', 'important');
        });
        void arguments[0].offsetHeight;
        """,
        element,
    )


def capture_stable_plugin(driver, name, locator, output_path, timeout=10):
    end_time = time.time() + timeout
    previous_path = None

    while time.time() < end_time:
        el = locate_element(driver, locator)
        normalize_plugin_for_screenshot(driver, name, el)
        time.sleep(0.2)

        if not wait_for_layout_stable(driver, el, timeout=2):
            time.sleep(0.3)
            continue

        current_path = os.path.join(
            tempfile.gettempdir(),
            f"plugin_probe_{time.time_ns()}.png"
        )
        el.screenshot(current_path)

        if not plugin_image_ready(name, current_path):
            previous_path = None
            time.sleep(0.3)
            continue

        if previous_path and images_close(previous_path, current_path):
            os.replace(current_path, output_path)
            return

        previous_path = current_path
        time.sleep(0.3)

    if previous_path:
        os.replace(previous_path, output_path)
        return

    raise Exception("插件截图未稳定")


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
    failures = []

    create_dirs(
        BASELINE_DIR,
        CURRENT_DIR,
        DIFF_DIR
    )

    driver = None

    try:
        driver = init_driver()

        driver.get(URL)

        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) == "complete"
        )

        time.sleep(2)

        failures.extend(dom_check(driver, MODULES))

        failures.extend(check_plugins(driver))

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

        failures.extend(process_results(module_results))

        failures.extend(process_results(plugin_results))

    except Exception as e:

        failures.append(f"首页: Selenium运行异常: {e}")

    finally:

        if driver:
            driver.quit()

    return failures


if __name__ == "__main__":

    page_failures = run()
    if page_failures:
        print("\n❌ 首页 Selenium 失败汇总")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
