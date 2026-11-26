from __future__ import annotations

import time
import uuid
from typing import Any

from app.broker.base import BrokerAdapter
from app.db import Database
from app.models import AlertCreateRequest, AlertRecord, QuoteData


SCANNERS = [
    {"id": "top_gainers", "name": "Top Gainers", "description": "Instruments with strongest positive % change"},
    {"id": "top_losers", "name": "Top Losers", "description": "Instruments with sharpest negative % change"},
    {"id": "high_oi_options", "name": "High OI Active Options", "description": "F&O contracts with highest Open Interest"},
    {"id": "high_volume", "name": "Volume Shockers", "description": "Highest traded volume across segments"},
]


def run_scanner(broker: BrokerAdapter, scanner_id: str, segment: str | None = None) -> list[dict[str, Any]]:
    instruments = broker.list_instruments("", exchange_segment=segment)
    tokens = [i.instrument_token for i in instruments]
    quotes: dict[str, QuoteData] = {}
    for start in range(0, len(tokens), 200):
        quotes.update({q.instrument_token: q for q in broker.get_quotes(tokens[start : start + 200])})

    rows: list[dict[str, Any]] = []
    for inst in instruments:
        q = quotes.get(inst.instrument_token)
        if not q:
            continue
        rows.append(
            {
                "instrument_token": inst.instrument_token,
                "trading_symbol": inst.trading_symbol,
                "exchange_segment": inst.exchange_segment,
                "instrument_type": inst.instrument_type,
                "ltp": q.ltp,
                "change": q.change,
                "change_pct": q.change_pct,
                "volume": q.volume,
                "oi": q.oi,
            }
        )

    if scanner_id == "top_gainers":
        rows.sort(key=lambda r: r["change_pct"], reverse=True)
    elif scanner_id == "top_losers":
        rows.sort(key=lambda r: r["change_pct"])
    elif scanner_id == "high_oi_options":
        rows = [r for r in rows if r["instrument_type"] in ("CE", "PE")]
        rows.sort(key=lambda r: r["oi"], reverse=True)
    elif scanner_id == "high_volume":
        rows.sort(key=lambda r: r["volume"], reverse=True)

    return rows[:25]


def create_alert(db: Database, req: AlertCreateRequest) -> AlertRecord:
    alert = AlertRecord(
        id=f"alt_{uuid.uuid4().hex[:12]}",
        instrument_token=req.instrument_token,
        trading_symbol=req.trading_symbol,
        condition=req.condition,
        target_price=req.target_price,
        note=req.note,
        status="ACTIVE",
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        triggered_at=None,
    )
    return db.save_alert(alert)


def evaluate_alerts(db: Database, quotes_by_token: dict[str, QuoteData]) -> list[AlertRecord]:
    triggered: list[AlertRecord] = []
    previous_prices = db.get_json("alert_last_prices", {})
    current_prices: dict[str, float] = {}
    for alert in db.list_alerts():
        if alert.status != "ACTIVE":
            continue
        q = quotes_by_token.get(alert.instrument_token)
        if not q:
            continue
        previous = previous_prices.get(alert.id)
        hit = False
        if alert.condition == "ABOVE" and q.ltp >= alert.target_price:
            hit = True
        elif alert.condition == "BELOW" and q.ltp <= alert.target_price:
            hit = True
        elif alert.condition == "CROSS_ABOVE" and previous is not None and previous < alert.target_price <= q.ltp:
            hit = True
        elif alert.condition == "CROSS_BELOW" and previous is not None and previous > alert.target_price >= q.ltp:
            hit = True
        current_prices[alert.id] = q.ltp
        if hit:
            alert.status = "TRIGGERED"
            alert.triggered_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            db.save_alert(alert)
            triggered.append(alert)
    if current_prices:
        db.set_json("alert_last_prices", {**previous_prices, **current_prices})
    return triggered
