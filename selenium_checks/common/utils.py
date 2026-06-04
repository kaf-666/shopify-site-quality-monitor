import os
import sys
import time


os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# 项目根目录用于拼接 screenshots、reports 等输出路径。
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)


def screenshot_root(site):
    """返回指定站点的截图根目录。"""

    return os.path.join(PROJECT_ROOT, "screenshots", site)


def locate_element(driver, locator):
    """按项目统一的 locator 格式查找可见元素。"""

    method, value = locator

    by = By.CSS_SELECTOR if method == "css" else By.XPATH

    return WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((by, value))
    )


def navigate_with_retry(driver, url, attempts=3, delay=2):
    """打开页面，遇到 WebDriver 导航错误时重试。"""

    for attempt in range(1, attempts + 1):

        try:

            driver.get(url)
            return

        except WebDriverException:

            if attempt == attempts:
                raise

            print(
                f"⚠️ 页面打开失败, "
                f"retry {attempt}/{attempts}"
            )

            time.sleep(delay)


def open_page_with_retry(
    driver,
    url,
    wait_until_ready,
    label="page",
    attempts=3,
    delay=2
):
    """打开页面后等待业务关键模块就绪，失败时整体重试。"""

    for attempt in range(1, attempts + 1):

        try:

            navigate_with_retry(driver, url, attempts=1)
            wait_until_ready(driver)
            return

        except Exception:

            if attempt == attempts:
                raise

            print(
                f"⚠️ {label} 页面未就绪, "
                f"retry {attempt}/{attempts}"
            )

            time.sleep(delay)


def create_dirs(*dirs):
    """确保一组输出目录存在。"""

    for d in dirs:
        os.makedirs(d, exist_ok=True)


def build_paths(current_dir, baseline_dir, diff_dir, name):
    """生成某个截图用例的 current、baseline、diff 三类路径。"""

    return {

        "current": os.path.join(
            current_dir,
            f"{name}.png"
        ),

        "baseline": os.path.join(
            baseline_dir,
            f"{name}.png"
        ),

        "diff": os.path.join(
            diff_dir,
            f"{name}.png"
        ),
    }
