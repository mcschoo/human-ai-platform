#!/usr/bin/env python3
"""Check the container runtime, GPU, health endpoint, and authenticated API."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request


def probe(name: str, url: str, token: str | None, timeout: float) -> bool:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(512)
            content_type = response.headers.get("content-type", "")
            detail = body.decode("utf-8", errors="replace").strip()
            if "json" in content_type:
                try:
                    detail = json.dumps(json.loads(detail), separators=(",", ":"))
                except json.JSONDecodeError:
                    pass
            print(f"{name}: ok ({response.status}) {detail[:160]}")
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError) as exc:
        print(f"{name}: failed ({exc})", file=sys.stderr)
        return False


def command_probe(name: str, command: list[str]) -> bool:
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=15, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"{name}: failed ({exc})", file=sys.stderr)
        return False
    detail = (result.stdout or result.stderr).strip().splitlines()
    state = "ok" if result.returncode == 0 else "failed"
    print(f"{name}: {state} ({detail[0] if detail else 'no output'})")
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8080/v1"),
    )
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY"))
    parser.add_argument(
        "--health-url",
        default=os.getenv("HAI_HEALTH_URL", "http://127.0.0.1:2019/health"),
    )
    parser.add_argument("--skip-system-checks", action="store_true")
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()

    system_ok = True
    if not args.skip_system_checks:
        checks = (
            ("docker", ["docker", "info", "--format", "{{.ServerVersion}}"]),
            ("compose", ["docker", "compose", "version", "--short"]),
            (
                "gpu",
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader",
                ],
            ),
        )
        results = [command_probe(name, command) for name, command in checks]
        system_ok = all(results)

    healthy = probe("health", args.health_url, None, args.timeout)
    api_ok = probe(
        "models",
        f"{args.base_url.rstrip('/')}/models",
        args.api_key,
        args.timeout,
    )
    return 0 if system_ok and healthy and api_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
