import asyncio
import hashlib
import hmac
import json
import re
from datetime import datetime, timezone

import httpx
import pytest
from control_plane.api import create_app
from control_plane.models import (
    ActionKind,
    CallbackAction,
    GroupPolicy,
    RuntimeEvent,
    SessionRegistration,
)
from control_plane.orchestration import Orchestrator
from control_plane.repository import Repository
from control_plane.settings import Settings
from control_plane.webhooks import WebhookWorker, sign_webhook
from fastapi.testclient import TestClient
from pydantic import ValidationError

TOKEN = "a" * 32
SECRET = "b" * 32
OPERATOR = "operator-test-password"


def app_manifest() -> dict:
    return {
        "appId": "study-app",
        "displayName": "Study app",
        "callbackUrl": "http://callback.test/actions",
        "bearerToken": TOKEN,
        "webhookSecret": SECRET,
        "capabilities": {"typingIndicators": True},
        "defaults": {
            "responseProbability": 1,
            "maxRespondents": 1,
            "timing": {
                "wordsPerMinute": 1000,
                "interceptMs": 0,
                "minDelayMs": 0,
                "maxDelayMs": 0,
                "staggerMs": 0,
            },
        },
    }


def session(session_id: str, private: str) -> dict:
    return {
        "sessionId": session_id,
        "groupId": "group-a",
        "channels": ["chat"],
        "sharedContext": {"topic": private},
        "participants": [
            {
                "participantId": "human-1",
                "role": "human",
                "display": {"name": "Human"},
            },
            {
                "participantId": "ai-1",
                "role": "virtual",
                "display": {"name": "Peer"},
                "privateContext": {"private": private},
                "personalityId": "friendly",
            },
        ],
    }


def event(event_id: str, session_id: str, text: str = "Hello") -> dict:
    return {
        "eventId": event_id,
        "sessionId": session_id,
        "channel": "chat",
        "type": "message.created",
        "senderId": "human-1",
        "text": text,
        "occurredAt": "2026-01-01T00:00:00Z",
    }


def test_schema_rejects_duplicate_participants() -> None:
    body = session("s1", "x")
    body["participants"].append(body["participants"][0])
    with pytest.raises(ValidationError):
        SessionRegistration.model_validate(body)


def test_signature_matches_documented_format() -> None:
    body = b'{"actionId":"one"}'
    expected = hmac.new(SECRET.encode(), b"123." + body, hashlib.sha256).hexdigest()
    assert sign_webhook(body, "123", SECRET) == expected


