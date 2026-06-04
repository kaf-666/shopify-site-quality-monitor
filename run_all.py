import subprocess
import sys
import os

os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def run_stage(label, folder, script):
    """运行指定阶段，返回是否成功"""
    print("\n" + "=" * 60)
    print(f"{' STAGE: ' + label + ' ':=^60}")
    print("=" * 60 + "\n")
    sys.stdout.flush()

    result = subprocess.run(
        [sys.executable, "-u", "-m", script],
        cwd=folder,
        env=os.environ.copy(),
    )

    if result.returncode != 0:
        print(f"\n❌ {label} 未通过")
        return False

    print(f"\n✅ {label} 全部通过")
    return True


# ---------- 主流程 ----------

stage_failures = []

if not run_stage("Requests 健康检测", "requests_checks", "main"):
    stage_failures.append("Requests 健康检测")

if not run_stage("Selenium 视觉回归检测", "selenium_checks", "main"):
    stage_failures.append("Selenium 视觉回归检测")

if stage_failures:
    print("\n" + "=" * 60)
    print("❌ 失败阶段汇总")
    print("=" * 60)
    for index, failure in enumerate(stage_failures, 1):
        print(f"{index}. {failure}")
    sys.exit(1)

print("\n" + "=" * 60)
print("🎉 所有检测全部通过")
print("=" * 60)
