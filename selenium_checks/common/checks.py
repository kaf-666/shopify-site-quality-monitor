from common.utils import locate_element


def dom_check(driver, modules):
    """检查每个页面模块是否可见且尺寸有效。"""

    print("\n🧩 DOM检测")
    failures = []

    for name, locator in modules.items():

        try:

            el = locate_element(driver, locator)

            visible = el.is_displayed()

            size_ok = (
                el.size["width"] > 0
                and el.size["height"] > 0
            )

            print(
                f"✅ [{name}] "
                f"visible={visible} | "
                f"size_ok={size_ok}"
            )

            if not visible or not size_ok:
                failures.append(
                    f"DOM [{name}] visible={visible}, size_ok={size_ok}"
                )

        except Exception as e:

            print(f"❌ [{name}] DOM异常: {e}")
            failures.append(f"DOM [{name}] 异常: {e}")

    return failures


def hide_dynamic_elements(driver):
    """隐藏所有干扰截图的动态元素"""

    # 价格、评分、支付插件等经常异步变化，视觉回归前统一隐藏。
    driver.execute_script("""
        if (!document.getElementById('screenshot-hack')) {

            var style = document.createElement('style');

            style.id = 'screenshot-hack';

            style.innerHTML = `
                .price,
                .sale-badge,
                .spr-badge,
                #size_error,
                .alr-wh-rw-popup,
                .cbb-frequently-bought-container,
                [id*="cbb"],
                [class*="cbb"],
                [id*="frequently"],
                [class*="frequently"],
                [id*="bought"],
                [class*="bought"],
                .shopify-payment-button,
                .shopify-payment-button__button,
                [data-shopify="payment-button"],
                shopify-payment-terms {
                    display: none !important;
                }
            `;

            document.head.appendChild(style);
        }

        document.querySelectorAll('body *').forEach(function(el) {
            var text = (el.innerText || '').trim().toLowerCase();
            var compactText = text.replace(/\\s+/g, ' ');

            if (
                text === 'frequently bought together'
                || text === 'more payment options'
                || text === 'buy it now'
                || (
                    compactText.indexOf('pay with paypal') !== -1
                    && compactText.length < 80
                )
            ) {
                var block = el.closest(
                    '[id*="cbb"], [class*="cbb"], section, form, div'
                );

                if (block) {
                    block.style.display = 'none';
                }
            }
        });
    """)
