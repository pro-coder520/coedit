from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+aiosqlite:///./coedit.db"
    redis_url: str | None = None
    snapshot_interval: int = 100

    model_config = SettingsConfigDict(env_prefix="")