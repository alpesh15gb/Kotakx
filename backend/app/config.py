from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


PREFIX = "KOTAK_TERMINAL_"


def _load_env() -> None:
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]
    for path in candidates:
        if path.is_file():
            load_dotenv(path, override=False)


def _int(name: str, default: int) -> int:
    return int(os.getenv(PREFIX + name, str(default)))


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(PREFIX + name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(PREFIX + name)
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in raw.split(",") if item.strip()]
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"{PREFIX + name} must be a JSON array of strings")
    return parsed


@dataclass(frozen=True)
class Settings:
    app_name: str = "Sahi-Style Kotak Neo Trading Terminal API"
    environment: str = "development"
    db_path: str = "data/trading_terminal.db"
    default_mode: str = "paper"
    kotak_environment: str = "prod"
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:3001", "http://127.0.0.1:3001"]
    )
    tick_interval_ms: int = 750
    pnl_check_interval_ms: int = 1000
    public_static_ip: str = ""
    access_password: str = ""
    session_secret: str = ""
    session_ttl_seconds: int = 43200
    cookie_secure: bool = False
    login_rate_limit: int = 5
    login_rate_window_seconds: int = 60
    log_level: str = "INFO"

    def validate(self) -> None:
        if self.tick_interval_ms < 100:
            raise ValueError("tick_interval_ms must be at least 100")
        if self.pnl_check_interval_ms < 250:
            raise ValueError("pnl_check_interval_ms must be at least 250")
        if self.session_ttl_seconds < 300:
            raise ValueError("session_ttl_seconds must be at least 300")
        if self.login_rate_limit < 1:
            raise ValueError("login_rate_limit must be at least 1")
        if self.environment == "production":
            missing = [
                name
                for name, value in (
                    ("KOTAK_TERMINAL_ACCESS_PASSWORD", self.access_password),
                    ("KOTAK_TERMINAL_SESSION_SECRET", self.session_secret),
                )
                if not value
            ]
            if missing:
                raise ValueError("Required production settings missing: " + ", ".join(missing))
            if len(self.session_secret) < 32:
                raise ValueError("KOTAK_TERMINAL_SESSION_SECRET must contain at least 32 characters")
            if not self.cookie_secure:
                raise ValueError("KOTAK_TERMINAL_COOKIE_SECURE must be true in production")
            if not self.cors_origins:
                raise ValueError("At least one exact HTTPS CORS origin is required in production")
            for origin in self.cors_origins:
                parsed = urlparse(origin)
                if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
                    raise ValueError("Production CORS origins must be exact HTTPS origins without paths")


_load_env()
settings = Settings(
    app_name=os.getenv(PREFIX + "APP_NAME", "Sahi-Style Kotak Neo Trading Terminal API"),
    environment=os.getenv(PREFIX + "ENVIRONMENT", "development").strip().lower(),
    db_path=os.getenv(PREFIX + "DB_PATH", "data/trading_terminal.db"),
    default_mode=os.getenv(PREFIX + "DEFAULT_MODE", "paper").strip().lower(),
    kotak_environment=os.getenv(PREFIX + "KOTAK_ENVIRONMENT", "prod").strip().lower(),
    cors_origins=_list("CORS_ORIGINS", ["http://localhost:3001", "http://127.0.0.1:3001"]),
    tick_interval_ms=_int("TICK_INTERVAL_MS", 750),
    pnl_check_interval_ms=_int("PNL_CHECK_INTERVAL_MS", 1000),
    public_static_ip=os.getenv(PREFIX + "PUBLIC_STATIC_IP", ""),
    access_password=os.getenv(PREFIX + "ACCESS_PASSWORD", ""),
    session_secret=os.getenv(PREFIX + "SESSION_SECRET", ""),
    session_ttl_seconds=_int("SESSION_TTL_SECONDS", 43200),
    cookie_secure=_bool("COOKIE_SECURE", False),
    login_rate_limit=_int("LOGIN_RATE_LIMIT", 5),
    login_rate_window_seconds=_int("LOGIN_RATE_WINDOW_SECONDS", 60),
    log_level=os.getenv(PREFIX + "LOG_LEVEL", "INFO").upper(),
)
settings.validate()
