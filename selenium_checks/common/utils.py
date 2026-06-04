import os
import sys


os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)


def screenshot_root(site):

    return os.path.join(PROJECT_ROOT, "screenshots", site)


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
