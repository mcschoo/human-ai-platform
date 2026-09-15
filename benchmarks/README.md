# Capacity benchmark

Run this on the intended GPU host after the smoke test:

```bash
python benchmarks/stream_benchmark.py \
  --model qwen3.5-9b \
  --concurrency 1 10 20 30 \
  --profile all \
  --output benchmark-results-9b.json
```

The short profile measures decode-heavy interactive traffic. The long profile
adds roughly 8,000 characters of input per request to exercise prefill and KV
cache pressure. Each request streams up to 192 output tokens by default.
The runner warms each prompt profile, records output-token throughput, and
captures GPU identity and memory usage before and after the run.

A profile passes the basic capacity gate only when:

- all requests complete without protocol or request errors;
- the vLLM container remains healthy and reports no out-of-memory event;
- 20- and 30-stream results include valid p50 and p95 TTFT and total latency;
- measured latency is acceptable for the intended study; and
- `nvidia-smi` shows operational memory headroom after the run.

Archive the JSON result with the server GPU, model profile, repository commit,
and study deployment notes. Compare candidates using identical arguments.
