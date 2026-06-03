import os
import time

from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from common.driver import init_driver

from common.utils import (
    create_dirs,
    locate_element,
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

from common.visual import process_results


URL = (
    "https://www.mondressy.com/"
    "products/a-line-princess-sleeveless-tea-length-wedding-guest-dresses-mon2311613"
)

ROOT_DIR = screenshot_root("mondressy_US")

PAGE_DIR = os.path.join(ROOT_DIR, "product")

BASELINE_DIR = os.path.join(PAGE_DIR, "baseline")
CURRENT_DIR = os.path.join(PAGE_DIR, "current")
DIFF_DIR = os.path.join(PAGE_DIR, "diff")


MODULES = {

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

    print("\n🛒 Add To Cart状态")

    try:

        btn = driver.find_element(
            By.XPATH,
            "//button[@name='add']"
        )

        print(
            f"enabled={btn.is_enabled()}"
        )

    except Exception as e:

        print(f"❌ {e}")


def check_variant_count(driver):

    variants = driver.find_elements(
        By.XPATH,
        "//*[contains(@class,'variant')]//input"
    )

    print(f"\n🎨 Variant数量: {len(variants)}")


def test_variants(driver):

    print("\n🎨 Variant检测")

    results = {}

    try:

        variants = driver.find_elements(
            By.XPATH,
            "//*[contains(@class,'variant')]//input"
        )

        if not variants:

            print("⚠ 未找到variant")

            return results

        for i, v in enumerate(variants[:3]):

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

            try:

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

            except Exception as e:

                print(f"❌ Variant {i}: {e}")

                results[f"variant_{i}"] = None

    except Exception as e:

        print(f"❌ Variant检测失败: {e}")

    return results



def test_add_to_cart(driver):

    print("\n🛒 Add To Cart检测")

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


def wait_for_visible(driver, locator, timeout=45):

    method, value = locator

    by = By.CSS_SELECTOR if method == "css" else By.XPATH

    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((by, value))
    )


def wait_for_product_page(driver):

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

    create_dirs(
        BASELINE_DIR,
        CURRENT_DIR,
        DIFF_DIR
    )

    driver = init_driver()

    try:

        driver.get(URL)

        wait_for_product_page(driver)

        time.sleep(2)

        dom_check(driver, MODULES)

        check_add_to_cart(driver)

        check_variant_count(driver)

        hide_dynamic_elements(driver)

        module_results = capture_modules(
            driver,
            MODULES,
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR
        )

        variant_results = test_variants(driver)

        test_add_to_cart(driver)

        process_results(module_results)

        process_results(variant_results)

    finally:

        driver.quit()


if __name__ == "__main__":

    run()
