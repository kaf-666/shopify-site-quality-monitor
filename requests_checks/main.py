from home_health_check import run as run_home
from collection_health_check import run as run_collection
from product_health_check import run as run_product


print("=" * 50)
print("🚀 开始运行 requests 健康检测")
print("=" * 50)


print("\n🏠 首页检测")
run_home()


print("\n🛍️ PLP检测")
run_collection()


print("\n📦 PDP检测")
run_product()


print("\n🎉 所有 requests 检测通过")