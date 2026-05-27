from __future__ import annotations

import argparse
import os

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Fire a Plaid Sandbox webhook.")
    parser.add_argument("--webhook-type", default="TRANSACTIONS")
    parser.add_argument("--webhook-code", default="SYNC_UPDATES_AVAILABLE")
    args = parser.parse_args()

    base_url = os.getenv("SANDBOX_BACKEND_URL", "http://localhost:8000").rstrip("/")
    response = httpx.post(
        f"{base_url}/api/sandbox/fire-webhook",
        json={"webhook_type": args.webhook_type, "webhook_code": args.webhook_code},
        timeout=60.0,
    )
    print(f"status_code={response.status_code}")
    print(response.text)
    return 0 if response.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
