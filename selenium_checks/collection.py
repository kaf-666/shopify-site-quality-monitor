import os
import sys
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from PIL import Image

from common.driver import init_driver

from common.utils import (
    create_dirs,
    build_paths,
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
    wait_for_layout_stable
)

from common.test_results import add_result, clear_results, write_results
from common.visual import build_result, process_results


URL = (
    "https://www.mondressy.com/"
    "collections/wedding-guest-dresses"
)

SITE = "mondressy_US"
SUITE = "visual"
PAGE = "collection"

ROOT_DIR = screenshot_root(SITE)

PAGE_DIR = os.path.join(ROOT_DIR, "collection")

BASELINE_DIR = os.path.join(PAGE_DIR, "baseline")
CURRENT_DIR = os.path.join(PAGE_DIR, "current")
DIFF_DIR = os.path.join(PAGE_DIR, "diff")


MODULES = {
    # PLP 主模块：商品网格、筛选栏和分页区域。

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
    # 商品卡片单独截图，用于检查列表项和 hover 状态。
    "css",
    ".grid__item.grid-product"
)


def is_element_covered(driver, element):
    """判断元素中心点是否被其他元素遮挡。"""

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
    """获取当前页面已加载的商品卡片。"""

    method, value = PRODUCT_CARD

    by = By.CSS_SELECTOR if method == "css" else By.XPATH

    return driver.find_elements(by, value)


def wait_for_visible(driver, locator, timeout=45):
    """等待指定 locator 对应元素变为可见。"""

    method, value = locator

    by = By.CSS_SELECTOR if method == "css" else By.XPATH

    return WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((by, value))
    )


def wait_for_collection_page(driver):
    """等待 PLP 关键区域和商品卡片加载完成。"""

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
            f"PLP readyState wait skipped, "
            f"state={state}, url={driver.current_url}"
        )

    wait_for_visible(driver, MODULES["product_grid"])

    wait_for_visible(driver, MODULES["filter"])

    WebDriverWait(driver, 45).until(
        lambda d: len(get_product_cards(d)) > 0
    )



EXPECTED_COUNT = 40

def check_product_count(driver):
    """检查 PLP 商品数量是否符合预期。"""

    print("\n🛍 商品数量检测")
    failures = []

    scroll_to_load_all(driver, ".grid__item.grid-product", timeout=15)

    cards = get_product_cards(driver)

    count = len(cards)

    if count == EXPECTED_COUNT:
        print(f"✅ 商品数量正确: {count}")
    else:
        print(f"❌ 商品数量异常: {count}，期望: {EXPECTED_COUNT}")
        failures.append(
            f"商品数量异常: 实际 {count}, 期望 {EXPECTED_COUNT}"
        )

    return failures



def scroll_to_center(driver, el):
    """把元素滚到视口中间，减少 sticky header 遮挡。"""

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


def normalize_current_to_baseline(paths):
    """把当前截图尺寸对齐到 baseline，避免尺寸抖动放大 diff。"""
    baseline = paths["baseline"]
    current = paths["current"]

    if not os.path.exists(baseline):
        return

    with Image.open(baseline) as base_image:
        base_size = base_image.size
        base_mode = base_image.mode

    with Image.open(current) as current_image:
        if current_image.size == base_size:
            return

        normalized = Image.new(
            current_image.mode,
            base_size,
            (255, 255, 255, 0)
            if "A" in current_image.mode else (255, 255, 255)
        )

        crop = current_image.crop((
            0,
            0,
            min(current_image.width, base_size[0]),
            min(current_image.height, base_size[1])
        ))

        normalized.paste(crop, (0, 0))

        if normalized.mode != base_mode:
            normalized = normalized.convert(base_mode)

        normalized.save(current)


def mask_card_dynamic_area(paths):
    """遮掉商品卡片底部易变化区域，如价格、评分或促销信息。"""
    for key in ("baseline", "current"):
        path = paths[key]

        if not os.path.exists(path):
            continue

        with Image.open(path) as image:
            masked = image.copy()
            mask_height = max(86, int(masked.height * 0.17))
            y = max(0, masked.height - mask_height)
            color = (
                (255, 255, 255, 0)
                if "A" in masked.mode else (255, 255, 255)
            )

            masked.paste(color, (0, y, masked.width, masked.height))
            masked.save(path)


def get_product_card_by_index(driver, index):
    """按索引获取商品卡片，索引越界时抛出清晰错误。"""
    cards = get_product_cards(driver)

    if index >= len(cards):
        raise Exception(f"product card {index} 不存在")

    return cards[index]


