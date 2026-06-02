from common import (
    request_page,
    check_response_time,
    check_status_code,
    check_title,
)

URL = "https://mondressy.com/products/a-line-princess-sleeveless-tea-length-wedding-guest-dresses-mon2311613"

EXPECTED_KEYWORDS = [
    "dress",
]



def run():

    try:
        response, response_time = request_page(URL)

        check_response_time(response_time)
        check_status_code(response)
        check_title(response, EXPECTED_KEYWORDS)

        if "/products/" not in response.text:
            raise Exception("PDP页面异常")

        print("✅ PDP页面正常")

        print("🎉 PDP requests检测通过")
        return True

    except Exception as e:
        print(f"❌ PDP检测失败: {e}")
        return False