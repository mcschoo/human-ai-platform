import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str = "control-plane.db"
    operator_password: str = "change-me"
    cookie_secret: str = "change-me-cookie"
    operator_bind: str = "127.0.0.1"
    vllm_base_url: str = "http://vllm:8000/v1"
    vllm_api_key: str = ""
    model: str = "qwen3.5-9b"
    webhook_timeout_seconds: float = 10.0
    max_webhook_attempts: int = 5
    context_messages: int = 40

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=os.getenv("CONTROL_PLANE_DB", cls.database_path),
            operator_password=os.getenv(
                "CONTROL_PLANE_OPERATOR_PASSWORD", cls.operator_password
            ),
            cookie_secret=os.getenv("CONTROL_PLANE_COOKIE_SECRET", cls.cookie_secret),
            operator_bind=os.getenv("CONTROL_PLANE_BIND_ADDRESS", cls.operator_bind),
            vllm_base_url=os.getenv("CONTROL_PLANE_VLLM_URL", cls.vllm_base_url),
            vllm_api_key=os.getenv("VLLM_API_KEY", ""),
            model=os.getenv("CONTROL_PLANE_MODEL", cls.model),
            webhook_timeout_seconds=float(
                os.getenv("CONTROL_PLANE_WEBHOOK_TIMEOUT", "10")
            ),
            max_webhook_attempts=int(os.getenv("CONTROL_PLANE_WEBHOOK_ATTEMPTS", "5")),
            context_messages=int(os.getenv("CONTROL_PLANE_CONTEXT_MESSAGES", "40")),
        )
