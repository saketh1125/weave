from dotenv import load_dotenv

load_dotenv()

from pydantic import Field
from pydantic_settings import BaseSettings
from urllib.parse import urlsplit


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://sentra:sentra@localhost:5432/sentra"
    mistral_api_key: str = ""
    mistral_api_key2: str = ""
    documentation_model: str = Field(
        default="mistral-large-4", min_length=1, max_length=200, validation_alias="SENTRA_DOCUMENTATION_MODEL"
    )
    sentra_gh_token: str = ""
    sentra_git_allowed_hosts: str = "github.com"
    sentra_repo_max_file_bytes: int = Field(default=1_000_000, ge=1)
    sentra_repo_max_total_bytes: int = Field(default=50_000_000, ge=1)
    sentra_repo_max_files: int = Field(default=10000, ge=1)
    sentra_embed_batch_size: int = Field(default=8, ge=1, le=64)
    sentra_scan_jobs: int = Field(default=2, ge=1, le=8)
    sentra_repo_max_chunks: int = Field(default=20000, ge=1)
    sentra_access_token: str = ""
    sentra_cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    session_ttl_seconds: int = Field(default=3600, ge=60, le=86400)

    @property
    def cors_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.sentra_cors_origins.split(",") if origin.strip()]
        if not origins:
            raise ValueError("SENTRA_CORS_ORIGINS must contain explicit origins and cannot contain '*'")
        for origin in origins:
            parsed = urlsplit(origin)
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("SENTRA_CORS_ORIGINS contains an invalid port") from exc
            if (
                origin == "null" or "*" in origin
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment
            ):
                raise ValueError("SENTRA_CORS_ORIGINS must contain only exact http(s) origins")
        return origins

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
