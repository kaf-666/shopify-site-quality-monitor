import time

from common.utils import (
    locate_element,
    build_paths
)


def wait_for_images(driver, element, timeout=10):
    """等待指定元素内所有图片真正加载完成"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        ready = driver.execute_script("""
            var container = arguments[0];
            var imgs = container.querySelectorAll('img');
            if (imgs.length === 0) return true;
            for (var i = 0; i < imgs.length; i++) {
                var img = imgs[i];
                if (img.loading === 'lazy') {
                    img.loading = 'eager';
                }
                if (img.dataset.src && !img.getAttribute('src')) {
                    img.src = img.dataset.src;
                }
                if (img.dataset.srcset && !img.getAttribute('srcset')) {
                    img.srcset = img.dataset.srcset;
                }
                if (!img.complete || img.naturalWidth === 0) {
                    return false;
                }
            }
            return true;
        """, element)
        if ready:
            time.sleep(0.3)
            return True
        time.sleep(0.3)
    print(f"      ⚠️  等待图片超时 ({timeout}s)")
    return False


def wait_for_reviews(driver, timeout=10):
    """等待所有 AliReviews 评分组件初始化完成"""
    end_time = time.time() + timeout
    while time.time() < end_time:
        ready = driver.execute_script("""
            var all = document.querySelectorAll('.alireviews-review-star-rating');
            if (all.length === 0) return true;
            for (var i = 0; i < all.length; i++) {
                if (all[i].getAttribute('data-status') !== 'initialized') {
                    return false;
                }
            }
            return true;
        """)
        if ready:
            time.sleep(0.3)
            return True
        time.sleep(0.3)
    print(f"      ⚠️  等待评分组件超时 ({timeout}s)")
    return False


def wait_for_layout_stable(driver, element, timeout=10, check_interval=0.5):
    """等待元素尺寸不再变化（布局稳定）"""
    prev_size = None
    stable_count = 0
    required_stable = 2
    end_time = time.time() + timeout

    while time.time() < end_time:
        current_size = driver.execute_script("""
            var el = arguments[0];
            var rect = el.getBoundingClientRect();
            return {
                width: Math.round(rect.width),
                height: Math.round(rect.height)
            };
        """, element)

        if current_size == prev_size:
            stable_count += 1
            if stable_count >= required_stable:
                return True
        else:
            stable_count = 0

        prev_size = current_size
        time.sleep(check_interval)

    print(f"      ⚠️  布局未稳定 ({timeout}s), 当前尺寸: {prev_size}")
    return False




def capture_modules(driver, modules, current_dir, baseline_dir, diff_dir):

    print("\n📸 模块截图")

    results = {}

    for name, locator in modules.items():

        paths = build_paths(
            current_dir,
            baseline_dir,
            diff_dir,
            name
        )

        try:

            # ★ 重新获取元素
            el = locate_element(driver, locator)

            # ★ 滚动到中间，避免sticky遮挡
            driver.execute_script("""
                arguments[0].scrollIntoView({
                    block: 'center',
                    inline: 'center'
                });
            """, el)

            time.sleep(0.5)

            # ★ 隐藏动态元素
            driver.execute_script("""
                document.querySelectorAll(
                    '.price,.sale-badge,.spr-badge,#size_error,.alr-wh-rw-popup'
                ).forEach(function(el) {
                    el.style.visibility = 'hidden';
                });
            """)

            # ★ 等待资源稳定
            wait_for_images(driver, el, timeout=10)

            wait_for_reviews(driver, timeout=10)

            wait_for_layout_stable(
                driver,
                el,
                timeout=10
            )

            # ★ 再等一下避免二次reflow
            time.sleep(1)

            # ★ 重新获取元素（非常重要）
            el = locate_element(driver, locator)

            # ★ Selenium原生截图
            el.screenshot(paths["current"])

            results[name] = paths

            print(f"✅ [{name}]")

        except Exception as e:

            print(f"❌ [{name}] {e}")

            results[name] = None

    return results



