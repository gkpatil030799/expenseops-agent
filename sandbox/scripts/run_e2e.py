from __future__ import annotations

import os

import httpx


def main() -> int:
    base_url = os.getenv("SANDBOX_BACKEND_URL", "http://localhost:8000").rstrip("/")
    response = httpx.post(f"{base_url}/api/sandbox/run-e2e", timeout=60.0)
    print_response(response)
    return 0 if response.is_success else 1


def print_response(response: httpx.Response) -> None:
    print(f"status_code={response.status_code}")
    try:
        data = response.json()
    except Exception:
        print(response.text)
        return
    print(f"trace_id={data.get('trace_id')}")
    for step in data.get("steps", []):
        print(f"- {step.get('name')}: {step.get('status')}")


if __name__ == "__main__":
    raise SystemExit(main())
