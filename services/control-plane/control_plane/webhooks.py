import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import httpx

from .models import CallbackAction
from .repository import Repository
from .settings import Settings


# Input: Exact callback bytes, Unix timestamp, and app webhook secret.
# Output: Hex HMAC-SHA256 signature over timestamp and body.
def sign_webhook(body: bytes, timestamp: str, secret: str) -> str:
    signed = timestamp.encode() + b"." + body
    return hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


class WebhookWorker:
    def __init__(
        self,
        repo: Repository,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.repo = repo
        self.settings = settings
        self.transport = transport
        self.running = False

    async def run(self) -> None:
        self.running = True
        while self.running:
            await self.deliver_due()
            await asyncio.sleep(0.25)

    def stop(self) -> None:
        self.running = False

    # Input: Pending persisted actions whose due times have passed.
    # Output: Count attempted; failures remain persisted for bounded retry.
    async def deliver_due(self) -> int:
        rows = self.repo.due_deliveries()
        for row in rows:
            await self._deliver(row)
        return len(rows)

    async def _deliver(self, row: dict) -> None:
        action = CallbackAction.model_validate_json(row["action"])
        target = self.repo.webhook_target(action.app_id)
        if not target:
            self.repo.finish_delivery(action.action_id, False, "app missing")
            return
        url, secret = target
        body = json.dumps(
            action.model_dump(mode="json", by_alias=True),
            separators=(",", ":"),
        ).encode()
        timestamp = str(int(datetime.now(timezone.utc).timestamp()))
        headers = {
            "Content-Type": "application/json",
            "X-Control-Plane-Timestamp": timestamp,
            "X-Control-Plane-Signature": f"sha256={sign_webhook(body, timestamp, secret)}",
            "Idempotency-Key": action.action_id,
        }
        success = False
        status_code = None
        error = None
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.webhook_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post(url, content=body, headers=headers)
                status_code = response.status_code
                success = 200 <= response.status_code < 300
                if not success:
                    error = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            error = str(exc)
        self.repo.record_webhook_attempt(action.action_id, success, status_code, error)
        if success:
            self.repo.finish_delivery(action.action_id, True)
            return
        attempts = int(row["attempts"]) + 1
        if attempts >= self.settings.max_webhook_attempts:
            self.repo.finish_delivery(action.action_id, False, error)
            return
        backoff = min(300, 2**attempts)
        due = datetime.now(timezone.utc) + timedelta(seconds=backoff)
        self.repo.retry_delivery(action.action_id, due, error or "delivery failed")
