from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_REVISION = "0015"


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
    media_storage_path: Path = REPO_ROOT / ".local" / "media"
    media_live_enabled: bool = False
    elevenlabs_api_key: SecretStr | None = None
    heygen_api_key: SecretStr | None = None
    hf_api_key_id: SecretStr | None = None
    hf_api_key_secret: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    social_connect_enabled: bool = False
    social_publish_enabled: bool = False
    social_reply_enabled: bool = False
    social_app_id: str | None = None
    social_app_secret: SecretStr | None = None
    social_api_version: str | None = None
    social_redirect_uri: str | None = None
    social_public_base_url: str | None = None
    social_vault_key: SecretStr | None = None
    social_webhook_verify_token: SecretStr | None = None
    social_service_tokens: SecretStr | None = None
    social_storage_path: Path = REPO_ROOT / ".local" / "social"

    @model_validator(mode="after")
    def milestone_scope(self):
        url = make_url(self.database_url.get_secret_value())
        if url.drivername != "postgresql+psycopg" or url.username != "mediaos_runtime":
            raise ValueError("Runtime must use its restricted PostgreSQL role")
        if not self.ai_mock_mode and not (
            self.openai_api_key and self.openai_api_key.get_secret_value()
        ):
            raise ValueError("Real text mode requires a configured OpenAI key and tenant policy")
        if any(
            (
                self.enable_external_creators,
                self.enable_auto_publish,
                self.enable_auto_replies,
                self.enable_video,
            )
        ):
            raise ValueError(
                "External creator and automatic publishing/reply feature gates must remain off"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings.model_validate({})
