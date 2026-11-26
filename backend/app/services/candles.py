from __future__ import annotations

import math
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import Database


TIMEFRAME_SECONDS = {
    "1m": 60,
    "2m": 120,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "1h": 3600,
    "1D": 86400,
}


class CandleService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_history(
        self,
        instrument_token: str,
        base_price: float,
        timeframe: str = "1m",
        count: int = 160,
        allow_synthetic: bool = False,
    ) -> list[dict[str, Any]]:
        if timeframe not in TIMEFRAME_SECONDS:
            raise ValueError("Unsupported candle timeframe")
        existing = self.db.get_candles(
            instrument_token,
            timeframe,
            limit=count,
            synthetic=True if allow_synthetic else False,
        )
        if len(existing) >= 40 or not allow_synthetic:
            return existing

        step = TIMEFRAME_SECONDS[timeframe]
        now_bucket = (int(time.time()) // step) * step
        seed = sum(ord(ch) for ch in instrument_token) + step
        rng = random.Random(seed)

        # Walk backward so the latest bar ends near base_price
        closes = [base_price]
        vol_scale = max(0.0008, 0.0022 * math.sqrt(step / 60.0))
        for _ in range(count - 1):
            prev = closes[-1] / (1.0 + rng.gauss(0.00005, vol_scale))
            closes.append(max(0.5, prev))
        closes.reverse()

        candles: list[dict[str, Any]] = []
        for idx, close in enumerate(closes):
            t = now_bucket - (count - 1 - idx) * step
            open_px = closes[idx - 1] if idx > 0 else close * (1.0 + rng.gauss(0, vol_scale * 0.4))
            spread = abs(close - open_px) + close * vol_scale * rng.uniform(0.3, 1.1)
            high = round(max(open_px, close) + spread * rng.uniform(0.2, 0.7), 2)
            low = round(max(0.05, min(open_px, close) - spread * rng.uniform(0.2, 0.7)), 2)
            candles.append(
                {
                    "time": t,
                    "open": round(open_px, 2),
                    "high": high,
                    "low": low,
                    "close": round(close, 2),
                    "volume": int(rng.uniform(4500, 95000)),
                }
            )
        self.db.upsert_candles(instrument_token, timeframe, candles, synthetic=True)
        return candles

    def update_with_tick(
        self,
        instrument_token: str,
        price: float,
        volume_delta: int = 0,
        synthetic: bool = False,
    ) -> dict[str, dict[str, Any]]:
        updated: dict[str, dict[str, Any]] = {}
        now = int(time.time())
        for tf, step in (("1m", 60), ("5m", 300), ("15m", 900)):
            bucket = (now // step) * step
            bars = self.db.get_candles(instrument_token, tf, limit=2, synthetic=synthetic)
            if bars and bars[-1]["time"] == bucket:
                bar = dict(bars[-1])
                bar["high"] = round(max(bar["high"], price), 2)
                bar["low"] = round(min(bar["low"], price), 2)
                bar["close"] = round(price, 2)
                bar["volume"] = int(bar["volume"]) + volume_delta
            else:
                open_px = bars[-1]["close"] if bars else price
                bar = {
                    "time": bucket,
                    "open": round(open_px, 2),
                    "high": round(max(open_px, price), 2),
                    "low": round(min(open_px, price), 2),
                    "close": round(price, 2),
                    "volume": volume_delta,
                }
            self.db.upsert_candles(instrument_token, tf, [bar], synthetic=synthetic)
            updated[tf] = bar
        return updated


def compute_indicators(candles: list[dict[str, Any]], indicators: list[str]) -> dict[str, list[dict[str, float | int]]]:
    result: dict[str, list[dict[str, float | int]]] = {}
    normalized = [name.strip().lower() for name in indicators if name.strip()]
    for key in normalized:
        if key in {"vwap", "rsi"}:
            continue
        if key.startswith("ema") or key.startswith("sma"):
            suffix = key[3:] or "20"
            if not suffix.isdigit() or not 1 <= int(suffix) <= 500:
                raise ValueError("EMA and SMA periods must be integers between 1 and 500")
        else:
            raise ValueError(f"Unsupported indicator: {key}")
    if not candles:
        return result

    closes = [float(c["close"]) for c in candles]
    times = [int(c["time"]) for c in candles]

    for key in normalized:
        if key.startswith("ema"):
            period = int(key[3:] or "20")
            k = 2.0 / (period + 1)
            ema_val = closes[0]
            series = []
            for t, c in zip(times, closes):
                ema_val = c * k + ema_val * (1.0 - k)
                series.append({"time": t, "value": round(ema_val, 2)})
            result[f"ema{period}"] = series

        elif key.startswith("sma"):
            period = int(key[3:] or "20")
            series = []
            for i, t in enumerate(times):
                window = closes[max(0, i - period + 1) : i + 1]
                series.append({"time": t, "value": round(sum(window) / len(window), 2)})
            result[f"sma{period}"] = series

        elif key == "vwap":
            cum_pv = 0.0
            cum_v = 0.0
            session_date = None
            series = []
            ist = timezone(timedelta(hours=5, minutes=30))
            for c in candles:
                candle_date = datetime.fromtimestamp(int(c["time"]), ist).date()
                if candle_date != session_date:
                    session_date = candle_date
                    cum_pv = 0.0
                    cum_v = 0.0
                typical = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
                volume = max(0.0, float(c.get("volume", 0)))
                cum_pv += typical * volume
                cum_v += volume
                series.append({"time": int(c["time"]), "value": round(cum_pv / cum_v, 2) if cum_v else 0.0})
            result["vwap"] = series

        elif key == "rsi":
            period = 14
            series = []
            gains: list[float] = []
            losses: list[float] = []
            avg_g = 0.0
            avg_l = 0.0
            for i in range(1, len(closes)):
                delta = closes[i] - closes[i - 1]
                gains.append(max(0.0, delta))
                losses.append(max(0.0, -delta))
                if i < period:
                    continue
                if i == period:
                    avg_g = sum(gains[:period]) / period
                    avg_l = sum(losses[:period]) / period
                else:
                    avg_g = (avg_g * (period - 1) + gains[-1]) / period
                    avg_l = (avg_l * (period - 1) + losses[-1]) / period
                if avg_g == 0 and avg_l == 0:
                    rsi = 50.0
                elif avg_l == 0:
                    rsi = 100.0
                else:
                    rs = avg_g / avg_l
                    rsi = 100.0 - (100.0 / (1.0 + rs))
                series.append({"time": times[i], "value": round(rsi, 2)})
            result["rsi"] = series

    return result
