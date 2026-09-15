"""Opt-in integration tests for the platform's OpenAI-compatible API."""

from __future__ import annotations

import json
import os
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
from openai import AsyncOpenAI, AuthenticationError

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


async def test_chat_completions(openai_client: AsyncOpenAI, model: str) -> None:
    result = await openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with exactly: ready"}],
        temperature=0,
        max_tokens=16,
    )
    assert result.id
    assert result.choices
    assert result.choices[0].message.content


async def test_responses_api(openai_client: AsyncOpenAI, model: str) -> None:
    result = await openai_client.responses.create(
        model=model,
        input="Reply with exactly: ready",
        max_output_tokens=16,
    )
    assert result.id
    assert result.output_text


@pytest.mark.parametrize(
    ("instruction", "required"),
    [
        (
            "Return only JSON with MESSAGE set to ready and RATIONALE set to test.",
            {"MESSAGE", "RATIONALE"},
        ),
        (
            (
                "Return only JSON with DECISION set to WAIT, MESSAGE set to NONE, "
                "and RATIONALE set to test."
            ),
            {"DECISION", "MESSAGE", "RATIONALE"},
        ),
    ],
)
async def test_grail_responses_contract(
    base_url: str,
    api_key: str,
    model: str,
    instruction: str,
    required: set[str],
) -> None:
    request = {
        "model": model,
        "max_output_tokens": 64,
        "input": [
            {
                "role": "system",
                "content": instruction,
            },
            {"role": "user", "content": "Respond now."},
        ],
        "text": {"format": {"type": "json_object"}},
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=request,
        )
    response.raise_for_status()
    envelope = response.json()
    message = next(item for item in envelope["output"] if item["type"] == "message")
    output = next(
        item["text"] for item in message["content"] if item["type"] == "output_text"
    )
    assert required <= set(json.loads(output))


async def test_structured_json(openai_client: AsyncOpenAI, model: str) -> None:
    result = await openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Return status ready and code 200."}],
        temperature=0,
        max_tokens=64,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "status",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "code": {"type": "integer"},
                    },
                    "required": ["status", "code"],
                    "additionalProperties": False,
                },
            },
        },
    )
    payload = json.loads(result.choices[0].message.content or "")
    assert payload == {"status": "ready", "code": 200}


async def test_streaming_protocol(openai_client: AsyncOpenAI, model: str) -> None:
    stream = await openai_client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Count from one to five."}],
        max_tokens=40,
        stream=True,
    )
    chunks = 0
    text_parts: list[str] = []
    async for chunk in stream:
        chunks += 1
        assert chunk.id
        assert isinstance(chunk.choices, list)
        for choice in chunk.choices:
            content = choice.delta.content
            assert content is None or isinstance(content, str)
            if content:
                text_parts.append(content)
    assert chunks > 0
    assert "".join(text_parts).strip()


async def test_stream_can_be_cancelled(openai_client: AsyncOpenAI, model: str) -> None:
    stream = await openai_client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "Write a long story with many paragraphs."}
        ],
        max_tokens=512,
        stream=True,
    )
    async for chunk in stream:
        if any(choice.delta.content for choice in chunk.choices):
            break
    await stream.close()


async def test_auth_failure(base_url: str, model: str) -> None:
    async with AsyncOpenAI(
        base_url=base_url,
        api_key="intentionally-invalid-test-key",
        timeout=15,
        max_retries=0,
    ) as client:
        with pytest.raises(AuthenticationError):
            await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "hello"}],
                max_tokens=1,
            )


def _server_root(base_url: str) -> str:
    parsed = urlsplit(base_url)
    path = parsed.path.removesuffix("/v1").rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


async def test_health_endpoint(base_url: str) -> None:
    health_url = os.getenv("HAI_HEALTH_URL", "http://127.0.0.1:2019/health")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(health_url)
    assert response.status_code == 200


async def test_public_non_api_paths_are_blocked(base_url: str) -> None:
    server_root = _server_root(base_url)
    async with httpx.AsyncClient(timeout=10) as client:
        for path in ("/", "/health", "/metrics", "/invocations"):
            response = await client.get(f"{server_root}{path}")
            assert response.status_code == 404


async def test_oversized_request_is_rejected(
    base_url: str, api_key: str, model: str
) -> None:
    request = {
        "model": model,
        "input": "x" * (2 * 1024 * 1024),
        "max_output_tokens": 1,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=request,
        )
    assert response.status_code == 413


@pytest.mark.admin
async def test_metrics_endpoint() -> None:
    metrics_url = os.getenv("HAI_METRICS_URL", "http://127.0.0.1:2019/metrics")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(metrics_url)
    assert response.status_code == 200
    assert "vllm:" in response.text