def capture_card_image(driver, index, output_path, hover=False):
    """截取指定商品卡片，必要时先触发 hover 状态。"""
    card = get_product_card_by_index(driver, index)

    scroll_to_center(driver, card)

    hide_dynamic_elements(driver)

    if not wait_for_images(driver, card, timeout=10):
        raise Exception("等待图片超时")

    if not wait_for_layout_stable(driver, card, timeout=10):
        raise Exception("布局未稳定")

    card = get_product_card_by_index(driver, index)

    scroll_to_center(driver, card)

    hide_dynamic_elements(driver)

    if hover:
        ActionChains(driver).move_to_element(card).perform()
        time.sleep(1)
        card = get_product_card_by_index(driver, index)

    card.screenshot(output_path)


def capture_product_cards(driver):
    """截取前几个商品卡片的默认状态。"""

    print("\n📸 商品卡片截图")

    results = {}

    card_count = min(8, len(get_product_cards(driver)))

    for i in range(card_count):

        paths = build_paths(
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR,
            f"product_{i}"
        )

        max_attempts = 3

        for attempt in range(1, max_attempts + 1):

            try:

                capture_card_image(driver, i, paths["current"])

                normalize_current_to_baseline(paths)
                mask_card_dynamic_area(paths)

                results[f"product_{i}"] = paths

                print(f"✅ product_{i}")

                break

            except StaleElementReferenceException as e:

                if attempt == max_attempts:

                    print(f"❌ product_{i} 截图失败（详见失败汇总）")

                    results[f"product_{i}"] = {"error": f"截图失败: {e}"}

                else:

                    print(
                        f"⚠️ product_{i} stale, "
                        f"retry {attempt}/{max_attempts}"
                    )

                    time.sleep(1)

            except Exception as e:

                print(f"❌ product_{i} 截图失败（详见失败汇总）")

                results[f"product_{i}"] = {"error": f"截图失败: {e}"}

                break

    return results


def capture_hover_cards(driver):
    """截取前几个商品卡片的 hover 状态。"""

    print("\n🖱 Hover截图")

    results = {}

    card_count = min(8, len(get_product_cards(driver)))

    for i in range(card_count):

        paths = build_paths(
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR,
            f"hover_{i}"
        )

        max_attempts = 3

        for attempt in range(1, max_attempts + 1):

            try:

                capture_card_image(driver, i, paths["current"], hover=True)

                normalize_current_to_baseline(paths)
                mask_card_dynamic_area(paths)

                results[f"hover_{i}"] = paths

                print(f"✅ hover_{i}")

                break

            except StaleElementReferenceException as e:

                if attempt == max_attempts:

                    print(f"❌ hover_{i} 截图失败（详见失败汇总）")

                    results[f"hover_{i}"] = {"error": f"截图失败: {e}"}

                else:

                    print(
                        f"⚠️ hover_{i} stale, "
                        f"retry {attempt}/{max_attempts}"
                    )

                    time.sleep(1)

            except Exception as e:

                print(f"❌ hover_{i} 截图失败（详见失败汇总）")

                results[f"hover_{i}"] = {"error": f"截图失败: {e}"}

                break

    return results


def run():
    """执行 PLP Selenium 视觉检测并返回失败信息列表。"""
    failures = []

    create_dirs(
        BASELINE_DIR,
        CURRENT_DIR,
        DIFF_DIR
    )

    driver = None

    try:
        driver = init_driver()

        open_page_with_retry(driver, URL, wait_for_collection_page, "PLP")

        time.sleep(2)

        failures.extend(dom_check(driver, MODULES))

        failures.extend(check_product_count(driver))

        hide_dynamic_elements(driver)

        module_results = capture_modules(
            driver,
            MODULES,
            CURRENT_DIR,
            BASELINE_DIR,
            DIFF_DIR,
            require_reviews=False
        )

        product_results = capture_product_cards(driver)

        hover_results = capture_hover_cards(driver)

        failures.extend(process_results(module_results, SITE, SUITE, PAGE))

        failures.extend(process_results(product_results, SITE, SUITE, PAGE))

        failures.extend(process_results(hover_results, SITE, SUITE, PAGE))

    except Exception as e:

        error = f"Selenium运行异常: {type(e).__name__}: {e}"
        failures.append(f"PLP: {error}")
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
        print("\n❌ PLP Selenium 失败汇总")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
