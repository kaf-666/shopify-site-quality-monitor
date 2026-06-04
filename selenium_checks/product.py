import os
import sys
import time

from selenium.webdriver.common.by import By
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from common.driver import init_driver

from common.utils import (
    create_dirs,
    locate_element,
    open_page_with_retry,
    screenshot_root
)

from common.checks import (
    dom_check,
    hide_dynamic_elements
)

from common.capture import (
    capture_modules,
    wait_for_images,
    wait_for_reviews,
    wait_for_layout_stable
)

from common.test_results import add_result, clear_results, write_results
from common.visual import build_result, process_results


URL = (
    "https://www.mondressy.com/"
    "products/a-line-princess-sleeveless-tea-length-wedding-guest-dresses-mon2311613"
)

SITE = "mondressy_US"
SUITE = "visual"
PAGE = "product"

ROOT_DIR = screenshot_root(SITE)

PAGE_DIR = os.path.join(ROOT_DIR, "product")

BASELINE_DIR = os.path.join(PAGE_DIR, "baseline")
CURRENT_DIR = os.path.join(PAGE_DIR, "current")
DIFF_DIR = os.path.join(PAGE_DIR, "diff")


MODULES = {
    # PDP 主模块：商品图、商品信息和加购按钮。

    "gallery": (
        "xpath",
        "//*[contains(@class,'product__photos')]"
    ),

    "info": (
        "xpath",
        "//*[contains(@class,'product-single__meta')]"
    ),

    "add_to_cart": (
        "xpath",
        "//button[@name='add']"
    )
}


def check_add_to_cart(driver):
    """检查 Add To Cart 按钮是否可用。"""

    print("\n🛒 Add To Cart状态")
    failures = []

    try:

        btn = driver.find_element(
            By.XPATH,
            "//button[@name='add']"
        )

        print(
            f"enabled={btn.is_enabled()}"
        )

        if not btn.is_enabled():
            failures.append("Add To Cart按钮不可用")

    except Exception as e:

        print(f"❌ {e}")
        failures.append(f"Add To Cart状态异常: {e}")

    return failures


def check_variant_count(driver):
    """打印当前商品可选 variant 数量。"""

    variants = driver.find_elements(
        By.XPATH,
        "//*[contains(@class,'variant')]//input"
    )

    print(f"\n🎨 Variant数量: {len(variants)}")


def test_variants(driver):
    """切换前几个 variant，并分别截取商品图区域。"""

    print("\n🎨 Variant检测")

    results = {}
    failures = []

    try:

        variants = driver.find_elements(
            By.XPATH,
            "//*[contains(@class,'variant')]//input"
        )

        if not variants:

            print("⚠ 未找到variant")

            failures.append("Variant未找到")
            return results, failures

        variant_count = min(3, len(variants))

        for i in range(variant_count):

            paths = {

                "current": os.path.join(
                    CURRENT_DIR,
                    f"variant_{i}.png"
                ),

                "baseline": os.path.join(
                    BASELINE_DIR,
                    f"variant_{i}.png"
                ),

                "diff": os.path.join(
                    DIFF_DIR,
                    f"variant_{i}.png"
                ),
            }

            max_attempts = 3

            for attempt in range(1, max_attempts + 1):

                try:

                    variants = driver.find_elements(
                        By.XPATH,
                        "//*[contains(@class,'variant')]//input"
                    )

                    if i >= len(variants):
                        raise Exception(f"Variant {i} 不存在")

                    v = variants[i]

                    driver.execute_script("""
                        arguments[0].scrollIntoView({
                            block: 'center'
                        });
                    """, v)

                    driver.execute_script(
                        "arguments[0].click();",
                        v
                    )

                    time.sleep(2)

                    gallery = locate_element(
                        driver,
                        MODULES["gallery"]
                    )

                    driver.execute_script("""
                        arguments[0].scrollIntoView({
                            block: 'center'
                        });
                    """, gallery)

                    # ★ variant切换后重新隐藏
                    hide_dynamic_elements(driver)

                    wait_for_images(driver, gallery, timeout=10)

                    wait_for_reviews(driver, timeout=10)

                    wait_for_layout_stable(
                        driver,
                        gallery,
                        timeout=10
                    )

                    # ★ 有些插件会二次render
                    hide_dynamic_elements(driver)

                    time.sleep(0.5)

                    gallery = locate_element(
                        driver,
                        MODULES["gallery"]
                    )

                    gallery.screenshot(
                        paths["current"]
                    )

                    results[f"variant_{i}"] = paths

                    print(f"✅ Variant {i}")

                    break

                except StaleElementReferenceException as e:

                    if attempt == max_attempts:

                        print(f"❌ Variant {i} 截图失败（详见失败汇总）")

                        results[f"variant_{i}"] = {"error": f"截图失败: {e}"}

                    else:

                        print(
                            f"⚠️ Variant {i} stale, "
                            f"retry {attempt}/{max_attempts}"
                        )

                        time.sleep(1)

                except Exception as e:

                    print(f"❌ Variant {i} 截图失败（详见失败汇总）")

                    results[f"variant_{i}"] = {"error": f"截图失败: {e}"}

                    break

    except Exception as e:

        print(f"❌ Variant检测失败: {e}")
        failures.append(f"Variant检测失败: {e}")

    return results, failures



def test_add_to_cart(driver):
    """点击 Add To Cart，确认基础加购链路没有报错。"""

    print("\n🛒 Add To Cart检测")
    failures = []

    try:

        btn = driver.find_element(
            By.XPATH,
            "//button[@name='add']"
        )

        btn.click()

        time.sleep(2)

        print("✅ Add To Cart成功")

    except Exception as e:

        print(f"❌ {e}")
        failures.append(f"Add To Cart点击失败: {e}")

    return failures


def wait_for_visible(driver, locator, timeout=45):
    """等待指定 locator 对应元素变为可见。"""

    method, value = locator

    by = By.CSS_SELECTOR if method == "css" else By.XPATH

    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((by, value))
    )


def wait_for_product_page(driver):
    """等待 PDP 的关键购买区域加载完成。"""

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
            f"PDP readyState wait skipped, "
            f"state={state}, url={driver.current_url}"
        )

    # Third-party requests can keep the load event open after PDP content is ready.
    wait_for_visible(driver, MODULES["gallery"])

    wait_for_visible(driver, MODULES["info"])

    wait_for_visible(driver, MODULES["add_to_cart"])


def run():
    """执行 PDP Selenium 视觉检测并返回失败信息列表。"""
    failures = []

    create_dirs(
        BASELINE_DIR,
        CURRENT_DIR,
        DIFF_DIR
    )

    driver = None

    try:
        driver = init_driver()

        open_page_with_retry(driver, URL, wait_for_product_page, "PDP")

        time.sleep(2)

        failures.extend(dom_check(driver, MODULES))

        failures.extend(check_add_to_cart(driver))

        check_variant_count(driver)

        hide_dynamic_elements(driver)

        module_results = capture_modules(
            driver,
            MODULES,
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR
        )

        variant_results, variant_failures = test_variants(driver)
        failures.extend(variant_failures)

        failures.extend(test_add_to_cart(driver))

        failures.extend(process_results(module_results, SITE, SUITE, PAGE))

        failures.extend(process_results(variant_results, SITE, SUITE, PAGE))

    except Exception as e:

        error = f"Selenium运行异常: {type(e).__name__}: {e}"
        failures.append(f"PDP: {error}")
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
        print("\n❌ PDP Selenium 失败汇总")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
