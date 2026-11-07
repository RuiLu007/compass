from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    database_url: str = "postgresql+psycopg://compass:compass_local_only@127.0.0.1:55432/compass"
    compass_routing_jev_provider_id: UUID | None = None
    compass_routing_flash_provider_id: UUID | None = None
    compass_routing_pro_provider_id: UUID | None = None
    compass_routing_mode: str = "jev_with_flash"
    compass_model_max_concurrency: int = Field(default=8, ge=1, le=64)
    compass_model_max_inflight: int = Field(default=32, ge=1, le=256)
    compass_observability_enabled: bool = False
    compass_observability_sample_ratio: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)
    compass_observability_instance_id: str = Field(
        default="", max_length=128, pattern=r"^[a-zA-Z0-9_.:-]*$"
    )
    compass_observability_environment: Literal[
        "local", "test", "staging", "production", "showcase"
    ] = "local"
    compass_observability_capture_content: bool | None = None
    compass_observability_content_max_bytes: int = Field(default=16384, ge=1024, le=65536)
    compass_langfuse_base_url: str = ""
    compass_langfuse_public_key: str = ""
    compass_langfuse_secret_key: SecretStr = SecretStr("")
    compass_demo_enabled: bool = False
    compass_agent_service_token: str = ""
    compass_model_encryption_key: str = ""
    compass_model_allowed_origins: str = "https://api.deepseek.com"

    @property
    def capture_observability_content(self) -> bool:
        configured = self.compass_observability_capture_content
        return (
            self.compass_observability_environment == "local" if configured is None else configured
        )

    @model_validator(mode="after")
    def execution_limits(self):
        if self.compass_model_max_inflight < self.compass_model_max_concurrency:
            raise ValueError("inflight limit must cover execution capacity")
        if self.compass_langfuse_base_url:
            url = urlsplit(self.compass_langfuse_base_url)
            local = url.hostname in {"localhost", "127.0.0.1", "langfuse-web"}
            if (
                url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in {"", "/"}
                or not (url.scheme == "https" or url.scheme == "http" and local)
            ):
                raise ValueError("Invalid observability service URL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
