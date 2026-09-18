import asyncio
import os

import pytest
from control_plane.inference import ResponsesClient
from control_plane.models import ModelPolicy


@pytest.mark.live
def test_live_responses_api_store_false() -> None:
    if os.getenv("CONTROL_PLANE_LIVE_TESTS") != "1":
        pytest.skip("set CONTROL_PLANE_LIVE_TESTS=1 to run")
    client = ResponsesClient(
        os.getenv("CONTROL_PLANE_VLLM_URL", "http://127.0.0.1:8000/v1"),
        os.environ["VLLM_API_KEY"],
        os.getenv("CONTROL_PLANE_MODEL", "qwen3.5-9b"),
    )
    text = asyncio.run(
        client.respond(
            "Reply with exactly: ready",
            [{"role": "user", "content": "Are you ready?"}],
            ModelPolicy(maxOutputTokens=16, temperature=0),
        )
    )
    assert text.strip().lower() == "ready"
