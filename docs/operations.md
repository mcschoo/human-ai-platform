# Operations runbook

Run commands from the repository root on the Linux GPU host. Replace service
names in targeted commands with those shown by `docker compose config --services`.
For a DNS/TLS deployment, add `-f compose.yaml -f compose.tls.yaml` to every
Compose command so the certificate and ports remain active.

## Start, stop, and status

Start or reconcile the stack:

```bash
docker compose up -d
docker compose ps
```

Run the operator check after exporting `OPENAI_BASE_URL` and `OPENAI_API_KEY`:

```bash
sh scripts/status.sh
```

For a concise API/GPU status report, run:

```bash
./scripts/status.sh
```

Stop containers while retaining volumes and cached models:

```bash
docker compose stop
```

Start stopped containers:

```bash
docker compose start
```

Remove containers and the project network, while retaining named volumes:

```bash
docker compose down
```

Do not add `--volumes` unless permanent data and model caches have been backed
up or are intentionally being deleted.

## Logs and health

Follow all logs:

```bash
docker compose logs --follow --tail=200
```

Inspect one service:

```bash
docker compose logs --follow --tail=200 SERVICE
docker inspect --format '{{json .State.Health}}' CONTAINER
```

Avoid sharing logs until they have been reviewed for participant prompts,
identifiers, API keys, and study data.

## API key rotation

1. Schedule a short maintenance window. This minimal deployment accepts one
   API key at a time.
2. Generate a cryptographically random key:

   ```bash
   ./scripts/generate-api-key.sh
   ```

3. Update the key in `.env` or the deployment's secret store. Never put it in
   shell history, tickets, source control, or participant-facing code.
4. Reconcile the stack:

   ```bash
   docker compose up -d --force-recreate
   ```

5. Test with the new key, update consuming applications, then revoke the old
   key. Confirm that the old key receives `401 Unauthorized`.

If a key may have leaked, rotate immediately and review Caddy and vLLM logs.

## Model switching

Treat a model change as a research-instrument change: record the profile, model
revision, configuration, and switch time with the study metadata.

1. Stop new client traffic and wait for in-flight requests.
2. Back up `.env` and any locally maintained configuration.
3. Set `MODEL_PROFILE` in `.env` to a profile defined by the repository's model
   configuration: `qwen3.5-9b-bf16` or `qwen3.5-27b-gptq-int4`.
4. Pull images and recreate:

   ```bash
   docker compose pull
   docker compose up -d --force-recreate
   ```

5. Watch startup and model-loading logs:

   ```bash
   docker compose logs --follow --tail=200
   ```

6. Run a small `/v1/responses` or `/v1/chat/completions` request and verify the
   expected model identity, response schema, latency, and GPU memory use.
7. Resume traffic only after health checks pass.

Switching can download large weights, invalidate warm caches, change behavior,
and interrupt requests. Do not switch profiles during an active experimental
session unless the protocol explicitly requires and records it.

## Cache cleanup

Measure usage before deleting anything:

```bash
docker system df
docker compose ps
```

Remove only unused Docker build cache and images:

```bash
docker builder prune
docker image prune
```

Model weights commonly live in a named volume or host directory. Stop the stack,
identify the configured cache mount with `docker compose config`, and back up or
remove only weights that are not used by the active profile. Never run broad
volume-pruning commands on a host that contains unverified data.

## Backup

Back up before upgrades, profile changes, and cleanup:

- `.env` and TLS private material through an approved encrypted secret-backup
  process (not in the same archive as ordinary configuration)
- Compose and configuration files at a known Git commit
- Caddy certificate state if automatic HTTPS is enabled
- Study data owned by consuming applications, using that application's runbook

To back up a named volume while the stack is stopped:

```bash
docker compose stop
docker run --rm \
  -v VOLUME_NAME:/source:ro \
  -v "$PWD/backups":/backup \
  alpine tar -czf /backup/VOLUME_NAME.tgz -C /source .
docker compose start
```

Store backups encrypted, test restoration, and apply the study's retention and
deletion schedule. Cached model weights can usually be re-downloaded and may be
excluded if licensing and availability have been checked.

