# AI participant control plane

This service owns virtual-participant selection, prompts, model context, timing,
callback delivery, and private audit data. Applications own their study
lifecycle and participant-facing policy.

## Run

The root Compose stack publishes the operator UI at
`http://127.0.0.1:8090/operator`. Set strong
`CONTROL_PLANE_OPERATOR_PASSWORD` and `CONTROL_PLANE_COOKIE_SECRET` values in
`.env` first. The API documentation is at `/docs`.

For local development:

```bash
cd services/control-plane
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[test]"
CONTROL_PLANE_DB=/tmp/control-plane.db uvicorn control_plane.main:app --port 8090
```

## Contract

See the root [application integration guide](../../docs/application-integration.md)
for the minimum adapter lifecycle, complete request examples, callback actions,
and a deployment checklist.

All application routes use `Authorization: Bearer APP_TOKEN`. Operators create
an app with `POST /v1/apps` and the `X-Operator-Password` header. An app then
registers sessions and sends unique events:

- `POST /v1/apps/{appId}/sessions`
- `POST /v1/apps/{appId}/events`

The strict model-context key is `appId + sessionId`; channel further limits the
transcript. `groupId` is management metadata and may select an operator-defined
policy. Participant private context and personality prompts never enter callback
payloads.

Session registration supplies:

- `groupId` and the unique `sessionId`
- shared study context
- active channel names
- stable human and virtual participant IDs
- public display data and private context for each virtual slot

Message events may include `metadata.mentionedParticipantIds`. Direct mentions
take priority; other respondents are ranked against their private context.

Callbacks are JSON actions with an `Idempotency-Key` equal to `actionId`.
Verify `X-Control-Plane-Signature` as lowercase hex HMAC-SHA256 over:

```text
X-Control-Plane-Timestamp + "." + exact_request_body
```

Reject timestamps outside a short window and remember action IDs. The service
retries non-2xx responses with bounded exponential backoff. Pending deliveries
and attempts survive restart in SQLite.

## Operator API

Operator cookie or `X-Operator-Password` protects personality, group-policy,
session/audit, webhook-health, and CSV export routes under `/v1/operator`.
The dashboard follows application → group → session and shows private
assignments, transcripts, audit entries, and callback state. It can register
applications, create personality templates, and assign templates to participant
IDs for a group.

## Operations

- Callback URLs must be reachable from the control-plane container. Test this
  before a study.
- A host application may bind its signed callback listener to the Compose
  network gateway and register that address as its callback URL.
- Rotate an app token and webhook secret by posting its registration again.
  Coordinate webhook-secret rotation because there is no dual-secret window.
- Back up the `control-plane-data` volume while writes are stopped, or use the
  SQLite online backup command.
- Exports do not delete records. Set a study-specific retention process for the
  database and backups.
- For cutover from another orchestrator, stop its reply path first, register
  active sessions here, then enable forwarding. Do not run both reply paths.
- Existing private data should be mapped to shared context, slot private
  context, and personality templates only after a data-owner review.

## Tests

```bash
pytest -q
CONTROL_PLANE_LIVE_TESTS=1 pytest -q -m live
```

The live test also needs `CONTROL_PLANE_VLLM_URL`, `VLLM_API_KEY`, and
`CONTROL_PLANE_MODEL`.
