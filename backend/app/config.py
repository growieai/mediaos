from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    app_env: str = "development"
    app_name: str = "Growie Media OS"
    openai_api_key: str | None = None
    openai_model_editor: str = "gpt-5.6-terra"
    openai_model_research: str = "gpt-5.6-terra"
    openai_model_creator: str = "gpt-5.6-terra"
    openai_model_qa: str = "gpt-5.6-sol"
    ai_mock_mode: bool = True
    enable_external_creators: bool = False
    enable_auto_publish: bool = False
    enable_auto_replies: bool = False
    enable_video: bool = False

    @property
    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]


@lru_cache
def get_settings() -> Settings:
    return Settings()
