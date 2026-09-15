"""Minimal OpenAI SDK client for Human AI Platform."""

import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:8080/v1")
api_key = os.environ["OPENAI_API_KEY"]
model = os.getenv("OPENAI_MODEL", "qwen3.5-9b")

client = OpenAI(base_url=base_url, api_key=api_key)
response = client.responses.create(
    model=model,
    input="Reply with exactly: ready",
    max_output_tokens=16,
)

print(response.output_text)
