import os
import sys

os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import time

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    )
}

BAD_TITLES = [
    "403",
    "404",
    "error",
    "not found",
    "access denied",
    "attention required",
    "cloudflare",
    "captcha",
    "blocked",
]

MAX_RESPONSE_TIME = float(os.environ.get("MAX_RESPONSE_TIME", "10"))


class CheckFailure(Exception):
    """健康检查失败。"""


def request_page(url):
    try:
        start_time = time.time()

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=20
        )

        response_time = round(time.time() - start_time, 2)

        return response, response_time

    except Exception as e:
        raise CheckFailure(f"请求失败: {e}") from e



def check_response_time(response_time):
    if response_time > MAX_RESPONSE_TIME:
        raise CheckFailure(f"页面响应过慢: {response_time}s")

    print(f"✅ 页面响应正常: {response_time}s")



def check_status_code(response):
    if response.status_code != 200:
        raise CheckFailure(f"HTTP状态异常: {response.status_code}")

    print(f"✅ HTTP状态正常: {response.status_code}")



def check_title(response, expected_keywords):
    soup = BeautifulSoup(response.text, "html.parser")

    title = soup.title.string.strip() if soup.title else ""

    if not title:
        raise CheckFailure("title缺失")

    title_lower = title.lower()

    for bad in BAD_TITLES:
        if bad in title_lower:
            raise CheckFailure(f"异常title: {title}")

    matched = False

    for keyword in expected_keywords:
        if keyword.lower() in title_lower:
            matched = True
            break

    if not matched:
        raise CheckFailure(f"title不符合预期: {title}")

    print(f"✅ title正常: {title}")
