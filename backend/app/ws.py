from __future__ import annotations

import json
from typing import Any

from fastapi import WebSocket


class MarketFeedHub:
    def __init__(self) -> None:
        self.subscriptions: dict[WebSocket, set[str]] = {}

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.subscriptions[ws] = {"NIFTY 50", "NIFTY BANK"}

    def disconnect(self, ws: WebSocket) -> None:
        self.subscriptions.pop(ws, None)

    def update_subscription(self, ws: WebSocket, action: str, tokens: list[str]) -> list[str]:
        if action not in {"subscribe", "unsubscribe", "replace"}:
            raise ValueError("Unsupported subscription action")
        if not isinstance(tokens, list) or len(tokens) > 200:
            raise ValueError("A maximum of 200 subscription tokens is allowed")
        if any(not isinstance(token, str) or not token.strip() or len(token) > 100 for token in tokens):
            raise ValueError("Invalid subscription token")
        normalized = list(dict.fromkeys(token.strip() for token in tokens))
        current = self.subscriptions.setdefault(ws, set())
        if action == "subscribe":
            updated = current | set(normalized)
        elif action == "unsubscribe":
            updated = current - set(normalized)
        else:
            updated = set(normalized)
        if len(updated) > 200:
            raise ValueError("A maximum of 200 active subscriptions is allowed")
        self.subscriptions[ws] = updated
        return sorted(updated)

    async def broadcast_ticks(self, quotes_by_token: dict[str, dict[str, Any]]) -> None:
        dead: list[WebSocket] = []
        for ws, subs in list(self.subscriptions.items()):
            ticks = [quotes_by_token[token] for token in subs if token in quotes_by_token]
            if not ticks:
                continue
            try:
                await ws.send_text(json.dumps({"type": "ticks", "data": ticks}))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


class OrderFeedHub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def broadcast(self, event_type: str, data: Any) -> None:
        dead: list[WebSocket] = []
        message = json.dumps({"type": event_type, "data": data})
        for ws in list(self.clients):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
