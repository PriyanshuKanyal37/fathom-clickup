from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./app.db"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-mini"
    clickup_workspace_id: str = ""
    clickup_list_id: str = ""
    clickup_date_field_id: str = ""
    encryption_key: str = ""
    admin_token: str = ""
    public_url: str = "http://localhost:8000"
    webhook_tolerance_seconds: int = 300


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

