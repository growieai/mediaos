from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")
    app_env: str = "development"
    app_name: str = "Growie Media OS"
    database_url: SecretStr
    ai_mock_mode: bool = True
    enable_external_creators: bool = False
    enable_auto_publish: bool = False
    enable_auto_replies: bool = False
    enable_video: bool = False
    max_request_bytes: int = 262144
    max_skill_attempts: int = Field(default=3, ge=1, le=5)
    intelligence_tokens: SecretStr | None = None
    asset_storage_path: Path = REPO_ROOT / ".local" / "renders"

    @model_validator(mode="after")
    def milestone_scope(self):
        url = make_url(self.database_url.get_secret_value())
        if url.drivername != "postgresql+psycopg" or url.username != "mediaos_runtime":
            raise ValueError("Runtime must use its restricted PostgreSQL role")
        if not self.ai_mock_mode or any(
            (
                self.enable_external_creators,
                self.enable_auto_publish,
                self.enable_auto_replies,
                self.enable_video,
            )
        ):
            raise ValueError("Milestone 1 supports only internal deterministic carousel workflows")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate({})
