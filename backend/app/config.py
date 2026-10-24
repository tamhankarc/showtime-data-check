"""Runtime configuration. Provider credentials must never reach a browser."""
from dataclasses import dataclass, field
import json
import os


def env(name, default=""):
    value = os.getenv(name, default)
    if isinstance(value, str) and value.startswith('"'):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: env("DATABASE_URL", "sqlite:///./data-check.sqlite"))
    environment: str = field(default_factory=lambda: env("ENVIRONMENT", "development"))
    app_origin: str = field(default_factory=lambda: env("APP_ORIGIN", "http://localhost:3001").rstrip("/"))
    proxy_secret: str = field(default_factory=lambda: env("PROXY_SECRET", ""))
    admin_username: str = field(default_factory=lambda: env("ADMIN_USERNAME", "administrator"))
    admin_password_hash: str = field(default_factory=lambda: env("ADMIN_PASSWORD_HASH", ""))
    viewer_username: str = field(default_factory=lambda: env("VIEWER_USERNAME", "viewer"))
    viewer_password_hash: str = field(default_factory=lambda: env("VIEWER_PASSWORD_HASH", ""))
    is_api_key: str = field(default_factory=lambda: env("IS_API_KEY", ""))
    velox_username: str = field(default_factory=lambda: env("VELOX_USERNAME", ""))
    velox_password: str = field(default_factory=lambda: env("VELOX_PASSWORD", ""))
    ingresso_partnerships: list[str] = field(default_factory=lambda: [x.strip() for x in env("INGRESSO_PARTNERSHIPS", "warner,sony").split(",") if x.strip()])
    cinemark_relay_url: str = field(default_factory=lambda: env("CINEMARK_RELAY_URL", ""))
    cinemark_relay_secret: str = field(default_factory=lambda: env("CINEMARK_RELAY_SECRET", ""))
    request_timeout: int = 120
    provider_concurrency: int = 2
    velox_concurrency: int = 6
    session_hours: int = 12
    snapshot_seconds: int = 900
    max_snapshots: int = 8
    max_snapshot_rows: int = 250000

    @property
    def production(self):
        return self.environment == "production"

    def validate(self):
        if self.admin_username == self.viewer_username:
            raise ValueError("The two shared login usernames must differ.")
        if not self.app_origin.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
            raise ValueError("APP_ORIGIN must be an HTTPS origin (or local development origin).")
        if self.production and (len(self.proxy_secret) < 32 or not self.admin_password_hash or not self.viewer_password_hash):
            raise ValueError("Production requires PROXY_SECRET and both password hashes.")
        if not 1 <= self.snapshot_seconds <= 900:
            raise ValueError("Snapshot lifetime must not exceed 15 minutes.")
