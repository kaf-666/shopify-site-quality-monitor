from common import (
    request_page,
    check_response_time,
    check_status_code,
    check_title,
)

# 首页健康检查：只验证页面能访问、响应够快、title 与品牌相关。
URL = "https://www.mondressy.com"

EXPECTED_KEYWORDS = [
    "mondressy",
]



def run():
    """执行首页 requests 检测，返回失败信息列表。"""
    failures = []

    try:
        response, response_time = request_page(URL)

        check_response_time(response_time)
        check_status_code(response)
        check_title(response, EXPECTED_KEYWORDS)

        print("🎉 首页 requests检测通过")
        return failures

    except Exception as e:
        failures.append(f"首页: {e}")
        print("❌ 首页 requests检测失败（详见失败汇总）")
        return failures


if __name__ == "__main__":
    import sys

    page_failures = run()
    if page_failures:
        print("\n❌ 首页 requests 失败汇总")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
