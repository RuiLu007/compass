from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RetailRunScopeConfig(BaseModel):
    """One deployment-owned Web scope mapped to the Lesson 15 read-only source."""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True, frozen=True)

    tenant_id: UUID = Field(alias="tenantId")
    workspace_id: UUID = Field(alias="workspaceId")
    release: Literal["retail-dev-20261022b"]
    content_hash: str = Field(
        alias="contentHash",
        pattern=r"^7ae26405a71589c98ca8379bec1e1ec1ff77e16232ff2e1bf26b6d4501b8183e$",
    )
    role: str = Field(
        min_length=8,
        max_length=63,
        pattern=r"^[a-z_][a-z0-9_]{0,55}_a_east$",
    )
    retail_tenant_id: Literal[101] = Field(alias="retailTenantId")
    schema_name: Literal["core"] = Field(alias="schema")


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
    compass_retail_run_scope: str = ""
    compass_retail_run_readonly_dsn: SecretStr = SecretStr("")

    def retail_run_configuration(
        self,
    ) -> tuple[RetailRunScopeConfig | None, SecretStr | None, str | None]:
        """Parse the optional pair without making an unrelated Agent startup fail closed."""

        raw_scope = self.compass_retail_run_scope.strip()
        raw_dsn = self.compass_retail_run_readonly_dsn.get_secret_value().strip()
        if not raw_scope and not raw_dsn:
            return None, None, None
        if not raw_scope or not raw_dsn:
            return None, None, "partial_configuration"
        try:
            scope = RetailRunScopeConfig.model_validate_json(raw_scope)
        except ValidationError:
            return None, None, "invalid_scope"
        return scope, SecretStr(raw_dsn), None

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
