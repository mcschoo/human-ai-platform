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


async def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    random.seed(args.seed)
    await require_health(args.health_url, min(args.timeout, 30))
    environment_before = environment_info()
    profiles = list(PROMPTS) if args.profile == "all" else [args.profile]
    results: list[dict[str, Any]] = []
    async with AsyncOpenAI(
        base_url=args.base_url,
        api_key=args.api_key,
        timeout=args.timeout,
        max_retries=0,
    ) as client:
        for profile in profiles:
            prompt = PROMPTS[profile]
            for _ in range(args.warmup):
                warmup = await run_stream(
                    client,
                    args.model,
                    prompt,
                    min(args.max_tokens, 32),
                    asyncio.Semaphore(1),
                )
                if warmup.error:
                    raise RuntimeError(f"{profile} warm-up failed: {warmup.error}")
            for concurrency in args.concurrency:
                semaphore = asyncio.Semaphore(concurrency)
                tasks = [
                    run_stream(client, args.model, prompt, args.max_tokens, semaphore)
                    for _ in range(concurrency * args.repetitions)
                ]
                batch_started = time.perf_counter()
                samples = await asyncio.gather(*tasks)
                batch_ms = (time.perf_counter() - batch_started) * 1000
                result = summarize(profile, concurrency, samples, batch_ms)
                results.append(result)
                ttft = result["ttft_ms"]
                total = result["total_ms"]
                print(
                    f"{profile:5} c={concurrency:2} n={len(samples):3} "
                    f"ttft p50/p95={_ms(ttft['p50'])}/{_ms(ttft['p95'])} "
                    f"total p50/p95={_ms(total['p50'])}/{_ms(total['p95'])} "
                    f"tok/s={result['output_tokens_per_second'] or 0:.1f} "
                    f"errors={result['errors']} protocol={result['protocol_errors']}"
                )
    await require_health(args.health_url, min(args.timeout, 30))
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "environment_before": environment_before,
        "environment_after": environment_info(),
        "base_url": args.base_url,
        "model": args.model,
        "seed": args.seed,
        "repetitions": args.repetitions,
        "warmup": args.warmup,
        "max_tokens": args.max_tokens,
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
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument("--output", type=Path, default=Path("benchmark-results.json"))
    args = parser.parse_args()
    if not args.model:
        parser.error("--model or OPENAI_MODEL is required")
    if (
        any(value < 1 for value in args.concurrency)
        or args.repetitions < 1
        or args.warmup < 0
    ):
        parser.error("concurrency/repetitions must be positive and warmup non-negative")
    return args


def main() -> int:
    args = parse_args()
    result = asyncio.run(benchmark(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    errors = sum(item["errors"] for item in result["results"])
    print(f"Wrote {args.output} ({errors} errors)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
