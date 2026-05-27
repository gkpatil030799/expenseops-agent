from __future__ import annotations

import argparse
import os

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Plaid Sandbox transaction.")
    parser.add_argument("--description", default="ExpenseOps Sandbox Coffee")
    parser.add_argument("--amount", default="12.34")
    parser.add_argument("--auto-fire-webhook", action="store_true")
    args = parser.parse_args()

    base_url = os.getenv("SANDBOX_BACKEND_URL", "http://localhost:8000").rstrip("/")
    payload = {
        "description": args.description,
        "amount": args.amount,
        "auto_fire_webhook": args.auto_fire_webhook,
    }
    response = httpx.post(
        f"{base_url}/api/sandbox/create-transaction",
        json=payload,
        timeout=60.0,
    )
    print(f"status_code={response.status_code}")
    print(response.text)
    return 0 if response.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
