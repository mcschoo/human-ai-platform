"""Shared configuration for opt-in live compatibility tests."""

from __future__ import annotations

import os

import pytest
from openai import AsyncOpenAI


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if _enabled(os.getenv("HAI_LIVE_TESTS")):
        return
    skip = pytest.mark.skip(reason="set HAI_LIVE_TESTS=1 to run live server tests")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.getenv("OPENAI_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/")


@pytest.fixture(scope="session")
def api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "local-test-key")


@pytest.fixture(scope="session")
def model() -> str:
    value = os.getenv("OPENAI_MODEL")
    if not value:
        pytest.skip("set OPENAI_MODEL when running live tests")
    return value


@pytest.fixture
async def openai_client(base_url: str, api_key: str):
    async with AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=float(os.getenv("HAI_TEST_TIMEOUT", "60")),
        max_retries=0,
    ) as client:
        yield client
