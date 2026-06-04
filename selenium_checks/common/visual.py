import os
import shutil

import numpy as np

from PIL import Image

from common.test_results import add_result


CHANGE_THRESHOLD = 0.005
AUTO_UPDATE_THRESHOLD = 0.02


def compare_images(img1_path, img2_path, diff_path):
    """对比两张截图，返回是否通过和变化像素比例。"""

    img1 = np.array(Image.open(img1_path))
    img2 = np.array(Image.open(img2_path))

    if img1.shape != img2.shape:
        return False, 1.0

    diff = np.abs(
        img1.astype(int) - img2.astype(int)
    )

    significant = (diff > 25).any(axis=2)

    ratio = significant.sum() / significant.size

    if ratio > 0:

        # 把变化像素标红，方便人工打开 diff 图定位问题。
        highlight = img2.copy()

        highlight[significant] = [255, 0, 0]

        Image.fromarray(highlight).save(diff_path)

    return ratio < CHANGE_THRESHOLD, ratio


def build_result(site, suite, page, case, status, paths, ratio=None, error=None):
    """组装统一的视觉测试结果，最终写入 reports/visual-results.json。"""
    result = {
        "site": site,
        "suite": suite,
        "page": page,
        "case": case,
        "status": status,
        "ratio": ratio,
        "threshold": CHANGE_THRESHOLD,
        "baseline": None,
        "current": None,
        "diff": None,
    }

    if paths:
        result.update({
            "baseline": os.path.abspath(paths.get("baseline"))
            if paths.get("baseline") else None,
            "current": os.path.abspath(paths.get("current"))
            if paths.get("current") else None,
            "diff": os.path.abspath(paths.get("diff"))
            if paths.get("diff") else None,
        })

    if error:
        result["error"] = error

    return result


def process_results(results, site="mondressy_US", suite="visual", page=None):
    """处理截图结果：初始化 baseline、通过、自动更新或记录失败。"""
    failures = []

    for name, paths in results.items():

        if paths is None or paths.get("error"):

            error = paths.get("error") if paths else "截图失败"
            print(f"🚨 [{name}] {error}")
            failures.append(f"视觉 [{name}] {error}")
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    name,
                    "failed",
                    paths,
                    error=error
                )
            )

            continue

        cur = paths["current"]
        base = paths["baseline"]
        diff = paths["diff"]

        if not os.path.exists(base):

            # 第一次运行没有基准图时，用当前图初始化 baseline。
            shutil.copy2(cur, base)

            print(f"🆕 [{name}] baseline 初始化")
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    name,
                    "initialized",
                    paths,
                    ratio=0.0
                )
            )

            continue

        ok, ratio = compare_images(
            base,
            cur,
            diff
        )

        if ok:

            print(f"✅ [{name}] 正常 {ratio:.4%}")
            add_result(
                build_result(
                    site,
                    suite,
                    page,
                    name,
                    "passed",
                    paths,
                    ratio=ratio
                )
            )

        else:

            print(f"❌ [{name}] 变化 {ratio:.2%}")

            if ratio < AUTO_UPDATE_THRESHOLD:

                # 小幅变化视为可接受波动，自动更新基准图。
                shutil.copy2(cur, base)

                print(f"🔄 [{name}] 自动更新 baseline")
                add_result(
                    build_result(
                        site,
                        suite,
                        page,
                        name,
                        "updated",
                        paths,
                        ratio=ratio
                    )
                )

            else:

                failures.append(
                    f"视觉 [{name}] diff {ratio:.2%} 超过阈值 {CHANGE_THRESHOLD:.2%}"
                )
                add_result(
                    build_result(
                        site,
                        suite,
                        page,
                        name,
                        "failed",
                        paths,
                        ratio=ratio
                    )
                )

    return failures
