from common import (
    CheckFailure,
    request_page,
    check_response_time,
    check_status_code,
    check_title,
)

# PDP（商品详情页）健康检查：验证指定商品页可访问且内容像 product。
URL = "https://mondressy.com/products/a-line-princess-sleeveless-tea-length-wedding-guest-dresses-mon2311613"

EXPECTED_KEYWORDS = [
    "dress",
]



def run():
    """执行 PDP requests 检测，返回失败信息列表。"""
    failures = []

    try:
        response, response_time = request_page(URL)

        check_response_time(response_time)
        check_status_code(response)
        check_title(response, EXPECTED_KEYWORDS)

        # 简单确认 HTML 里包含商品路径，避免 200 状态的软错误页漏检。
        if "/products/" not in response.text:
            raise CheckFailure("PDP页面异常")

        print("✅ PDP页面正常")

        print("🎉 PDP requests检测通过")
        return failures

    except Exception as e:
        failures.append(f"PDP: {e}")
        print("❌ PDP requests检测失败（详见失败汇总）")
        return failures


if __name__ == "__main__":
    import sys

    page_failures = run()
    if page_failures:
        print("\n❌ PDP requests 失败汇总")
        for index, failure in enumerate(page_failures, 1):
            print(f"{index}. {failure}")
    sys.exit(1 if page_failures else 0)