## Upgrade

1. Record the current Git commit, image digests, model profile, and a successful
   smoke-test response.
2. Back up configuration and persistent state.
3. Review release notes and configuration changes.
4. Fetch the intended tagged release, then:

   ```bash
   docker compose pull
   docker compose config --quiet
   docker compose up -d
   docker compose ps
   ```

5. Check logs and repeat the smoke test before restoring client traffic.

   ```bash
   ./scripts/smoke-test.sh
   ```

Avoid deploying unreviewed moving tags in the middle of a study. Pin releases
and image versions so the environment can be reproduced.

## Rollback

1. Stop client traffic.
2. Check out the previously recorded release or commit.
3. Restore the corresponding `.env`, configuration, and persistent data if an
   upgrade migrated them.
4. Recreate using the previously recorded image digests:

   ```bash
   docker compose pull
   docker compose up -d --force-recreate
   ```

5. Verify health and run the saved smoke test.

Do not restore an older application over newer persistent data unless its
backward compatibility is documented. Restore the matching backup instead.

## Troubleshooting

### GPU is unavailable

Run `nvidia-smi` on the host, then repeat the NVIDIA container test from the
main README. If the host works but the container fails, reconfigure NVIDIA
Container Toolkit for Docker and restart Docker. Confirm that the Compose
rendered configuration requests a GPU.

### Service is unhealthy or repeatedly restarts

Use `docker compose ps` and service logs. Common causes are a missing required
environment variable, unreadable TLS files, insufficient GPU memory, an invalid
model profile, or insufficient disk space. Validate configuration with:

```bash
docker compose config --quiet
```

### Model download fails

Check DNS, outbound HTTPS, disk space, provider credentials, model license
acceptance, and access permissions. A partially downloaded cache may need
targeted removal after the stack is stopped.

### CUDA out of memory

Stop other GPU workloads, verify GPU processes with `nvidia-smi`, and choose a
smaller or more quantized model profile. Reducing client concurrency may help
runtime pressure but does not make a model fit if its weights exceed capacity.

### `401 Unauthorized`

Confirm the client sends `Authorization: Bearer ...`, that its key matches
`VLLM_API_KEY`, and that vLLM was recreated after rotation. Do not print
the complete key while diagnosing.

### `502`, `503`, timeout, or unexpectedly slow first request

The model may still be loading or downloading. Follow inference-service logs
and inspect GPU utilization. First-token latency is usually higher after start
or a profile switch. A proxy timeout can also be shorter than model startup or
request latency.

### TLS or DNS errors

Verify the DNS record resolves to the intended host, ports 80/443 are permitted,
the certificate covers the hostname, and host time is correct. Do not bypass
certificate verification in research clients.

## Security boundary

This platform's boundary is the authenticated inference API, its reverse proxy,
model runtime, configuration/secrets, Docker host, GPU, and attached persistent
storage. Operators are responsible for host hardening, firewalling, TLS,
authentication, patching, log access, backups, and restricting administrative
interfaces to a trusted network.

The platform does **not** provide participant enrollment, authorization,
consent, withdrawal, debriefing, IRB/ethics approval, recruitment-platform
integration, experiment logic, or a complete study-data governance program.
Those controls stay in GRAIL or another consuming application. The consuming
application must minimize prompts and metadata, disclose AI use as required,
avoid sending unnecessary identifiers, enforce retention/deletion, and handle
participant rights.

API authentication separates clients from anonymous access; it is not a
participant identity system and does not make model output safe or correct.
Assume prompts can be adversarial and outputs can be inaccurate. Keep the API
off the public internet where possible, use least privilege, rotate secrets,
and never place long-lived server keys in browser-delivered code.

The included deployment has one shared API key and a 2 MB request-body limit;
it does not provide per-user quotas or rate limits. Restrict remote access by
source network in the host or institutional firewall. If mutually untrusted
teams need separate quotas, place an institution-supported identity/rate-limit
gateway in front rather than extending this repository with custom auth code.
