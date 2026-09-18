import asyncio
import csv
import io
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse

from .auth import app_required, operator_api_required
from .dashboard import router as dashboard_router
from .inference import ResponsesClient
from .models import (
    AppRegistration,
    EventKind,
    EventReceipt,
    GroupPolicy,
    Health,
    PersonalityTemplate,
    RuntimeEvent,
    SessionRegistration,
    StoredApp,
)
from .orchestration import Orchestrator
from .repository import Repository
from .settings import Settings
from .webhooks import WebhookWorker


# Input: Optional settings and HTTP transports for production or isolated tests.
# Output: A configured FastAPI service with persistent workers.
def create_app(
    settings: Settings | None = None,
    inference_transport=None,
    webhook_transport=None,
    start_worker: bool = True,
) -> FastAPI:
    settings = settings or Settings.from_env()
    os.makedirs(os.path.dirname(settings.database_path) or ".", exist_ok=True)
    repo = Repository(settings.database_path)
    repo.initialize()
    inference = ResponsesClient(
        settings.vllm_base_url,
        settings.vllm_api_key,
        settings.model,
        inference_transport,
    )
    worker = WebhookWorker(repo, settings, webhook_transport)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(worker.run()) if start_worker else None
        yield
        worker.stop()
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(
        title="Human AI Control Plane",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.repo = repo
    app.state.orchestrator = Orchestrator(repo, inference)
    app.state.worker = worker
    app.include_router(dashboard_router)

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health()

    @app.post(
        "/v1/apps",
        response_model=StoredApp,
        dependencies=[Depends(operator_api_required)],
    )
    def register_app(body: AppRegistration) -> StoredApp:
        repo.save_app(body)
        saved = repo.get_app(body.app_id)
        assert saved
        return saved

    @app.get(
        "/v1/apps",
        dependencies=[Depends(operator_api_required)],
    )
    def list_apps() -> list[dict]:
        return repo.list_apps()

    @app.post(
        "/v1/apps/{app_id}/sessions",
        status_code=201,
        dependencies=[Depends(app_required)],
    )
    def register_session(app_id: str, body: SessionRegistration) -> dict:
        repo.save_session(app_id, body)
        return {"appId": app_id, "sessionId": body.session_id, "registered": True}

    @app.post(
        "/v1/apps/{app_id}/events",
        response_model=EventReceipt,
        dependencies=[Depends(app_required)],
    )
    async def ingest_event(app_id: str, body: RuntimeEvent) -> EventReceipt:
        session = repo.get_session(app_id, body.session_id)
        if not session:
            raise HTTPException(404, "session not registered")
        if body.channel not in session.channels:
            raise HTTPException(422, "channel not registered")
        inserted = repo.record_event(app_id, body)
        if not inserted:
            return EventReceipt(eventId=body.event_id, accepted=True, duplicate=True)
        if body.type == EventKind.session_started:
            repo.set_session_state(app_id, body.session_id, "active")
        if body.type == EventKind.session_ended:
            repo.set_session_state(app_id, body.session_id, "ended")
        scheduled = await app.state.orchestrator.handle_message(app_id, session, body)
        repo.audit(
            app_id,
            body.session_id,
            "event.accepted",
            {"eventId": body.event_id, "type": body.type.value},
        )
        return EventReceipt(
            eventId=body.event_id,
            accepted=True,
            scheduledActions=scheduled,
        )

    @app.put(
        "/v1/operator/apps/{app_id}/personalities/{personality_id}",
        dependencies=[Depends(operator_api_required)],
    )
    def put_personality(
        app_id: str, personality_id: str, body: PersonalityTemplate
    ) -> PersonalityTemplate:
        if personality_id != body.personality_id:
            raise HTTPException(422, "personality ID mismatch")
        if not repo.get_app(app_id):
            raise HTTPException(404, "app not found")
        repo.save_personality(app_id, body)
        return body

    @app.put(
        "/v1/operator/apps/{app_id}/groups/{group_id}/policy",
        dependencies=[Depends(operator_api_required)],
    )
    def put_group_policy(app_id: str, group_id: str, body: GroupPolicy) -> GroupPolicy:
        if group_id != body.group_id:
            raise HTTPException(422, "group ID mismatch")
        repo.save_group_policy(app_id, body)
        return body

    @app.get(
        "/v1/operator/apps/{app_id}/sessions/{session_id}",
        dependencies=[Depends(operator_api_required)],
    )
    def operator_session(app_id: str, session_id: str) -> dict:
        session = repo.get_session(app_id, session_id)
        if not session:
            raise HTTPException(404, "session not found")
        return {
            "registration": session.model_dump(mode="json", by_alias=True),
            "transcript": repo.transcript(app_id, session_id, limit=500),
            "audit": repo.audit_log(app_id, session_id),
        }

    @app.get(
        "/v1/operator/apps/{app_id}/webhooks",
        dependencies=[Depends(operator_api_required)],
    )
    def webhook_health(app_id: str) -> list[dict]:
        return repo.webhook_health(app_id)

    @app.get(
        "/v1/operator/apps/{app_id}/export",
        response_class=PlainTextResponse,
        dependencies=[Depends(operator_api_required)],
    )
    def export_transcript(
        app_id: str,
        session_id: str = Query(alias="sessionId"),
        channel: str | None = None,
    ) -> Response:
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "channel",
                "sender_id",
                "role",
                "text",
                "event_id",
                "created_at",
            ],
        )
        writer.writeheader()
        writer.writerows(repo.transcript(app_id, session_id, channel, 100_000))
        headers = {
            "Content-Disposition": f'attachment; filename="{app_id}-{session_id}.csv"'
        }
        return PlainTextResponse(output.getvalue(), headers=headers)

    return app
