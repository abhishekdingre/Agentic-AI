from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "local"

    anthropic_api_key: str = ""
    xai_api_key: str = ""
    claude_model: str = "claude-opus-5"
    anthropic_effort: str = "medium"
    grok_model: str = "grok-4.7"
    xai_reasoning_effort: str = "high"
    xai_base_url: str = "https://api.x.ai/v1"

    database_url: str = "postgresql+asyncpg://banner:banner@postgres:5432/banner_copilot"
    db_pool_size: int = 10
    db_max_overflow: int = 20

    redis_url: str = "redis://redis:6379/0"

    fhir_base_url: str = "http://mock-fhir:8000"
    fhir_timeout_seconds: float = 2.0
    fhir_max_retries: int = 1
    fhir_breaker_failure_threshold: int = 3
    fhir_breaker_reset_seconds: float = 15.0
    fhir_cache_ttl_seconds: int = 300

    jwt_secret: str = "local-dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    minio_endpoint: str = "minio:9000"
    minio_access_key: str = "banner_minio"
    minio_secret_key: str = "banner_minio_secret"
    minio_bucket: str = "signed-notes"
    minio_secure: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
