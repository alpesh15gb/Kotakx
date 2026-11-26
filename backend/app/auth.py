from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Request, Response, WebSocket

from app.config import Settings, settings


SESSION_COOKIE = "kotak_terminal_session"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def auth_configured(config: Settings = settings) -> bool:
    return bool(config.access_password and config.session_secret)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session_token(config: Settings = settings) -> str:
    payload = _b64encode(
        json.dumps(
            {"exp": int(time.time()) + config.session_ttl_seconds, "nonce": secrets.token_urlsafe(18)},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = _b64encode(hmac.new(config.session_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_session_token(token: str | None, config: Settings = settings) -> bool:
    if not token or not config.session_secret:
        return False
    try:
        payload, signature = token.split(".", 1)
        expected = _b64encode(
            hmac.new(config.session_secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return False
        data = json.loads(_b64decode(payload))
        return isinstance(data, dict) and isinstance(data.get("exp"), int) and data["exp"] > int(time.time())
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def password_matches(candidate: str, config: Settings = settings) -> bool:
    if not config.access_password:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), config.access_password.encode("utf-8"))


def set_session_cookie(response: Response, token: str, config: Settings = settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=config.session_ttl_seconds,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, config: Settings = settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        path="/",
    )


def request_authenticated(request: Request, config: Settings = settings) -> bool:
    return verify_session_token(request.cookies.get(SESSION_COOKIE), config)


def origin_allowed(request: Request, config: Settings = settings) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    return origin.rstrip("/") in {item.rstrip("/") for item in config.cors_origins}


def require_websocket_session(ws: WebSocket, config: Settings = settings) -> bool:
    if not auth_configured(config):
        origin = ws.headers.get("origin")
        return not origin or origin.rstrip("/") in {item.rstrip("/") for item in config.cors_origins}
    if not verify_session_token(ws.cookies.get(SESSION_COOKIE), config):
        return False
    origin = ws.headers.get("origin")
    if not origin:
        return True
    return origin.rstrip("/") in {item.rstrip("/") for item in config.cors_origins}


class LoginRateLimiter:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and now - attempts[0] >= self.window_seconds:
                attempts.popleft()
            return len(attempts) < self.limit

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            self._attempts[key].append(now)
            stale = [ip for ip, values in self._attempts.items() if not values or now - values[-1] >= self.window_seconds * 10]
            for ip in stale:
                self._attempts.pop(ip, None)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


def error_payload(detail: str, code: str) -> dict[str, Any]:
    return {"detail": detail, "code": code}
