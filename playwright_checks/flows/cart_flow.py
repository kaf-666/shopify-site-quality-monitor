import sys


def run():
    print("cart_flow is reserved for explicit business-flow checks.")
    return []


if __name__ == "__main__":
    failures = run()
    sys.exit(1 if failures else 0)
