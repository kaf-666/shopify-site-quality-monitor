from common import (
    CheckFailure,
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
    failures = []

    try:
        response, response_time = request_page(URL)

        check_response_time(response_time)
        check_status_code(response)
        check_title(response, EXPECTED_KEYWORDS)

        if "/collections/" not in response.text:
            raise CheckFailure("Collection页面异常")

        print("✅ Collection页面正常")

        print("🎉 PLP requests检测通过")
        return failures

    except Exception as e:
        failures.append(f"PLP: {e}")
        print("❌ PLP requests检测失败（详见失败汇总）")
        return failures


if __name__ == "__main__":
    import sys

    page_failures = run()
    if page_failures:
        print("\n❌ PLP requests 失败汇总")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
