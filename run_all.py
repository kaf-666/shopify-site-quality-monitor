import subprocess
import sys

def run_stage(label, folder, script):
    """运行指定阶段，返回是否成功"""
    print("\n" + "=" * 60)
    print(f"{' STAGE: ' + label + ' ':=^60}")
    print("=" * 60 + "\n")

    result = subprocess.run(
        [sys.executable, "-m", script],
        cwd=folder,
    )

    if result.returncode != 0:
        print(f"\n❌ {label} 未通过，终止后续检测")
        sys.exit(1)

    print(f"\n✅ {label} 全部通过")


# ---------- 主流程 ----------

run_stage("Requests 健康检测", "requests_checks", "main")
run_stage("Selenium 视觉回归检测",      "selenium_checks", "main")

print("\n" + "=" * 60)
print("🎉 所有检测全部通过")
print("=" * 60)
