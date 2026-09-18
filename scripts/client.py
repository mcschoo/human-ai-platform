#!/usr/bin/env python3
"""Deploy, inspect, test, and benchmark the Human AI Platform."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
PLATFORM_FILE = ROOT / "config/platform.env"
MODEL_DIR = ROOT / "config/models"
BENCHMARK = ROOT / "benchmarks/stream_benchmark.py"


# Input: A simple KEY=VALUE file.
# Output: Its values without shell evaluation.
def read_env(path: Path) -> dict[str, str]:
    values = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


# Input: A dotted version string.
# Output: A numeric tuple suitable for comparison.
def version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"\d+(?:\.\d+)+", value)
    return tuple(map(int, match.group().split("."))) if match else ()


# Input: A command and optional execution settings.
# Output: The completed process.
def run(
    command: list[str],
    *,
    capture: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=capture,
            check=check,
        )
    except FileNotFoundError as exc:
        if check:
            raise
        return subprocess.CompletedProcess(command, 127, "", str(exc))


# Input: An operator-facing check and optional fix.
# Output: Printed status and the check result.
def report(name: str, passed: bool, detail: str, fix: str = "") -> bool:
    print(f"{'PASS' if passed else 'FAIL'} {name}: {detail}")
    if not passed and fix:
        print(f"     Fix: {fix}")
    return passed


def active_values() -> dict[str, str]:
    values = read_env(ENV_EXAMPLE)
    values.update(read_env(ENV_FILE))
    return values


def profile_values(profile: str | None = None) -> dict[str, str]:
    name = profile or active_values().get("MODEL_PROFILE", "gemma-4-12b-bf16")
    path = MODEL_DIR / f"{name}.env"
    if not path.exists():
        raise ValueError(f"unknown model profile: {name}")
    return {"MODEL_PROFILE": name, **read_env(path)}


def command_environment(profile: str | None = None) -> dict[str, str]:
    values = active_values()
    model = profile_values(profile)
    environment = os.environ.copy()
    environment.update(values)
    environment.update(
        {
            "MODEL_PROFILE": model["MODEL_PROFILE"],
            "OPENAI_API_KEY": values.get("VLLM_API_KEY", ""),
            "OPENAI_MODEL": model["SERVED_MODEL_NAME"],
            "OPENAI_BASE_URL": (
                f"http://127.0.0.1:{values.get('PUBLIC_PORT', '8080')}/v1"
            ),
            "HAI_HEALTH_URL": (
                f"http://127.0.0.1:{values.get('ADMIN_PORT', '2019')}/health"
            ),
            "CONTROL_PLANE_MODEL": model["SERVED_MODEL_NAME"],
            "CONTROL_PLANE_VLLM_URL": (
                f"http://127.0.0.1:{values.get('PUBLIC_PORT', '8080')}/v1"
            ),
        }
    )
    return environment


# Input: Optional profile selected by the operator.
# Output: A local secret file, created or updated without replacing secrets.
def ensure_env(profile: str | None = None) -> None:
    created = not ENV_FILE.exists()
    if created:
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        text = text.replace(
            "VLLM_API_KEY=replace-with-a-random-secret",
            f"VLLM_API_KEY={secrets.token_hex(32)}",
        )
        password = secrets.token_urlsafe(18)
        text = text.replace(
            "CONTROL_PLANE_OPERATOR_PASSWORD=replace-with-a-random-secret",
            f"CONTROL_PLANE_OPERATOR_PASSWORD={password}",
        )
        text = text.replace(
            "CONTROL_PLANE_COOKIE_SECRET=replace-with-a-different-random-secret",
            f"CONTROL_PLANE_COOKIE_SECRET={secrets.token_hex(32)}",
        )
        ENV_FILE.write_text(text, encoding="utf-8")
        ENV_FILE.chmod(0o600)
        print(f"Created .env. Control-plane password: {password}")
    if profile:
        model = profile_values(profile)
        set_env_value("MODEL_PROFILE", profile)
        set_env_value("CONTROL_PLANE_MODEL", model["SERVED_MODEL_NAME"])


# Input: One .env key and value.
# Output: The local file with that setting replaced or appended.
def set_env_value(key: str, value: str) -> None:
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    replacement = f"{key}={value}"
    found = False
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            found = True
            break
    if not found:
        lines.append(replacement)
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# Input: Current host and selected model profile.
# Output: True when the machine can run the stack.
def doctor(profile: str | None = None, container_check: bool = True) -> bool:
    requirements = read_env(PLATFORM_FILE)
    checks = []
    current_python = ".".join(map(str, sys.version_info[:3]))
    checks.append(
        report(
            "Python",
            version_tuple(current_python)
            >= version_tuple(requirements["PYTHON_MIN"]),
            current_python,
            "sudo apt-get install python3 python3-venv",
        )
    )
    docker = run(["docker", "info", "--format", "{{.ServerVersion}}"], capture=True, check=False)
    checks.append(
        report(
            "Docker",
            docker.returncode == 0,
            (docker.stdout or docker.stderr).strip() or "not available",
            "Install Docker Engine and start the Docker service.",
        )
    )
    compose = run(["docker", "compose", "version", "--short"], capture=True, check=False)
    compose_version = (compose.stdout or compose.stderr).strip()
    checks.append(
        report(
            "Docker Compose",
            compose.returncode == 0
            and version_tuple(compose_version)
            >= version_tuple(requirements["DOCKER_COMPOSE_MIN"]),
            compose_version or "not available",
            f"Install Docker Compose {requirements['DOCKER_COMPOSE_MIN']} or newer.",
        )
    )
    gpu = run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        capture=True,
        check=False,
    )
    gpu_detail = (gpu.stdout or gpu.stderr).strip()
    memory_match = re.search(r",\s*(\d+)\s*,", gpu_detail)
    enough_memory = bool(
        memory_match
        and int(memory_match.group(1)) >= int(requirements["MIN_GPU_MEMORY_MIB"])
    )
    checks.append(
        report(
            "NVIDIA GPU",
            gpu.returncode == 0 and enough_memory,
            gpu_detail or "not available",
            "Install a supported NVIDIA driver or choose a GPU with at least 45 GB.",
        )
    )
    try:
        model = profile_values(profile)
        checks.append(report("Model profile", True, model["MODEL_PROFILE"]))
    except ValueError as exc:
        checks.append(report("Model profile", False, str(exc)))
        model = {"MODEL_PROFILE": profile or "missing"}
    config_env = os.environ.copy()
    config_env.setdefault("VLLM_API_KEY", "doctor-placeholder")
    config_env["MODEL_PROFILE"] = model["MODEL_PROFILE"]
    compose_config = run(
        ["docker", "compose", "config", "--quiet"],
        capture=True,
        check=False,
        env=config_env,
    )
    checks.append(
        report(
            "Compose config",
            compose_config.returncode == 0,
            (compose_config.stderr or "valid").strip(),
        )
    )
    if container_check and docker.returncode == 0 and gpu.returncode == 0:
        toolkit = run(
            [
                "docker",
                "run",
                "--rm",
                "--gpus",
                "all",
                requirements["CUDA_TEST_IMAGE"],
                "nvidia-smi",
            ],
            capture=True,
            check=False,
        )
        checks.append(
            report(
                "NVIDIA Container Toolkit",
                toolkit.returncode == 0,
                "GPU visible in container"
                if toolkit.returncode == 0
                else (toolkit.stderr.strip() or "container check failed"),
                "Install and configure NVIDIA Container Toolkit for Docker.",
            )
        )
    print(
        f"Reference: {requirements['REFERENCE_GPU']}, driver "
        f"{requirements['REFERENCE_DRIVER']}, vLLM CUDA "
        f"{requirements['VLLM_CUDA_VERSION']}."
    )
    return all(checks)


# Input: URL and optional bearer token.
# Output: Parsed JSON, or None when unavailable.
def get_json(url: str, token: str = "") -> Any | None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())
    except (OSError, TimeoutError, json.JSONDecodeError):
        return None


# Input: A local health URL.
# Output: True for a successful HTTP response.
def url_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return 200 <= response.status < 300
    except (OSError, TimeoutError):
        return False


# Input: A Compose service.
# Output: Its Docker health state and recent startup stage.
def service_state(service: str, environment: dict[str, str]) -> tuple[str, str]:
    container = run(
        ["docker", "compose", "ps", "-q", service],
        capture=True,
        check=False,
        env=environment,
    ).stdout.strip()
    if not container:
        return "missing", "container not created"
    state = run(
        [
            "docker",
            "inspect",
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            container,
        ],
        capture=True,
        check=False,
    ).stdout.strip()
    logs = run(
        ["docker", "compose", "logs", "--tail", "40", "--no-color", service],
        capture=True,
        check=False,
        env=environment,
    ).stdout.lower()
    if "loading model weights" in logs or "loading safetensors" in logs:
        stage = "loading model weights"
    elif "downloading" in logs or "fetching" in logs:
        stage = "downloading model"
    else:
        stage = "starting model server"
    return state or "unknown", stage


# Input: A Compose service and timeout.
# Output: True when Docker reports the service healthy.
def wait_for_service(
    service: str,
    environment: dict[str, str],
    timeout: int,
) -> bool:
    started = time.monotonic()
    progress = tqdm(
        desc=f"Starting {service}",
        unit="check",
        bar_format="{desc}: {n} checks [{elapsed}] {postfix}",
    )
    try:
        while time.monotonic() - started < timeout:
            state, stage = service_state(service, environment)
            progress.set_postfix_str(stage)
            if state == "healthy":
                progress.update()
                return True
            if state in {"dead", "exited", "unhealthy"}:
                print(f"\n{service} stopped with state: {state}", file=sys.stderr)
                return False
            progress.update()
            time.sleep(2)
    finally:
        progress.close()
    return False


# Input: Expected model and timeout.
# Output: Success when health and model discovery become ready.
def wait_until_ready(environment: dict[str, str], timeout: int) -> bool:
    health_url = environment["HAI_HEALTH_URL"]
    models_url = f"{environment['OPENAI_BASE_URL']}/models"
    expected = environment["OPENAI_MODEL"]
    with tqdm(
        desc="Checking API",
        unit="check",
        bar_format="{desc}: {n} checks [{elapsed}] {postfix}",
    ) as progress:
        started = time.monotonic()
        while time.monotonic() - started < timeout:
            health = url_ok(health_url)
            models = get_json(models_url, environment["OPENAI_API_KEY"])
            active = {
                item.get("id")
                for item in (models or {}).get("data", [])
                if isinstance(item, dict)
            }
            if health and expected in active:
                progress.set_postfix_str(f"{expected} ready")
                progress.update()
                return True
            progress.set_postfix_str("waiting for proxy and model API")
            progress.update()
            time.sleep(2)
    return False


# Input: A selected model and readiness timeout.
# Output: A running, healthy Compose stack.
def deploy(profile: str | None, timeout: int) -> int:
    if not doctor(profile):
        return 1
    ensure_env(profile)
    environment = command_environment(profile)
    run(["docker", "compose", "up", "-d", "--build", "vllm"], env=environment)
    started = time.monotonic()
    if not wait_for_service("vllm", environment, timeout):
        print("Model failed to start. Run ./client.sh logs vllm.", file=sys.stderr)
        return 1
    remaining = max(30, timeout - int(time.monotonic() - started))
    run(["docker", "compose", "up", "-d", "--build"], env=environment)
    if wait_until_ready(environment, remaining):
        run(["docker", "compose", "ps"], env=environment)
        return 0
    print("Model startup timed out. Run ./client.sh logs vllm.", file=sys.stderr)
    return 1


def status() -> int:
    environment = command_environment()
    run(["docker", "compose", "ps"], check=False, env=environment)
    health = url_ok(environment["HAI_HEALTH_URL"])
    models = get_json(
        f"{environment['OPENAI_BASE_URL']}/models",
        environment["OPENAI_API_KEY"],
    )
    active = [item.get("id") for item in (models or {}).get("data", [])]
    healthy = health and environment["OPENAI_MODEL"] in active
    print(f"API: {'ready' if healthy else 'not ready'}")
    print(f"Models: {', '.join(active) if active else 'none'}")
    return 0 if healthy else 1


def test() -> int:
    environment = command_environment()
    if status():
        return 1
    root = run(
        [sys.executable, "-m", "pytest", "tests"],
        check=False,
        env={**environment, "HAI_LIVE_TESTS": "1"},
    )
    control = run(
        [sys.executable, "-m", "pytest", "services/control-plane/tests"],
        check=False,
        env={**environment, "CONTROL_PLANE_LIVE_TESTS": "1"},
    )
    return 0 if root.returncode == 0 and control.returncode == 0 else 1


def benchmark(arguments: list[str]) -> int:
    environment = command_environment()
    requirements = read_env(PLATFORM_FILE)
    started_at = datetime.now(timezone.utc).isoformat()
    defaults = [
        "--api-key",
        environment["OPENAI_API_KEY"],
        "--model",
        environment["OPENAI_MODEL"],
        "--base-url",
        environment["OPENAI_BASE_URL"],
        "--health-url",
        environment["HAI_HEALTH_URL"],
        "--max-p95-ttft-ms",
        requirements["MAX_P95_TTFT_MS"],
        "--max-p95-total-ms",
        requirements["MAX_P95_TOTAL_MS"],
        "--max-concurrency",
        requirements["MAX_CONCURRENCY"],
    ]
    result = run(
        [sys.executable, str(BENCHMARK), *defaults, *arguments],
        check=False,
        env=environment,
    )
    logs = run(
        [
            "docker",
            "compose",
            "logs",
            "--since",
            started_at,
            "--no-color",
            "vllm",
        ],
        capture=True,
        check=False,
        env=environment,
    )
    if re.search(r"out of memory|cuda oom", logs.stdout, re.IGNORECASE):
        print("FAIL vLLM logs contain an out-of-memory error.", file=sys.stderr)
        return 1
    return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--profile")
    doctor_parser.add_argument("--skip-container-check", action="store_true")
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--profile")
    deploy_parser.add_argument("--timeout", type=int, default=1200)
    commands.add_parser("status")
    commands.add_parser("test")
    logs_parser = commands.add_parser("logs")
    logs_parser.add_argument("services", nargs="*")
    commands.add_parser("down")
    commands.add_parser("benchmark", add_help=False)
    args, remaining = parser.parse_known_args()
    if args.command == "benchmark":
        args.arguments = remaining
    elif remaining:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")
    return args


def main() -> int:
    args = parse_args()
    if not args.command:
        print(
            "Usage: ./client.sh "
            "{doctor,deploy,status,test,benchmark,logs,down}"
        )
        return 0
    if args.command == "doctor":
        return 0 if doctor(args.profile, not args.skip_container_check) else 1
    if args.command == "deploy":
        return deploy(args.profile, args.timeout)
    if args.command == "status":
        return status()
    if args.command == "test":
        return test()
    environment = command_environment()
    if args.command == "logs":
        services = args.services or ["vllm", "control-plane"]
        return run(
            ["docker", "compose", "logs", "-f", *services],
            check=False,
            env=environment,
        ).returncode
    if args.command == "down":
        return run(
            ["docker", "compose", "down"],
            check=False,
            env=environment,
        ).returncode
    return benchmark(args.arguments)


if __name__ == "__main__":
    raise SystemExit(main())
