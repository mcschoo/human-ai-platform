# Human AI Platform

A small Linux/NVIDIA stack for serving an open-weight model and coordinating AI
participants. It contains three services:

- vLLM exposes OpenAI-compatible Chat Completions and Responses APIs.
- Caddy keeps the model API authenticated and bound to localhost.
- The FastAPI [control plane](services/control-plane/README.md) manages
  applications, AI personalities, session context, callbacks, and audit data.

The consuming application remains responsible for consent, debriefing, data
retention, and all study-specific participant policy.

## Requirements

The tested host is Linux with an NVIDIA L40S, driver 565.57.01, 48 GB VRAM,
Python 3.10, Docker Compose 2.24.4 or newer, and NVIDIA Container Toolkit. The
pinned vLLM 0.28.0 image uses CUDA 13.0.3 compatibility libraries.

Use the built-in check for another controlled Linux/NVIDIA server:

```bash
./client.sh doctor
```

It prints exact fixes for missing prerequisites. It does not install or replace
system drivers.

## Deploy

```bash
./client.sh deploy
```

The first run creates `.env` with random secrets, starts the stack, and waits
with a progress display until the model is ready. Docker displays container
image downloads, while the CLI tracks model download and loading stages. Later
operations use the same entry point:

```bash
./client.sh status
./client.sh logs
./client.sh test
./client.sh down
```

The local model API is `http://127.0.0.1:8080/v1`. The private operator UI is
`http://127.0.0.1:8090/operator/login`. Use SSH port forwarding to access these
ports from another computer.

## Models

Gemma 4 12B is the default text-only model:

```bash
./client.sh deploy --profile gemma-4-12b-bf16
```

The repository retains two optional Qwen profiles:

```bash
./client.sh deploy --profile qwen3.5-9b-bf16
./client.sh deploy --profile qwen3.5-27b-gptq-int4
```

Profiles in `config/models/` pin the Hugging Face revision, served name,
precision, context limit, GPU allocation, and reasoning parser. Changing a
profile recreates the model service and briefly interrupts requests.

## Test capacity

Run fixed concurrency levels:

```bash
./client.sh benchmark --concurrency 1 10 20 40
```

Find the highest level that has zero errors, p95 time-to-first-token at or below
5 seconds, and p95 total latency at or below 30 seconds:

```bash
./client.sh benchmark --find-max --max-concurrency 128
```

Both latency limits, request counts, token limits, and the search ceiling are
command-line options. Results are written under `benchmarks/results/`, which is
not committed.

## Connect an application

Register the application in the operator UI and give its backend:

```dotenv
CONTROL_PLANE_URL=http://127.0.0.1:8090
CONTROL_PLANE_APP_ID=your-app-id
CONTROL_PLANE_APP_TOKEN=generated-app-token
CONTROL_PLANE_WEBHOOK_SECRET=generated-webhook-secret
```

The application registers sessions and sends events. The control plane returns
signed typing and message actions to its callback URL. Full request models,
authentication rules, and callback signing are documented in
[`services/control-plane/README.md`](services/control-plane/README.md).

## Source layout

```text
client.sh                  Developer entry point
compose.yaml               Complete runtime stack
config/platform.env        Tested host and benchmark requirements
config/models/             Pinned model profiles
benchmarks/                Capacity benchmark
services/control-plane/    Generic AI participant service
tests/                     Model API and benchmark tests
```

Licensed under the [MIT License](LICENSE).