def test_auth_idempotency_isolation_scheduling_and_provenance(tmp_path) -> None:
    requests: list[dict] = []

    def inference(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={"output_text": "A private-safe reply"})

    settings = Settings(
        database_path=str(tmp_path / "control.db"),
        operator_password=OPERATOR,
        cookie_secret="cookie-test",
        vllm_base_url="http://model.test/v1",
        vllm_api_key="model-key",
    )
    app = create_app(
        settings,
        inference_transport=httpx.MockTransport(inference),
        start_worker=False,
    )
    with TestClient(app) as client:
        assert client.post("/v1/apps", json=app_manifest()).status_code == 401
        created = client.post(
            "/v1/apps",
            json=app_manifest(),
            headers={"X-Operator-Password": OPERATOR},
        )
        assert created.status_code == 200
        headers = {"Authorization": f"Bearer {TOKEN}"}
        bad = client.post(
            "/v1/apps/study-app/sessions",
            json=session("s1", "secret-one"),
            headers={"Authorization": "Bearer wrong"},
        )
        assert bad.status_code == 401
        for session_id, private in [("s1", "secret-one"), ("s2", "secret-two")]:
            response = client.post(
                "/v1/apps/study-app/sessions",
                json=session(session_id, private),
                headers=headers,
            )
            assert response.status_code == 201
        personality = {
            "personalityId": "friendly",
            "name": "Friendly",
            "prompt": "Be warm but brief.",
        }
        assert (
            client.put(
                "/v1/operator/apps/study-app/personalities/friendly",
                json=personality,
                headers={"X-Operator-Password": OPERATOR},
            ).status_code
            == 200
        )
        first = client.post(
            "/v1/apps/study-app/events", json=event("e1", "s1"), headers=headers
        )
        assert first.status_code == 200
        assert first.json()["scheduledActions"] == 3
        duplicate = client.post(
            "/v1/apps/study-app/events", json=event("e1", "s1"), headers=headers
        )
        assert duplicate.json()["duplicate"] is True
        second = client.post(
            "/v1/apps/study-app/events", json=event("e2", "s2"), headers=headers
        )
        assert second.status_code == 200

        mentioned_session = session("s3", "not-mentioned")
        mentioned_session["sharedContext"] = {"topic": "shared"}
        mentioned_session["participants"][1]["privateContext"] = {
            "private": "unselected-secret"
        }
        mentioned_session["participants"].append({
            "participantId": "ai-2",
            "role": "virtual",
            "display": {"name": "Mentioned peer"},
            "privateContext": {"private": "mentioned-secret"},
        })
        assert client.post(
            "/v1/apps/study-app/sessions",
            json=mentioned_session,
            headers=headers,
        ).status_code == 201
        mentioned_event = event("e3", "s3", "@Mentioned peer, what do you think?")
        mentioned_event["metadata"] = {"mentionedParticipantIds": ["ai-2"]}
        assert client.post(
            "/v1/apps/study-app/events",
            json=mentioned_event,
            headers=headers,
        ).status_code == 200

        assert len(requests) == 3
        assert all(payload["store"] is False for payload in requests)
        assert "secret-two" in requests[1]["instructions"]
        assert "secret-one" not in json.dumps(requests[1])
        assert "mentioned-secret" in requests[2]["instructions"]
        assert "unselected-secret" not in requests[2]["instructions"]
        pending = app.state.repo.due_deliveries()
        message = next(
            json.loads(row["action"])
            for row in pending
            if json.loads(row["action"])["type"] == "message.deliver"
        )
        assert "privateContext" not in message
        assert "instructions" not in message
        client.post(
            "/operator/login",
            content=f"password={OPERATOR}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        group_page = client.get("/operator/apps/study-app/groups/group-a")
        assert group_page.status_code == 200
        assert "ai-1" in group_page.text
        assert 'name="assignment:ai-1"' in group_page.text


def test_webhook_retry_records_and_restart_recovery(tmp_path) -> None:
    path = str(tmp_path / "restart.db")
    repo = Repository(path)
    repo.initialize()
    from control_plane.models import AppRegistration

    repo.save_app(AppRegistration.model_validate(app_manifest()))
    action = CallbackAction(
        actionId="action-one",
        appId="study-app",
        sessionId="s1",
        channel="chat",
        type=ActionKind.message_deliver,
        participantId="ai-1",
        text="Hello",
    )
    repo.schedule(action, datetime.now(timezone.utc))
    restarted = Repository(path)
    restarted.initialize()
    seen_headers: list[httpx.Headers] = []

    def callback(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers)
        return httpx.Response(503)

    settings = Settings(database_path=path, max_webhook_attempts=2)
    worker = WebhookWorker(restarted, settings, httpx.MockTransport(callback))
    assert asyncio.run(worker.deliver_due()) == 1
    health = restarted.webhook_health("study-app")
    assert health[0]["status"] == "pending"
    assert health[0]["attempts"] == 1
    assert seen_headers[0]["idempotency-key"] == "action-one"
    assert seen_headers[0]["x-control-plane-signature"].startswith("sha256=")


def test_operator_cookie_and_dashboard_navigation(tmp_path) -> None:
    settings = Settings(
        database_path=str(tmp_path / "dashboard.db"),
        operator_password=OPERATOR,
        cookie_secret="cookie-test",
    )
    app = create_app(settings, start_worker=False)
    with TestClient(app) as client:
        assert client.get("/operator").status_code == 401
        login = client.post(
            "/operator/login",
            content=f"password={OPERATOR}",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        page = client.get("/operator")
        assert page.status_code == 200
        assert "Connected applications" in page.text
        csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
        form = {
            "appId": "dashboard-app",
            "displayName": "Dashboard app",
            "callbackUrl": "http://callback.test/actions",
        }
        assert client.post("/operator/apps", data=form).status_code == 403
        created = client.post("/operator/apps", data={**form, "csrf": csrf})
        assert created.status_code == 200
        assert "APP_TOKEN=" in created.text
        saved = client.post(
            "/operator/apps/dashboard-app/personalities",
            data={
                "personalityId": "friendly",
                "name": "Friendly",
                "prompt": "Keep it warm and brief.",
                "csrf": csrf,
            },
            follow_redirects=False,
        )
        assert saved.status_code == 303
        assert app.state.repo.personalities("dashboard-app")[0]["name"] == "Friendly"
        app.state.repo.save_session(
            "dashboard-app",
            SessionRegistration.model_validate(session("dashboard-session", "private")),
        )
        app_page = client.get("/operator/apps/dashboard-app")
        assert "Created " in app_page.text
        assert "Unnamed group" in app_page.text
        assert "button-danger" in app_page.text
        assert 'aria-label="Back to Applications"' in app_page.text
        group_page = client.get(
            "/operator/apps/dashboard-app/groups/group-a"
        )
        assert (
            'aria-label="Back to dashboard-app"'
            in group_page.text
        )
        renamed = client.post(
            "/operator/apps/dashboard-app/groups/group-a/metadata",
            data={"displayName": "Friday pilot", "csrf": csrf},
            follow_redirects=False,
        )
        assert renamed.status_code == 303
        assert app.state.repo.get_group("dashboard-app", "group-a")[
            "display_name"
        ] == "Friday pilot"
        app.state.repo.save_group_policy(
            "dashboard-app",
            GroupPolicy(
                groupId="group-a",
                personalityAssignments={"ai-1": "friendly"},
            ),
        )
        deleted = client.post(
            "/operator/apps/dashboard-app/personalities/friendly/delete",
            data={"csrf": csrf},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        assert app.state.repo.personalities("dashboard-app") == []
        assert (
            app.state.repo.group_policy(
                "dashboard-app", "group-a"
            ).personality_assignments
            == {}
        )
        removed = client.post(
            "/operator/apps/dashboard-app/groups/group-a/remove",
            data={"csrf": csrf},
            follow_redirects=False,
        )
        assert removed.status_code == 303
        assert app.state.repo.list_groups("dashboard-app") == []
        assert app.state.repo.get_session("dashboard-app", "dashboard-session")


def test_message_event_requires_text() -> None:
    body = event("e1", "s1")
    body["text"] = None
    with pytest.raises(ValidationError):
        RuntimeEvent.model_validate(body)


def test_intro_channel_hides_study_context_and_removes_name_prefix(tmp_path) -> None:
    body = session("intro-session", "private-study")
    body["channels"] = ["intro"]
    body["sharedContext"] = {"study": "shared-study"}
    body["channelContext"] = {
        "intro": {
            "instructions": "Only greet the group.",
            "includeSharedContext": False,
            "includePrivateContext": False,
        }
    }
    registration = SessionRegistration.model_validate(body)
    repo = Repository(str(tmp_path / "intro.db"))
    repo.initialize()
    orchestrator = Orchestrator(repo, None)
    prompt = orchestrator._prompt(
        "study-app",
        registration,
        registration.participants[1],
        "Be conversational.",
        None,
        "intro",
    )
    assert "Only greet the group." in prompt
    assert "shared-study" not in prompt
    assert "private-study" not in prompt
    assert (
        orchestrator._normalize(
            "ai-1: Peer: Hey everyone! Study details come later.",
            24,
            ["ai-1", "Peer"],
        )
        == "Hey everyone!"
    )
    repo.add_message(
        "study-app",
        registration.session_id,
        "intro",
        "human-1",
        "user",
        "Hello",
    )
    repo.add_message(
        "study-app",
        registration.session_id,
        "intro",
        "ai-1",
        "assistant",
        "Hey!",
    )
    transcript = orchestrator._transcript(
        "study-app",
        registration,
        "intro",
        10,
        "ai-1",
    )
    assert transcript == [
        {"role": "user", "content": "Human: Hello"},
        {"role": "assistant", "content": "Peer: Hey!"},
    ]
    assert "ai-1" not in json.dumps(transcript)
