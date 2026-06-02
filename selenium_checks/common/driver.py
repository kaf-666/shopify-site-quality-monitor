from selenium import webdriver
from selenium.webdriver.chrome.options import Options


def init_driver():

    options = Options()

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

    driver = webdriver.Chrome(options=options)

    return driver