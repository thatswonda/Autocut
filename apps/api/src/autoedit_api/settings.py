"""Configuration, all from environment variables (prefix ``AUTOEDIT_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOEDIT_", env_file=".env", extra="ignore")

    env: Literal["dev", "test", "prod"] = "dev"
    database_url: str = "sqlite:///./data/autoedit.db"
    redis_url: str = "redis://localhost:6379/0"
    # "rq": background worker via Redis. "inline": run jobs in-process (tests).
    # "process": one job at a time in a short-lived child process, no Redis (single small server,
    # e.g. a Render instance); memory is fully released after every clip.
    queue_mode: Literal["rq", "inline", "process"] = "rq"

    storage_backend: Literal["local", "s3"] = "local"
    data_dir: Path = Path("./data")
    s3_bucket: str = "autoedit"
    s3_endpoint_url: str | None = None  # MinIO / Cloudflare R2; None for AWS
    s3_region: str = "auto"
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_server_side_encryption: bool = True

    max_upload_bytes: int = 8 * 1024**3
    max_chunk_bytes: int = 64 * 1024**2
    recommended_chunk_bytes: int = 8 * 1024**2
    stale_upload_hours: int = 48
    retention_days: int = 30

    # M1 has no real sign-in yet (M10). In dev the caller may pick a user with X-User-Id.
    dev_auth: bool = True
    # When set, every page and API call (except /healthz) asks for this password (HTTP Basic auth,
    # any username). Use it for a public test deployment before real sign-in exists (M10).
    access_password: str | None = None
    default_plan: Literal["free", "paid"] = "free"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    job_timeout_s: int = 3600
    job_retries: int = 3

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "storage"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"


@lru_cache
def get_settings() -> Settings:
    return Settings()
