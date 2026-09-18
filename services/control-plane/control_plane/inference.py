from typing import Any

import httpx

from .models import ModelPolicy


class ResponsesClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.url = f"{base_url.rstrip('/')}/responses"
        self.api_key = api_key
        self.model = model
        self.transport = transport

    # Input: A private system prompt, isolated transcript, and model policy.
    # Output: Normalized response text from the internal Responses API.
    async def respond(
        self, instructions: str, transcript: list[dict[str, str]], policy: ModelPolicy
    ) -> str:
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": transcript,
            "temperature": policy.temperature,
            "max_output_tokens": policy.max_output_tokens,
            "store": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=120, transport=self.transport) as client:
            response = await client.post(self.url, headers=headers, json=payload)
            response.raise_for_status()
        return self._output_text(response.json())

    @staticmethod
    def _output_text(body: dict[str, Any]) -> str:
        if body.get("output_text"):
            return str(body["output_text"]).strip()
        parts: list[str] = []
        for item in body.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    parts.append(str(content.get("text", "")))
        text = "\n".join(parts).strip()
        if not text:
            raise ValueError("inference response contained no text")
        return text
