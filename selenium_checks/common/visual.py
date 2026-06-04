import os
import shutil

import numpy as np

from PIL import Image


CHANGE_THRESHOLD = 0.005
AUTO_UPDATE_THRESHOLD = 0.02


def compare_images(img1_path, img2_path, diff_path):

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

        highlight = img2.copy()

        highlight[significant] = [255, 0, 0]

        Image.fromarray(highlight).save(diff_path)

    return ratio < CHANGE_THRESHOLD, ratio


def process_results(results):
    failures = []

    for name, paths in results.items():

        if paths is None or paths.get("error"):

            error = paths.get("error") if paths else "截图失败"
            print(f"🚨 [{name}] {error}")
            failures.append(f"视觉 [{name}] {error}")

            continue

        cur = paths["current"]
        base = paths["baseline"]
        diff = paths["diff"]

        if not os.path.exists(base):

            os.replace(cur, base)

            print(f"🆕 [{name}] baseline 初始化")

            continue

        ok, ratio = compare_images(
            base,
            cur,
            diff
        )

        if ok:

            print(f"✅ [{name}] 正常 {ratio:.4%}")

        else:

            print(f"❌ [{name}] 变化 {ratio:.2%}")
            failures.append(
                f"视觉 [{name}] diff {ratio:.2%} 超过阈值 {CHANGE_THRESHOLD:.2%}"
            )

            if ratio < AUTO_UPDATE_THRESHOLD:

                shutil.copy2(cur, base)

                print(f"🔄 [{name}] 自动更新 baseline")

    return failures
