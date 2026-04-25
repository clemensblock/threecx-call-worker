from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    threecx_base_url: str
    threecx_client_id: str
    threecx_client_secret: str
    threecx_monitored_extensions: str

    supabase_url: str
    supabase_service_role_key: str

    log_level: str = "INFO"
    reconnect_max_backoff: int = 60

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def monitored_extensions(self) -> list[str]:
        return [e.strip() for e in self.threecx_monitored_extensions.split(",") if e.strip()]

    @property
    def threecx_host(self) -> str:
        from urllib.parse import urlparse

        return urlparse(self.threecx_base_url).hostname or ""

    @property
    def ws_url(self) -> str:
        host = self.threecx_host
        return f"wss://{host}/callcontrol/ws"


settings = Settings()  # type: ignore[call-arg]
