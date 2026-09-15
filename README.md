# Human AI Platform

A self-hosted, OpenAI-compatible inference service for research applications. The
platform keeps model serving on infrastructure you control and exposes
`/v1/chat/completions` and `/v1/responses` to clients such as GRAIL.

> This repository provides model infrastructure, not a human-subjects research
> protocol. Consent, withdrawal, debriefing, participant-facing disclosure, and
> study-specific data-retention policy remain the responsibility of the consuming
> application and research team.

## Prerequisites

- A Linux host with a supported NVIDIA GPU and current NVIDIA driver
- Docker Engine with Docker Compose 2.24.4 or newer (`docker compose`)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
  configured for Docker
- Enough disk for the selected model and its cache
- One access choice:
  - **Local/private network:** bind the API only to a trusted interface and use
    an SSH tunnel or private network; or
  - **Shared/remote access:** create a DNS record, allow inbound TCP 80/443, and
    let the included Caddy service obtain and renew the TLS certificate.

Confirm GPU container access before setup:

```bash
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi
```

## Install to first request

```bash
git clone <repository-url> human-ai-platform
cd human-ai-platform
sh scripts/setup.sh
```

The setup command creates `.env` with a random API key, validates the
configuration, and starts the default model. PowerShell users can run
`.\scripts\setup.ps1`. Treat `.env` as a secret and do not commit it.

The defaults listen only on `127.0.0.1:8080`. For automatic HTTPS, stop the
stack and set these values in `.env`:

```dotenv
PUBLIC_BIND_ADDRESS=0.0.0.0
TLS_DOMAIN=llm.example.edu
```

Then use the TLS override whenever operating the stack:

```bash
docker compose -f compose.yaml -f compose.tls.yaml up -d
```

Wait for services to become healthy:

```bash
docker compose ps
```

Set the URL and key to the values selected in `.env`. For a host-local
deployment, the base URL is `http://localhost:8080/v1`; a TLS
deployment should use `https://YOUR_DNS_NAME/v1`.

```bash
export OPENAI_BASE_URL=http://localhost:8080/v1
export OPENAI_API_KEY='replace-with-your-key'
```

Make a chat-completions request:

```bash
curl --fail-with-body "$OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-9b",
    "messages": [{"role": "user", "content": "Reply with exactly: ready"}],
    "max_tokens": 16
  }'
```

Or use the Responses API:

```bash
curl --fail-with-body "$OPENAI_BASE_URL/responses" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-9b",
    "input": "Reply with exactly: ready",
    "max_output_tokens": 16
  }'
```

The advertised name comes from the active profile. The included profiles serve
`qwen3.5-9b` and `qwen3.5-27b-gptq-int4`.
Clients should always set `max_tokens` or `max_output_tokens`; the platform
does not impose a custom output limit beyond the model's 8K total context cap.
The profiles default to Qwen's non-thinking mode so structured output remains
reliable within ordinary token limits.

The 27B checkpoint occupies about 30.2 GB on disk and is experimental on a
32 GB GPU. Use it only after its 20/30-stream benchmark passes with adequate
GPU-memory headroom; the 9B BF16 profile is the safer default candidate.

## Python client

```bash
cd examples/openai-client
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python client.py
```

The example uses the official OpenAI SDK and works against both local HTTP and
DNS/TLS deployments.

## Switch model profile

Set `MODEL_PROFILE` in `.env` to `qwen3.5-9b-bf16` or
`qwen3.5-27b-gptq-int4`, then recreate the stack:

```bash
docker compose up -d --force-recreate
docker compose ps
```

For TLS deployments, include both Compose files as shown in the HTTPS setup
section when running these commands.

The change may download model weights and briefly interrupt requests. Validate
the active model with a small request before resuming a study. See
[Model switching](docs/operations.md#model-switching) for a safer operational
sequence.

## Validate a deployment

Install the small validation environment and run the smoke checks:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
export OPENAI_BASE_URL=http://localhost:8080/v1
export OPENAI_API_KEY='replace-with-your-key'
export OPENAI_MODEL=qwen3.5-9b
sh scripts/smoke-test.sh
python benchmarks/stream_benchmark.py --model "$OPENAI_MODEL"
```

The benchmark tests 1, 10, 20, and 30 concurrent streams and writes
`benchmark-results.json`. Run it on the intended 32 GB server before declaring
a model profile supported.

## Next steps

- [Operations runbook](docs/operations.md)
- [Minimal OpenAI Python client](examples/openai-client/)
- [Connect GRAIL](examples/grail/)
- [Security boundary](docs/operations.md#security-boundary)

## License

[MIT](LICENSE)
