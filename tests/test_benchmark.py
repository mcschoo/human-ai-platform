"""Offline tests for benchmark result calculations."""

from argparse import Namespace

import pytest

import benchmarks.stream_benchmark as benchmark_module
from benchmarks.stream_benchmark import (
    Sample,
    capacity_levels,
    evaluate,
    percentile,
    summarize,
)


def test_percentile_uses_nearest_rank() -> None:
    values = [40.0, 10.0, 30.0, 20.0]
    assert percentile(values, 0.50) == 20.0
    assert percentile(values, 0.95) == 40.0
    assert percentile([], 0.95) is None


def test_summary_counts_protocol_and_request_errors() -> None:
    samples = [
        Sample(ttft_ms=10, total_ms=20),
        Sample(
            ttft_ms=12,
            total_ms=25,
            error="malformed streaming response",
            protocol_error=True,
        ),
        Sample(ttft_ms=None, total_ms=5, error="timeout"),
    ]
    result = summarize("short", 3, samples)
    assert result["successes"] == 1
    assert result["errors"] == 2
    assert result["protocol_errors"] == 1
    assert result["ttft_ms"]["p95"] == 10


def test_summary_reports_request_rate_and_capacity_gate() -> None:
    result = summarize(
        "short",
        2,
        [
            Sample(ttft_ms=100, total_ms=500, output_tokens=10),
            Sample(ttft_ms=200, total_ms=800, output_tokens=20),
        ],
        batch_ms=1000,
    )
    passed, reasons = evaluate(result, 5000, 30_000)
    assert passed
    assert reasons == []
    assert result["requests_per_second"] == 2
    assert result["output_tokens_per_second"] == 30


def test_capacity_gate_explains_latency_and_request_failures() -> None:
    result = summarize(
        "short",
        2,
        [
            Sample(ttft_ms=6000, total_ms=31_000),
            Sample(ttft_ms=None, total_ms=100, error="timeout"),
        ],
    )
    passed, reasons = evaluate(result, 5000, 30_000)
    assert not passed
    assert any("request errors" in reason for reason in reasons)
    assert any("TTFT" in reason for reason in reasons)
    assert any("total" in reason for reason in reasons)


def test_capacity_levels_include_limits_without_duplicates() -> None:
    assert capacity_levels(1) == [1]
    assert capacity_levels(20) == [1, 5, 10, 20]
    assert capacity_levels(50) == [1, 5, 10, 20, 40, 50]
    assert capacity_levels(128) == [1, 5, 10, 20, 40, 80, 128]


@pytest.mark.asyncio
async def test_find_capacity_narrows_first_failure(monkeypatch) -> None:
    async def fake_level(client, args, profile, concurrency):
        return {
            "profile": profile,
            "concurrency": concurrency,
            "passed": concurrency <= 37,
        }

    monkeypatch.setattr(benchmark_module, "run_level", fake_level)
    results, last_pass, first_fail = await benchmark_module.find_capacity(
        None,
        Namespace(max_concurrency=50),
        "short",
    )
    assert last_pass == 37
    assert first_fail == 38
    assert [item["concurrency"] for item in results] == sorted(
        item["concurrency"] for item in results
    )
