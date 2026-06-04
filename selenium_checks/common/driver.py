from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
import os


def init_driver():
    """初始化视觉回归专用的 ChromeDriver。"""

    options = Options()
    # eager 能在 DOM 可用后继续执行，减少第三方资源拖慢整页加载。
    options.page_load_strategy = "eager"

    options.add_argument("--headless=new")

    # ★ 初始窗口直接给大
    options.add_argument("--window-size=1600,4000")

    # ★ 固定DPR，避免diff抖动
    options.add_argument(
        "--force-device-scale-factor=1"
    )

    options.add_argument("--disable-gpu")

    options.add_argument("--no-sandbox")

    # ★ 禁止动画
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver_path = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "driver",
            "chromedriver.exe"
        )
    )

    if os.path.exists(driver_path):
        driver = webdriver.Chrome(
            service=Service(driver_path),
            options=options
        )
    else:
        driver = webdriver.Chrome(options=options)

    driver.set_page_load_timeout(45)
    driver.set_script_timeout(30)

    return driver
