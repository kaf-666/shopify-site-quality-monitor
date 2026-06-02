import os
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait

from common.driver import init_driver

from common.utils import (
    create_dirs,
    build_paths
)

from common.checks import (
    dom_check,
    hide_dynamic_elements
)

from common.capture import capture_modules

from common.visual import process_results


URL = (
    "https://www.mondressy.com/"
    "collections/wedding-guest-dresses"
)

ROOT_DIR = "screenshots/mondressy_US"

PAGE_DIR = os.path.join(ROOT_DIR, "collection")

BASELINE_DIR = os.path.join(PAGE_DIR, "baseline")
CURRENT_DIR = os.path.join(PAGE_DIR, "current")
DIFF_DIR = os.path.join(PAGE_DIR, "diff")


MODULES = {

    "product_grid": (
        "xpath",
        "//*[contains(@id,'shopify-section-template-color_collection')]"
    ),

    "filter": (
        "xpath",
        "(//*[contains(@class,'collection-filter')])[1]"
    ),

    "pagination": (
        "xpath",
        "//*[contains(@class,'pagination')]"
    )
}


PRODUCT_CARD = (
    "css",
    ".grid__item.grid-product"
)


def is_element_covered(driver, element):

    return driver.execute_script("""

    const el = arguments[0];

    const rect = el.getBoundingClientRect();

    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;

    const topEl = document.elementFromPoint(x, y);

    return !el.contains(topEl);

    """, element)


def scroll_to_load_all(driver, locator, timeout=10, max_scrolls=20):
    """滚动页面直到所有商品加载完成"""
    last_count = 0
    stable_count = 0
    end_time = time.time() + timeout

    for _ in range(max_scrolls):
        if time.time() > end_time:
            break

        # 当前已加载的商品数量
        current_count = driver.execute_script("""
            return document.querySelectorAll(
                arguments[0]
            ).length;
        """, locator)

        if current_count == last_count:
            stable_count += 1
            # 连续3次数量不变，认为加载完成
            if stable_count >= 3:
                return current_count
        else:
            stable_count = 0
            last_count = current_count

        # 往下滚一屏
        driver.execute_script(
            "window.scrollBy(0, window.innerHeight);"
        )
        time.sleep(0.5)

    return last_count



def get_product_cards(driver):

    method, value = PRODUCT_CARD

    by = By.CSS_SELECTOR if method == "css" else By.XPATH

    return driver.find_elements(by, value)



EXPECTED_COUNT = 40

def check_product_count(driver):

    print("\n🛍 商品数量检测")

    scroll_to_load_all(driver, ".grid__item.grid-product", timeout=15)

    cards = get_product_cards(driver)

    count = len(cards)

    if count == EXPECTED_COUNT:
        print(f"✅ 商品数量正确: {count}")
    else:
        print(f"❌ 商品数量异常: {count}，期望: {EXPECTED_COUNT}")



def scroll_to_center(driver, el):

    driver.execute_script(
        """
        arguments[0].scrollIntoView({
            block: 'center',
            inline: 'center'
        });
        """,
        el
    )

    time.sleep(0.5)


def capture_product_cards(driver):

    print("\n📸 商品卡片截图")

    results = {}

    cards = get_product_cards(driver)[:8]

    for i, card in enumerate(cards):

        paths = build_paths(
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR,
            f"product_{i}"
        )

        try:

            scroll_to_center(driver, card)

            card.screenshot(paths["current"])

            results[f"product_{i}"] = paths

            print(f"✅ product_{i}")

        except Exception as e:

            print(f"❌ product_{i}: {e}")

            results[f"product_{i}"] = None

    return results


def capture_hover_cards(driver):

    print("\n🖱 Hover截图")

    results = {}

    cards = get_product_cards(driver)[:8]

    for i, card in enumerate(cards):

        paths = build_paths(
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR,
            f"hover_{i}"
        )

        try:

            scroll_to_center(driver, card)

            ActionChains(driver)\
                .move_to_element(card)\
                .perform()

            time.sleep(1)

            card.screenshot(paths["current"])

            results[f"hover_{i}"] = paths

            print(f"✅ hover_{i}")

        except Exception as e:

            print(f"❌ hover_{i}: {e}")

            results[f"hover_{i}"] = None

    return results


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

        check_product_count(driver)

        hide_dynamic_elements(driver)

        module_results = capture_modules(
            driver,
            MODULES,
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR
        )

        product_results = capture_product_cards(driver)

        hover_results = capture_hover_cards(driver)

        process_results(module_results)

        process_results(product_results)

        process_results(hover_results)

    finally:

        driver.quit()


if __name__ == "__main__":

    run()