from common import (
    request_page,
    check_response_time,
    check_status_code,
    check_title,
)

URL = "https://www.mondressy.com"

EXPECTED_KEYWORDS = [
    "mondressy",
]



def run():

    try:
        response, response_time = request_page(URL)

        check_response_time(response_time)
        check_status_code(response)
        check_title(response, EXPECTED_KEYWORDS)

        print("🎉 首页 requests检测通过")
        return True

    except Exception as e:
        print(f"❌ 首页检测失败: {e}")
        return False