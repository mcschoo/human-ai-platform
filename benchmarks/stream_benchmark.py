#!/usr/bin/env python3
"""Reproducible async streaming benchmark for an OpenAI-compatible server."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI
from tqdm import tqdm

PROMPTS = {
    "short": "In one sentence, explain why deterministic tests are useful.",
    "long": (
        (
            "Context: A research platform serves concurrent text conversations. "
            "Requests need timeouts, cancellation, backpressure, observability, and "
            "reproducible configuration. "
        )
        * 50
    )
    + "Summarize the operational priorities in five concise bullets.",
}


def environment_info() -> dict[str, str | None]:
    gpu: str | None = None
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,driver_version",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        gpu = result.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "gpu": gpu,
    }


async def require_health(url: str, timeout: float) -> None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
    response.raise_for_status()


@dataclass
class Sample:
    ttft_ms: float | None
    total_ms: float
    output_tokens: int = 0
    error: str | None = None
    protocol_error: bool = False


def percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percent * len(ordered)) - 1)]


async def run_stream(
    client: AsyncOpenAI,
    model: str,
    prompt: str,
    max_tokens: int,
    semaphore: asyncio.Semaphore,
) -> Sample:
    async with semaphore:
        started = time.perf_counter()
        first_token: float | None = None
        saw_chunk = False
        saw_text = False
        protocol_error = False
        output_tokens = 0
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0,
                stream=True,
                stream_options={"include_usage": True},
            )
            async for chunk in stream:
                saw_chunk = True
                if chunk.usage:
                    output_tokens = chunk.usage.completion_tokens
                if not chunk.id or not isinstance(chunk.choices, list):
                    protocol_error = True
                for choice in chunk.choices:
                    content = choice.delta.content
                    if content is not None and not isinstance(content, str):
                        protocol_error = True
                    if content:
                        saw_text = True
                        first_token = first_token or time.perf_counter()
            if not saw_chunk or not saw_text:
                protocol_error = True
            ended = time.perf_counter()
            return Sample(
                ttft_ms=(first_token - started) * 1000 if first_token else None,
                total_ms=(ended - started) * 1000,
                output_tokens=output_tokens,
                error="malformed streaming response" if protocol_error else None,
                protocol_error=protocol_error,
            )
        except Exception as exc:  # noqa: BLE001 - each failed request is a result.
            return Sample(
                ttft_ms=None,
                total_ms=(time.perf_counter() - started) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )


def summarize(
    profile: str,
    concurrency: int,
    samples: list[Sample],
    batch_ms: float | None = None,
) -> dict[str, Any]:
    good = [sample for sample in samples if sample.error is None]
    ttfts = [sample.ttft_ms for sample in good if sample.ttft_ms is not None]
    totals = [sample.total_ms for sample in good]
    return {
        "profile": profile,
        "prompt_characters": len(PROMPTS[profile]),
        "concurrency": concurrency,
        "requests": len(samples),
        "successes": len(good),
        "errors": len(samples) - len(good),
        "protocol_errors": sum(sample.protocol_error for sample in samples),
        "output_tokens": sum(sample.output_tokens for sample in good),
        "output_tokens_per_second": (
            sum(sample.output_tokens for sample in good) / (batch_ms / 1000)
            if batch_ms and good
            else None
        ),
        "requests_per_second": (
            len(good) / (batch_ms / 1000) if batch_ms and good else None
        ),
        "ttft_ms": {
            "p50": percentile(ttfts, 0.50),
            "p95": percentile(ttfts, 0.95),
        },
        "total_ms": {
            "p50": percentile(totals, 0.50),
            "p95": percentile(totals, 0.95),
        },
        "mean_total_ms": statistics.fmean(totals) if totals else None,
        "samples": [asdict(sample) for sample in samples],
    }


# Input: One benchmark summary and its latency limits.
# Output: A pass flag and short failure reasons.
def evaluate(
    result: dict[str, Any],
    max_p95_ttft_ms: float,
    max_p95_total_ms: float,
) -> tuple[bool, list[str]]:
    reasons = []
    ttft = result["ttft_ms"]["p95"]
    total = result["total_ms"]["p95"]
    if result["errors"]:
        reasons.append(f"{result['errors']} request errors")
    if result["protocol_errors"]:
        reasons.append(f"{result['protocol_errors']} protocol errors")
    if ttft is None or total is None:
        reasons.append("missing latency samples")
    if ttft is not None and ttft > max_p95_ttft_ms:
        reasons.append(f"p95 TTFT {ttft:.0f}ms > {max_p95_ttft_ms:.0f}ms")
    if total is not None and total > max_p95_total_ms:
        reasons.append(f"p95 total {total:.0f}ms > {max_p95_total_ms:.0f}ms")
    return not reasons, reasons


# Input: One model client, prompt profile, and concurrency level.
# Output: A measured and evaluated benchmark result.
async def run_level(
    client: AsyncOpenAI,
    args: argparse.Namespace,
    profile: str,
    concurrency: int,
) -> dict[str, Any]:
    semaphore = asyncio.Semaphore(concurrency)
    tasks = [
        asyncio.create_task(
            run_stream(
                client,
                args.model,
                PROMPTS[profile],
                args.max_tokens,
                semaphore,
            )
        )
        for _ in range(concurrency * args.repetitions)
    ]
    started = time.perf_counter()
    samples = []
    progress = tqdm(
        total=len(tasks),
        desc=f"{profile} c={concurrency}",
        unit="request",
        disable=args.no_progress,
    )
    try:
        for task in asyncio.as_completed(tasks):
            samples.append(await task)
            progress.update()
    finally:
        progress.close()
    result = summarize(
        profile,
        concurrency,
        samples,
        (time.perf_counter() - started) * 1000,
    )
    passed, reasons = evaluate(
        result,
        args.max_p95_ttft_ms,
        args.max_p95_total_ms,
    )
    try:
        await require_health(args.health_url, min(args.timeout, 30))
    except Exception as exc:  # noqa: BLE001 - health failure is a benchmark result.
        passed = False
        reasons.append(f"model unhealthy: {exc}")
    result["passed"] = passed
    result["failure_reasons"] = reasons
    if not args.include_samples:
        result.pop("samples")
    _print_result(result)
    return result


# Input: A measured result.
# Output: One compact human-readable terminal line.
def _print_result(result: dict[str, Any]) -> None:
    ttft = result["ttft_ms"]
    total = result["total_ms"]
    state = "PASS" if result["passed"] else "FAIL"
    print(
        f"{state} {result['profile']:5} c={result['concurrency']} "
        f"n={result['requests']} "
        f"ttft p50/p95={_ms(ttft['p50'])}/{_ms(ttft['p95'])} "
        f"total p50/p95={_ms(total['p50'])}/{_ms(total['p95'])} "
        f"req/s={result['requests_per_second'] or 0:.2f} "
        f"tok/s={result['output_tokens_per_second'] or 0:.1f}"
    )
    if result["failure_reasons"]:
        print("  " + "; ".join(result["failure_reasons"]))


# Input: Capacity bounds.
# Output: Increasing coarse levels ending at the configured maximum.
def capacity_levels(max_concurrency: int) -> list[int]:
    levels = [value for value in (1, 5, 10, 20) if value <= max_concurrency]
    value = 40
    while value < max_concurrency:
        levels.append(value)
        value *= 2
    if not levels or levels[-1] != max_concurrency:
        levels.append(max_concurrency)
    return levels


# Input: One prompt profile and capacity limits.
# Output: Measurements plus the last passing and first failing levels.
async def find_capacity(
    client: AsyncOpenAI,
    args: argparse.Namespace,
    profile: str,
) -> tuple[list[dict[str, Any]], int, int | None]:
    measured: dict[int, dict[str, Any]] = {}
    last_pass = 0
    first_fail = None
    for concurrency in capacity_levels(args.max_concurrency):
        result = await run_level(client, args, profile, concurrency)
        measured[concurrency] = result
        if result["passed"]:
            last_pass = concurrency
            continue
        first_fail = concurrency
        break
    if first_fail is not None:
        low, high = last_pass, first_fail
        while high - low > 1:
            middle = (low + high) // 2
            result = await run_level(client, args, profile, middle)
            measured[middle] = result
            if result["passed"]:
                low = middle
            else:
                high = middle
        last_pass, first_fail = low, high
    return [measured[key] for key in sorted(measured)], last_pass, first_fail


async def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    random.seed(args.seed)
    await require_health(args.health_url, min(args.timeout, 30))
    environment_before = environment_info()
    profiles = list(PROMPTS) if args.profile == "all" else [args.profile]
    results = []
    capacity = {}
    async with AsyncOpenAI(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        max_retries=0,
    ) as client:
        for profile in profiles:
            for _ in range(args.warmup):
                warmup = await run_stream(
                    client,
                    args.model,
                    PROMPTS[profile],
                    min(args.max_tokens, 32),
                    asyncio.Semaphore(1),
                )
                if warmup.error:
                    raise RuntimeError(f"{profile} warm-up failed: {warmup.error}")
            if args.find_max:
                found, last_pass, first_fail = await find_capacity(
                    client, args, profile
                )
                results.extend(found)
                capacity[profile] = {
                    "max_sustainable_concurrency": last_pass,
                    "first_failing_concurrency": first_fail,
                    "required_concurrency": args.required_concurrency,
                    "required_concurrency_passed": last_pass
                    >= args.required_concurrency,
                }
            else:
                for concurrency in args.concurrency:
                    results.append(
                        await run_level(client, args, profile, concurrency)
                    )
    await require_health(args.health_url, min(args.timeout, 30))
    return {
        "schema_version": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment_before": environment_before,
        "environment_after": environment_info(),
        "base_url": args.base_url,
        "model": args.model,
        "seed": args.seed,
        "repetitions": args.repetitions,
        "warmup": args.warmup,
        "max_tokens": args.max_tokens,
        "gates": {
            "max_p95_ttft_ms": args.max_p95_ttft_ms,
            "max_p95_total_ms": args.max_p95_total_ms,
        },
        "capacity": capacity,
        "results": results,
    }


def _ms(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}ms"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8080/v1"),
    )
    parser.add_argument(
        "--health-url",
        default=os.getenv("HAI_HEALTH_URL", "http://127.0.0.1:2019/health"),
    )
    parser.add_argument(
        "--api-key", default=os.getenv("OPENAI_API_KEY", "local-test-key")
    )
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL"), required=False)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 10, 20, 30])
    parser.add_argument("--profile", choices=["short", "long", "all"], default="all")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--max-tokens", type=int, default=192)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--find-max", action="store_true")
    parser.add_argument("--max-concurrency", type=int, default=128)
    parser.add_argument("--required-concurrency", type=int, default=20)
    parser.add_argument("--max-p95-ttft-ms", type=float, default=5000)
    parser.add_argument("--max-p95-total-ms", type=float, default=30000)
    parser.add_argument("--include-samples", action="store_true")
    parser.add_argument("--no-progress", action="store_true")
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks/results/latest.json"),
    )
    args = parser.parse_args()
    if not args.model:
        parser.error("--model or OPENAI_MODEL is required")
    if (
        any(value < 1 for value in args.concurrency)
        or args.repetitions < 1
        or args.warmup < 0
        or args.max_concurrency < 1
        or args.required_concurrency < 1
        or args.max_p95_ttft_ms <= 0
        or args.max_p95_total_ms <= 0
    ):
        parser.error("concurrency and gate values must be positive")
    return args


def main() -> int:
    args = parse_args()
    result = asyncio.run(benchmark(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    errors = sum(item["errors"] for item in result["results"])
    if args.find_max:
        gate_failed = any(
            not item["required_concurrency_passed"]
            for item in result["capacity"].values()
        )
    else:
        gate_failed = any(not item["passed"] for item in result["results"])
    print(f"Wrote {args.output} ({errors} request errors)")
    if args.find_max:
        for profile, item in result["capacity"].items():
            print(
                f"{profile}: max sustainable concurrency "
                f"{item['max_sustainable_concurrency']}"
            )
    if errors and not args.find_max:
        return 1
    return 2 if gate_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
