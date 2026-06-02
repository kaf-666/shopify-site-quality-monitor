from common import (
    request_page,
    check_response_time,
    check_status_code,
    check_title,
)

URL = "https://mondressy.com/collections/wedding-guest-dresses"

EXPECTED_KEYWORDS = [
    "wedding",
]



def run():

    try:
        response, response_time = request_page(URL)

        check_response_time(response_time)
        check_status_code(response)
        check_title(response, EXPECTED_KEYWORDS)

        if "/collections/" not in response.text:
            raise Exception("Collection页面异常")

        print("✅ Collection页面正常")

        print("🎉 PLP requests检测通过")
        return True

    except Exception as e:
        print(f"❌ PLP检测失败: {e}")
        return False