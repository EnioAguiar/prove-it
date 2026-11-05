from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    vultr_api_key: str
    vultr_model: str
    vultr_base_url: str = "https://api.vultrinference.com/v1"
    sandbox_image: str = "proveit-runner:py312"
    sandbox_runtime: str = "runc"
    trust_proxy: bool = False  # behind Caddy: client IP = last X-Forwarded-For entry
    job_root: str | None = None  # host-visible dir for sandbox job dirs (deploy)
    signing_key: str | None = None  # base64 Ed25519 seed; `python -m app.signing` makes one


@lru_cache
def get_settings() -> Settings:
    return Settings()
