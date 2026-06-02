import requests
import sys
import time
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

MAX_RESPONSE_TIME = 5


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
        print(f"❌ 请求失败: {e}")
        sys.exit(1)



def check_response_time(response_time):
    if response_time > MAX_RESPONSE_TIME:
        print(f"❌ 页面响应过慢: {response_time}s")
        sys.exit(1)

    print(f"✅ 页面响应正常: {response_time}s")



def check_status_code(response):
    if response.status_code != 200:
        print(f"❌ HTTP状态异常: {response.status_code}")
        sys.exit(1)

    print(f"✅ HTTP状态正常: {response.status_code}")



def check_title(response, expected_keywords):
    soup = BeautifulSoup(response.text, "html.parser")

    title = soup.title.string.strip() if soup.title else ""

    if not title:
        print("❌ title缺失")
        sys.exit(1)

    title_lower = title.lower()

    for bad in BAD_TITLES:
        if bad in title_lower:
            print(f"❌ 异常title: {title}")
            sys.exit(1)

    matched = False

    for keyword in expected_keywords:
        if keyword.lower() in title_lower:
            matched = True
            break

    if not matched:
        print(f"❌ title不符合预期: {title}")
        sys.exit(1)

    print(f"✅ title正常: {title}")