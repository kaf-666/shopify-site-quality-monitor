from home import run as run_home

from collection import run as run_collection

from product import run as run_product


print("=" * 50)
print("🚀 开始运行视觉回归检测")
print("=" * 50)


print("\n🏠 首页检测")
run_home()


print("\n🛍️ PLP检测")
run_collection()


print("\n📦 PDP检测")
run_product()


print("\n🎉 所有视觉回归检测完成")