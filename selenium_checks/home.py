import os
import sys
import time
import tempfile

import numpy as np
from PIL import Image
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait

from common.driver import init_driver

from common.utils import (
    create_dirs,
    locate_element,
    open_page_with_retry,
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

from common.test_results import add_result, clear_results, write_results
from common.visual import build_result, process_results


URL = "https://www.mondressy.com"

# 当前脚本的结果会按 site/suite/page 写入统一报告。
SITE = "mondressy_US"
SUITE = "visual"
PAGE = "home"

ROOT_DIR = screenshot_root(SITE)

PAGE_DIR = os.path.join(ROOT_DIR, "home")

BASELINE_DIR = os.path.join(PAGE_DIR, "baseline")
CURRENT_DIR = os.path.join(PAGE_DIR, "current")
DIFF_DIR = os.path.join(PAGE_DIR, "diff")


MODULES = {
    # 首页主模块截图点：头部、首屏 banner、集合区和常用图标入口。
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
    # 第三方插件独立检测，避免它们影响主模块截图判断。

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
    """检查首页插件是否能找到且可见。"""

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
    """分别截取插件区域，后续作为独立视觉用例对比。"""

    print("\n📸 插件截图")

    results = {}

    for name, locator in PLUGINS.items():

        paths = build_paths(
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR,
            name
        )

        max_attempts = 2

        for attempt in range(1, max_attempts + 1):

            try:

                capture_stable_plugin(
                    driver,
                    name,
                    locator,
                    paths["current"],
                    timeout=20
                )

                results[name] = paths

                print(f"✅ [{name}]")

                break

            except Exception as e:

                if attempt == max_attempts:

                    print(f"❌ [{name}] 插件截图失败（详见失败汇总）")

                    results[name] = {"error": f"插件截图失败: {e}"}

                else:

                    print(
                        f"⚠️ [{name}] 插件截图未稳定, "
                        f"retry {attempt}/{max_attempts}"
                    )

                    time.sleep(1)

    return results


def images_close(path1, path2, threshold=0.001):
    """判断两张临时截图是否足够接近，用于确认插件渲染稳定。"""
    img1 = np.array(Image.open(path1).convert("RGB"))
    img2 = np.array(Image.open(path2).convert("RGB"))

    if img1.shape != img2.shape:
        return False

    diff = np.abs(img1.astype(int) - img2.astype(int))
    changed = (diff > 25).any(axis=2)
    return changed.sum() / changed.size <= threshold


def plugin_image_ready(name, path):
    """检查插件截图是否已经渲染到可对比状态。"""
    if name != "currency":
        return True

    image = np.array(Image.open(path).convert("RGB"))
    white_ratio = ((image > 235).all(axis=2)).sum() / image.shape[0] / image.shape[1]
    return white_ratio >= 0.4


def normalize_plugin_for_screenshot(driver, name, element):
    """对容易受样式影响的插件做截图前归一化。"""
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
    """重复截取插件，直到连续两次画面足够接近。"""
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
    """停止首页轮播并固定到第一张，减少自动轮播造成的 diff。"""
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


def wait_for_home_page(driver):
    """等待首页关键模块可见。"""

    try:

        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) in ("interactive", "complete")
        )

    except TimeoutException:

        state = driver.execute_script(
            "return document.readyState"
        )

        print(
            f"Home readyState wait skipped, "
            f"state={state}, url={driver.current_url}"
        )

    locate_element(driver, MODULES["header_1"])

    locate_element(driver, MODULES["banner"])


def run():
    """执行首页 Selenium 视觉检测并返回失败信息列表。"""
    failures = []

    create_dirs(
        BASELINE_DIR,
        CURRENT_DIR,
        DIFF_DIR
    )

    driver = None

    try:
        driver = init_driver()

        open_page_with_retry(driver, URL, wait_for_home_page, "Home")

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
            DIFF_DIR,
            require_reviews=False
        )

        failures.extend(process_results(module_results, SITE, SUITE, PAGE))

        failures.extend(process_results(plugin_results, SITE, SUITE, PAGE))

    except Exception as e:

        error = f"Selenium运行异常: {type(e).__name__}: {e}"
        failures.append(f"首页: {error}")
        add_result(
            build_result(
                SITE,
                SUITE,
                PAGE,
                "runtime",
                "failed",
                None,
                error=error
            )
        )

    finally:

        if driver:
            driver.quit()

    return failures


if __name__ == "__main__":

    clear_results()
    page_failures = run()
    results_file = write_results()
    print(f"\n📄 视觉测试结果: {results_file}")
    if page_failures:
        print("\n❌ 首页 Selenium 失败汇总")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
