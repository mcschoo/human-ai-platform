"""Offline tests for benchmark result calculations."""

from benchmarks.stream_benchmark import Sample, percentile, summarize


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
