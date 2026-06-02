import os

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


def locate_element(driver, locator):

    method, value = locator

    by = By.CSS_SELECTOR if method == "css" else By.XPATH

    return WebDriverWait(driver, 10).until(
        EC.visibility_of_element_located((by, value))
    )


def create_dirs(*dirs):

    for d in dirs:
        os.makedirs(d, exist_ok=True)


def build_paths(current_dir, baseline_dir, diff_dir, name):

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