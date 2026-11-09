from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    telegram_bot_token: SecretStr
    openai_api_key: SecretStr
    owner_id: int = Field(gt=0)
    text_model: str = "gpt-5-mini"
    audio_model: str = "gpt-audio-1.5"
    voice: str = "alloy"
    ffmpeg_path: str = "ffmpeg"
    database_path: Path = Path("data/mentor.sqlite3")
    max_voice_seconds: int = Field(default=90, ge=5, le=300)
    max_text_chars: int = Field(default=4000, ge=100, le=8000)
    daily_turn_limit: int = Field(default=40, ge=1, le=500)

    @field_validator("telegram_bot_token", "openai_api_key")
    @classmethod
    def nonempty_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Secret must not be empty")
        return value
