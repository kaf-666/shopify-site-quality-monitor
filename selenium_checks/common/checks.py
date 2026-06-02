from common.utils import locate_element


def dom_check(driver, modules):

    print("\n🧩 DOM检测")

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

        except Exception as e:

            print(f"❌ [{name}] DOM异常: {e}")


def hide_dynamic_elements(driver):
    """隐藏所有干扰截图的动态元素"""

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
                .cbb-frequently-bought-container {
                    display: none !important;
                }
            `;

            document.head.appendChild(style);
        }
    """)
