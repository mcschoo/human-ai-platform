# Application integration

This guide describes the minimum backend functionality an application needs to
use the Human AI Control Plane. The application owns its users, interface, and
session lifecycle. The control plane selects virtual participants, generates
messages, schedules delivery, and keeps private audit data.

## Minimum application adapter

An application backend must:

1. Keep its application token and webhook secret on the server.
2. register each session before sending events;
3. send session, channel, and message events with stable IDs;
4. expose an HTTP callback URL reachable from the control-plane container;
5. verify callback signatures and reject stale timestamps;
6. process each callback action once, using its idempotency key; and
7. return a `2xx` response after an action is stored or applied.

Do not put the application token, webhook secret, AI provenance, private
context, or personality prompts in participant browsers.

## 1. Register the application

The simplest option is the operator dashboard at
`http://127.0.0.1:8090/operator`. Enter:

- a stable application ID;
- a human-readable display name; and
- the callback URL that will receive control-plane actions.

Save the generated application token and webhook secret. They are displayed
only once.

Operators can also register an application directly:

```http
POST /v1/apps
X-Operator-Password: operator-password
Content-Type: application/json
```

```json
{
  "appId": "example-app",
  "displayName": "Example application",
  "callbackUrl": "http://application-host:3002/control-plane/actions",
  "bearerToken": "application-generated-secret-at-least-24-characters",
  "webhookSecret": "different-secret-at-least-24-characters",
  "capabilities": {
    "typingIndicators": true,
    "cancelActions": true
  },
  "defaults": {
    "systemPrompt": "Participate naturally as the assigned participant.",
    "responseProbability": 1,
    "maxRespondents": 1
  }
}
```

All remaining application requests use:

```http
Authorization: Bearer application-generated-secret-at-least-24-characters
```

## 2. Register a session

Register before sending any lifecycle or message event:

```http
POST /v1/apps/example-app/sessions
Authorization: Bearer APPLICATION_TOKEN
Content-Type: application/json
```

```json
{
  "sessionId": "session-42",
  "groupId": "batch-7",
  "channels": ["intro", "chat"],
  "channelContext": {
    "intro": {
      "instructions": "Greet the group briefly. Do not discuss the task.",
      "includeSharedContext": false,
      "includePrivateContext": false,
      "includePersonality": false,
      "responseProbability": 0.5,
      "maxRespondents": 1
    },
    "chat": {
      "instructions": "Discuss the task in concise, conversational prose.",
      "includeSharedContext": true,
      "includePrivateContext": true,
      "includePersonality": true,
      "responseProbability": 1,
      "maxRespondents": 4
    }
  },
  "sharedContext": {
    "task": "Information available to every virtual participant"
  },
  "participants": [
    {
      "participantId": "human-1",
      "role": "human",
      "display": {
        "name": "Taylor"
      }
    },
    {
      "participantId": "virtual-1",
      "role": "virtual",
      "display": {
        "name": "Jordan"
      },
      "privateContext": {
        "facts": ["Information available only to this participant"]
      },
      "personalityId": "friendly"
    }
  ]
}
```

Identifiers have different scopes:

- `appId` identifies the integrating application.
- `groupId` groups sessions for operator policy and navigation.
- `sessionId` isolates one conversation and its transcript.
- `participantId` identifies a stable participant within the session.
- `channel` identifies the current interaction phase.

Every participant ID and channel name must be unique within its session.
`display` is public presentation data. `privateContext` is available only when
prompting that virtual participant.

## 3. Send runtime events

Send events to:

```http
POST /v1/apps/example-app/events
Authorization: Bearer APPLICATION_TOKEN
Content-Type: application/json
```

Each event needs a unique, stable `eventId`. Retrying the same ID is safe and
returns `"duplicate": true` without scheduling the action twice.

Open the session and its first channel:

```json
{
  "eventId": "evt-session-started-42",
  "sessionId": "session-42",
  "channel": "intro",
  "type": "session.started",
  "occurredAt": "2026-09-18T12:00:00Z"
}
```

```json
{
  "eventId": "evt-intro-opened-42",
  "sessionId": "session-42",
  "channel": "intro",
  "type": "channel.opened",
  "occurredAt": "2026-09-18T12:00:01Z"
}
```

Forward each user-visible message:

```json
{
  "eventId": "evt-message-101",
  "sessionId": "session-42",
  "channel": "chat",
  "type": "message.created",
  "senderId": "human-1",
  "text": "Jordan, what do you think?",
  "occurredAt": "2026-09-18T12:01:10Z",
  "metadata": {
    "mentionedParticipantIds": ["virtual-1"]
  }
}
```

Direct mentions are optional but should be supplied when the application
supports them. They give the mentioned virtual participant priority.

Use these event types:

| Event | When to send it |
| --- | --- |
| `session.started` | The registered session becomes active. |
| `channel.opened` | A registered interaction phase begins. |
| `message.created` | A human, system, or participant message is committed. |
| `channel.closed` | The interaction phase stops accepting AI replies. |
| `session.ended` | The session ends and pending actions are cancelled. |

Every event requires a channel that was listed in the session registration.

## 4. Receive callback actions

The control plane sends `POST` requests to the registered callback URL. A
message action has this shape:

```json
{
  "actionId": "action-generated-by-control-plane",
  "appId": "example-app",
  "sessionId": "session-42",
  "channel": "chat",
  "type": "message.deliver",
  "participantId": "virtual-1",
  "text": "I think we should compare the shared evidence first.",
  "sourceEventId": "evt-message-101",
  "createdAt": "2026-09-18T12:01:12Z"
}
```

Supported action types are:

| Action | Minimum behavior |
| --- | --- |
| `typing.set` | Show the virtual participant as typing. |
| `typing.clear` | Remove its typing indicator. |
| `message.deliver` | Add the text as a normal message from `participantId`. |
| `actions.cancel` | Discard matching pending local actions when supported. |
| `session.end` | End local AI processing for the session. |

The callback includes:

```http
Content-Type: application/json
X-Control-Plane-Timestamp: 1789732872
X-Control-Plane-Signature: sha256=LOWERCASE_HEX_SIGNATURE
Idempotency-Key: action-generated-by-control-plane
```

Calculate the expected signature over the exact request bytes:

```text
HMAC-SHA256(
  WEBHOOK_SECRET,
  X-Control-Plane-Timestamp + "." + exact_request_body
)
```

Compare signatures with a constant-time function. Reject timestamps outside a
short window, such as five minutes. Store processed `Idempotency-Key` values so
a retry cannot duplicate a message.

Return `2xx` only after the action has been applied or durably queued. The
control plane retries non-`2xx` responses with bounded exponential backoff.

## 5. Minimal lifecycle

The complete minimum sequence is:

```text
Operator registers application
Application registers session
Application sends session.started
Application sends channel.opened
Application sends message.created events
Application receives signed callback actions
Application sends channel.closed
Application sends session.ended
```

The control plane API documentation is also available at
`http://127.0.0.1:8090/docs`.

## Integration checklist

- The callback address is reachable from inside the control-plane container.
- Application and webhook secrets are different and server-only.
- Session registration happens before the first event.
- Event IDs remain unchanged when requests are retried.
- Only registered channel names are used.
- Message events include committed text and the true sender ID.
- Callback timestamp and HMAC signature are verified against the raw body.
- Callback action IDs are processed once.
- Message delivery preserves the supplied participant ID and display mapping.
- Session and channel close events stop late AI replies.
