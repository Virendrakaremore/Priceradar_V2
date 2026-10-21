"""Environment-driven configuration. No secrets are ever hardcoded here."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal

try:  # .env is optional and only read locally; never logged.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///priceradar.db"))
    cors_origins: list[str] = field(default_factory=lambda: _list("CORS_ORIGINS", "http://localhost:3000"))
    api_token: str = field(default_factory=lambda: os.environ.get("API_TOKEN", ""))
    default_user_email: str = field(default_factory=lambda: os.environ.get("DEFAULT_USER_EMAIL", ""))
    max_content_length: int = field(default_factory=lambda: _int("MAX_CONTENT_LENGTH", 64 * 1024))
    rate_limit: str = field(default_factory=lambda: os.environ.get("RATE_LIMIT", "120 per minute"))

    # SMTP (legacy EMAIL_USER / EMAIL_PASS are still honoured so existing .env files keep working)
    smtp_host: str = field(default_factory=lambda: os.environ.get("SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: _int("SMTP_PORT", 587))
    smtp_username: str = field(default_factory=lambda: os.environ.get("SMTP_USERNAME") or os.environ.get("EMAIL_USER", ""))
    smtp_password: str = field(default_factory=lambda: os.environ.get("SMTP_PASSWORD") or os.environ.get("EMAIL_PASS", ""))
    smtp_use_tls: bool = field(default_factory=lambda: _bool("SMTP_USE_TLS", True))
    smtp_use_ssl: bool = field(default_factory=lambda: _bool("SMTP_USE_SSL", False))
    smtp_timeout: int = field(default_factory=lambda: _int("SMTP_TIMEOUT", 20))
    email_from: str = field(default_factory=lambda: os.environ.get("EMAIL_FROM", ""))
    public_app_url: str = field(default_factory=lambda: os.environ.get("PUBLIC_APP_URL", "http://localhost:3000"))
    notification_max_attempts: int = field(default_factory=lambda: _int("NOTIFICATION_MAX_ATTEMPTS", 5))

    # Monitoring
    monitor_interval_s: int = field(default_factory=lambda: _int("MONITOR_INTERVAL_SECONDS", 15))
    # Floor applied per retailer. Defaults conservative; lower it ONLY where you have the right to.
    retailer_min_interval_s: int = field(default_factory=lambda: _int("RETAILER_MIN_INTERVAL_SECONDS", 60))
    worker_concurrency: int = field(default_factory=lambda: _int("WORKER_CONCURRENCY", 2))
    per_retailer_concurrency: int = field(default_factory=lambda: _int("PER_RETAILER_CONCURRENCY", 1))
    fetch_timeout_s: int = field(default_factory=lambda: _int("FETCH_TIMEOUT_SECONDS", 30))
    max_retries: int = field(default_factory=lambda: _int("MAX_RETRIES", 3))
    backoff_base_s: int = field(default_factory=lambda: _int("BACKOFF_BASE_SECONDS", 30))
    backoff_max_s: int = field(default_factory=lambda: _int("BACKOFF_MAX_SECONDS", 3600))
    circuit_threshold: int = field(default_factory=lambda: _int("CIRCUIT_BREAKER_THRESHOLD", 5))
    circuit_cooldown_s: int = field(default_factory=lambda: _int("CIRCUIT_BREAKER_COOLDOWN_SECONDS", 900))
    stale_after_multiplier: int = field(default_factory=lambda: _int("STALE_AFTER_MULTIPLIER", 4))
    heartbeat_sample_s: int = field(default_factory=lambda: _int("HEARTBEAT_SAMPLE_SECONDS", 900))
    big_jump_pct: Decimal = field(default_factory=lambda: Decimal(os.environ.get("BIG_JUMP_PERCENT", "40")))
    use_playwright: bool = field(default_factory=lambda: _bool("USE_PLAYWRIGHT", True))
    respect_robots: bool = field(default_factory=lambda: _bool("RESPECT_ROBOTS", True))
    check_dns: bool = field(default_factory=lambda: not _bool("DISABLE_DNS_CHECK", False))
    job_workers: int = field(default_factory=lambda: _int("JOB_WORKERS", 2))
    run_jobs_inline: bool = field(default_factory=lambda: _bool("RUN_JOBS_INLINE", False))

    # Discovery providers (optional)
    serpapi_key: str = field(default_factory=lambda: os.environ.get("SERPAPI_KEY", ""))

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_username and self.smtp_password and (self.email_from or self.smtp_username))

    @property
    def sender(self) -> str:
        return self.email_from or self.smtp_username


def get_settings() -> Settings:
    return Settings()
