# Connect GRAIL

GRAIL's server calls the OpenAI Responses API directly. Human AI Platform
provides the compatible `/v1/responses` endpoint.

## Configure

Copy `.env.example` from this directory to GRAIL's `server/.env`, then replace
the key and endpoint:

```bash
cp examples/grail/.env.example /path/to/GRAIL/server/.env
```

- If GRAIL runs on the inference host, use
  `http://localhost:8080/v1/responses`.
- If GRAIL runs in a container, `localhost` means that container. Use a
  routable private hostname, a shared Docker network hostname, or the platform's
  DNS/TLS URL instead.
- Keep the API key server-side. Never put it in GRAIL client code or a value
  sent to participants.

Restart GRAIL after changing its environment.

## Request and response contract

GRAIL sends an OpenAI Responses request similar to:

```json
{
  "model": "qwen3.5-9b",
  "max_output_tokens": 1000,
  "input": [
    {"role": "system", "content": "Facilitator instructions"},
    {"role": "user", "content": "Timestamped conversation transcript"}
  ],
  "text": {"format": {"type": "json_object"}}
}
```

The endpoint returns an OpenAI Responses envelope. The generated facilitator
text is nested under `output[].content[]`:

```json
{
  "id": "resp_...",
  "object": "response",
  "status": "completed",
  "model": "qwen3.5-9b",
  "output": [
    {
      "type": "message",
      "role": "assistant",
      "content": [
        {
          "type": "output_text",
          "text": "{\"MESSAGE\":\"Compare the evidence.\",\"RATIONALE\":\"Needed.\"}"
        }
      ]
    }
  ],
  "usage": {
    "input_tokens": 120,
    "output_tokens": 8,
    "total_tokens": 128
  }
}
```

IDs and token counts vary. GRAIL should treat a non-2xx response, a status other
than `completed`, or missing `output_text` as an inference failure rather than
showing a malformed message to participants.

GRAIL requests JSON-object output and parses the `output_text` string as JSON.
Its prompt determines whether the object contains `MESSAGE` and `RATIONALE`, or
also `DECISION`. The API envelope remains unchanged.

## Research responsibility

Connecting the endpoint does not implement a research protocol. Participant
consent, AI disclosure, withdrawal, debriefing, moderation, study-specific
fallback behavior, and retention/deletion controls remain in GRAIL and the
research team's procedures. Review prompts and logs for personal or sensitive
data before enabling collection.
