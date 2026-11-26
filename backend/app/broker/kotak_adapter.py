from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import pandas as pd
import requests

from app.broker.base import BrokerAdapter
from app.models import (
    AuthSessionResponse,
    HoldingRecord,
    Instrument,
    ModifyOrderRequest,
    OrderRecord,
    PlaceOrderRequest,
    PositionConvertRequest,
    PositionRecord,
    QuoteData,
    TotpLoginRequest,
    TradeRecord,
)

logger = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))


class KotakNeoBroker(BrokerAdapter):
    mode = "kotak"

    def __init__(
        self,
        environment: str = "prod",
        on_tick_callback: Optional[Callable[[QuoteData], None]] = None,
        on_order_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.environment = environment
        self.client: Any = None
        self.ucc = ""
        self.authenticated = False
        self.two_fa_verified = False
        self._quotes_cache: dict[str, QuoteData] = {}
        self._segment_instruments: dict[str, list[Instrument]] = {}
        self._segment_loaded_at: dict[str, float] = {}
        self._token_to_segment: dict[str, str] = {}
        self._on_tick_callback = on_tick_callback
        self._on_order_callback = on_order_callback
        self._pending_ticks: list[QuoteData] = []
        self._pending_order_events: list[dict[str, Any]] = []
        self._seen_order_events: set[tuple[str, str]] = set()
        self._callback_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._last_order_call = 0.0
        self._last_quote_call = 0.0
        self._feed_loop: asyncio.AbstractEventLoop | None = None
        self._feed_thread: threading.Thread | None = None
        self._feed_ready = threading.Event()
        self._subscriptions: set[tuple[str, str]] = set()
        self._market_ws: Any = None

    def _require_client(self) -> Any:
        if self.client is None or not self.two_fa_verified:
            raise RuntimeError("Kotak Neo session is not authenticated. Complete TOTP + MPIN login first.")
        return self.client

    @staticmethod
    def _first_present(values: dict[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in values and values[key] is not None and values[key] != "":
                return values[key]
        return default

    @staticmethod
    def _extract_error(resp: Any) -> Optional[str]:
        if not isinstance(resp, dict):
            return None
        for key in ("Error", "Error Message", "error", "errMsg"):
            if key in resp and resp[key]:
                val = resp[key]
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    return str(val[0].get("message") or val[0])
                return str(val)
        return None

    @classmethod
    def _data_rows(cls, resp: Any, operation: str) -> list[dict[str, Any]]:
        err = cls._extract_error(resp)
        if err:
            raise RuntimeError(f"Kotak {operation} error: {err}")
        rows = resp.get("data") if isinstance(resp, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError(f"Kotak {operation} returned an invalid response")
        return [row for row in rows if isinstance(row, dict)]

    def _throttle(self, kind: str) -> None:
        interval = 0.12 if kind == "quote" else 0.15
        with self._rate_lock:
            now = time.monotonic()
            last = self._last_quote_call if kind == "quote" else self._last_order_call
            delay = interval - (now - last)
            if delay > 0:
                time.sleep(delay)
            if kind == "quote":
                self._last_quote_call = time.monotonic()
            else:
                self._last_order_call = time.monotonic()

    def login(self, req: TotpLoginRequest) -> AuthSessionResponse:
        try:
            from neo_api_client import NeoAPI
        except ImportError as exc:
            raise RuntimeError("kotakneoapi is not installed. Run: pip install kotakneoapi==3.0.13") from exc
        if not req.consumer_key or not req.mobile_number or not req.ucc or not req.totp or not req.mpin:
            raise ValueError("consumer_key, mobile_number, ucc, totp, and mpin are required for live Kotak Neo login.")
        if not req.totp.isdigit() or len(req.totp) != 6 or not req.mpin.isdigit() or len(req.mpin) != 6:
            raise ValueError("TOTP and MPIN must each contain exactly six digits.")

        client = NeoAPI(environment=self.environment, consumer_key=req.consumer_key)
        login_resp = client.totp_login(mobile_number=req.mobile_number, ucc=req.ucc, totp=req.totp)
        err = self._extract_error(login_resp)
        if err or not getattr(client.configuration, "view_token", None):
            raise ValueError(f"Kotak TOTP login failed: {err or 'view token was not issued'}")
        val_resp = client.totp_validate(mpin=req.mpin)
        err = self._extract_error(val_resp)
        if err or not getattr(client.configuration, "edit_token", None) or not getattr(client.configuration, "edit_sid", None):
            client.logout()
            raise ValueError(f"Kotak MPIN validation failed: {err or 'trade session was not issued'}")

        self.logout()
        self.client = client
        self.ucc = req.ucc
        self.authenticated = True
        self.two_fa_verified = True
        self._start_feeds()
        return AuthSessionResponse(
            session_id=f"kotak_{req.ucc}_{int(time.time())}",
            mode="kotak",
            ucc=req.ucc,
            authenticated=True,
            two_fa_verified=True,
            message="Authenticated with Kotak Neo API v3 (TOTP + MPIN).",
        )

    def _start_feeds(self) -> None:
        self._feed_ready.clear()
        self._feed_loop = asyncio.new_event_loop()
        self._feed_thread = threading.Thread(target=self._run_feed_loop, name="kotak-feeds", daemon=True)
        self._feed_thread.start()
        asyncio.run_coroutine_threadsafe(self._run_feeds(), self._feed_loop)

    def _run_feed_loop(self) -> None:
        assert self._feed_loop is not None
        asyncio.set_event_loop(self._feed_loop)
        try:
            self._feed_loop.run_forever()
        finally:
            self._feed_loop.close()

    async def _run_feeds(self) -> None:
        client = self.client
        retry_delay = 1.0
        while self.client is client and self.two_fa_verified:
            market = None
            try:
                async with client.create_websocket(max_connect_retries=1) as market:
                    async with client.create_order_feed(max_connect_retries=1) as orders:
                        self._market_ws = market
                        self._feed_ready.set()
                        retry_delay = 1.0
                        if self._subscriptions:
                            await self._apply_subscriptions(set(self._subscriptions))
                        async with asyncio.TaskGroup() as feeds:
                            feeds.create_task(self._consume_market(market))
                            feeds.create_task(self._consume_orders(orders))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Kotak feeds disconnected; reconnecting: %s", exc)
            finally:
                if self._market_ws is market:
                    self._market_ws = None
                self._feed_ready.clear()
            if self.client is client and self.two_fa_verified:
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

    async def _consume_market(self, market: Any) -> None:
        async for message in market:
            try:
                quote = self._normalize_ws_tick(message)
            except (TypeError, ValueError):
                logger.warning("Ignoring malformed Kotak market message")
                continue
            if quote is None:
                continue
            with self._callback_lock:
                self._quotes_cache[quote.instrument_token] = quote
                self._pending_ticks.append(quote)
            if self._on_tick_callback:
                try:
                    self._on_tick_callback(quote)
                except Exception:
                    logger.exception("Kotak tick callback failed")
        raise RuntimeError("Kotak market feed ended")

    async def _consume_orders(self, orders: Any) -> None:
        async for message in orders:
            data = message.model_dump(by_alias=True)
            payload = data.get("data") or {}
            if message.type == "order":
                event_key = f"{payload.get('nOrdNo', '')}:{payload.get('updRecvTm', '')}"
            else:
                event_key = f"position:{payload.get('exSeg', '')}:{payload.get('sym', '')}:{payload.get('prod', '')}:{payload.get('hsUpTm', '')}"
            with self._callback_lock:
                if event_key in self._seen_order_events:
                    continue
                self._seen_order_events.add(event_key)
                if len(self._seen_order_events) > 10000:
                    self._seen_order_events = set(sorted(self._seen_order_events)[-5000:])
                self._pending_order_events.append(data)
            if self._on_order_callback:
                try:
                    self._on_order_callback(data)
                except Exception:
                    logger.exception("Kotak order callback failed")
        raise RuntimeError("Kotak order feed ended")

    def _infer_segment(self, token: str) -> str:
        segment = self._token_to_segment.get(token)
        if segment:
            return segment
        for instruments in self._segment_instruments.values():
            for inst in instruments:
                if inst.instrument_token == token:
                    return inst.exchange_segment
        if token.upper() in {"NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE", "SENSEX", "INDIA VIX"}:
            return "nse_cm"
        return "nse_fo" if any(marker in token.upper() for marker in ("CE", "PE", "FUT")) else "nse_cm"

    def subscribe_tokens(self, tokens: list[str], replace: bool = False) -> None:
        if not self.client or not self.two_fa_verified:
            return
        wanted: set[tuple[str, str]] = set()
        for token in tokens[:500]:
            norm = "Nifty 50" if token.upper() == "NIFTY 50" else ("Nifty Bank" if token.upper() == "NIFTY BANK" else token)
            wanted.add((self._infer_segment(norm), norm))
        removed = self._subscriptions - wanted if replace else set()
        new = wanted - self._subscriptions
        if (new or removed) and self._feed_loop and self._feed_ready.is_set():
            future = asyncio.run_coroutine_threadsafe(self._change_subscriptions(new, removed), self._feed_loop)
            try:
                future.result(timeout=8)
            except Exception as exc:
                logger.warning("Kotak subscription change failed: %s", exc)
                return
        self._subscriptions = wanted

    async def _change_subscriptions(self, added: set[tuple[str, str]], removed: set[tuple[str, str]]) -> None:
        from neo_api_client.websocket.feed import WsToken

        if self._market_ws is None:
            raise RuntimeError("Kotak market feed is not connected")
        indices = [WsToken(segment, token) for segment, token in removed if token in {"Nifty 50", "Nifty Bank"}]
        scrips = [WsToken(segment, token) for segment, token in removed if token not in {"Nifty 50", "Nifty Bank"}]
        if indices:
            await self._market_ws.unsubscribe_index(indices)
        if scrips:
            await self._market_ws.unsubscribe_scrips(scrips)
            await self._market_ws.unsubscribe_depth(scrips)
        await self._apply_subscriptions(added)

    async def _apply_subscriptions(self, tokens: set[tuple[str, str]]) -> None:
        from neo_api_client.websocket.feed import WsToken

        if self._market_ws is None:
            raise RuntimeError("Kotak market feed is not connected")
        index_tokens = [WsToken(segment, token) for segment, token in tokens if token in {"Nifty 50", "Nifty Bank"}]
        scrip_tokens = [WsToken(segment, token) for segment, token in tokens if token not in {"Nifty 50", "Nifty Bank"}]
        if index_tokens:
            await self._market_ws.subscribe_index(index_tokens)
        if scrip_tokens:
            await self._market_ws.subscribe_scrips(scrip_tokens)
            await self._market_ws.subscribe_depth(scrip_tokens)

    def _normalize_ws_tick(self, item: Any) -> Optional[QuoteData]:
        if hasattr(item, "model_dump"):
            item = item.model_dump()
        if not isinstance(item, dict):
            return None
        raw_tk = str(self._first_present(item, "instrument_token", "tk", default="") or "")
        if not raw_tk:
            return None
        tk = "NIFTY 50" if raw_tk.lower() == "nifty 50" else ("NIFTY BANK" if raw_tk.lower() == "nifty bank" else raw_tk)
        prev = self._quotes_cache.get(tk)
        ltp = float(self._first_present(item, "last_traded_price", "ltp", "iv", default=prev.ltp if prev else 0.0) or 0.0)
        close = float(self._first_present(item, "close_price", "c", "ic", default=prev.close if prev else ltp) or 0.0)
        change = float(self._first_present(item, "net_change", "cng", "change", default=ltp - close) or 0.0)
        change_pct = float(self._first_present(
            item, "net_change_percent", "nc", "change_pct",
            default=(change / close * 100.0) if close else 0.0,
        ) or 0.0)
        if "buy" in item or "depth_bids" in item:
            buys = self._first_present(item, "buy", "depth_bids", default=[]) or []
        else:
            buys = prev.depth_bids if prev else []
        if "sell" in item or "depth_asks" in item:
            sells = self._first_present(item, "sell", "depth_asks", default=[]) or []
        else:
            sells = prev.depth_asks if prev else []
        depth_bids = [
            {"price": float(row.get("price") or 0), "qty": int(row.get("quantity") or row.get("qty") or 0), "orders": int(row.get("orders") or 0)}
            for row in buys[:5]
        ]
        depth_asks = [
            {"price": float(row.get("price") or 0), "qty": int(row.get("quantity") or row.get("qty") or 0), "orders": int(row.get("orders") or 0)}
            for row in sells[:5]
        ]
        return QuoteData(
            instrument_token=tk,
            exchange_segment=str(self._first_present(item, "exchange_segment", "e", default=prev.exchange_segment if prev else self._infer_segment(tk))),
            trading_symbol=str(self._first_present(item, "trading_symbol", "ts", default=prev.trading_symbol if prev else tk)),
            ltp=round(ltp, 2),
            open=float(self._first_present(item, "open_price", "op", "openingPrice", default=prev.open if prev else ltp) or 0.0),
            high=float(self._first_present(item, "high_price", "h", "highPrice", default=prev.high if prev else ltp) or 0.0),
            low=float(self._first_present(item, "low_price", "lo", "lowPrice", default=prev.low if prev else ltp) or 0.0),
            close=round(close, 2),
            change=round(change, 2),
            change_pct=round(change_pct, 2),
            volume=int(self._first_present(item, "volume_traded_today", "v", default=prev.volume if prev else 0) or 0),
            oi=int(self._first_present(item, "open_interest", "oi", default=prev.oi if prev else 0) or 0),
            bid=depth_bids[0]["price"] if depth_bids else float(self._first_present(item, "bp", default=prev.bid if prev else 0) or 0),
            ask=depth_asks[0]["price"] if depth_asks else float(self._first_present(item, "sp", default=prev.ask if prev else 0) or 0),
            bid_qty=depth_bids[0]["qty"] if depth_bids else int(self._first_present(item, "bq", default=prev.bid_qty if prev else 0) or 0),
            ask_qty=depth_asks[0]["qty"] if depth_asks else int(self._first_present(item, "bs", default=prev.ask_qty if prev else 0) or 0),
            depth_bids=depth_bids,
            depth_asks=depth_asks,
            updated_at=int(time.time()),
        )

    def logout(self) -> dict[str, Any]:
        if self.client:
            try:
                self.client.logout()
            except Exception:
                logger.warning("Kotak local logout completed without broker confirmation")
        loop = self._feed_loop
        self._feed_ready.clear()
        if loop and loop.is_running():
            async def close_feeds() -> None:
                for task in asyncio.all_tasks(loop):
                    if task is not asyncio.current_task():
                        task.cancel()

            try:
                asyncio.run_coroutine_threadsafe(close_feeds(), loop).result(timeout=5)
            except Exception:
                logger.warning("Kotak feed shutdown did not finish cleanly")
            loop.call_soon_threadsafe(loop.stop)
        if self._feed_thread and self._feed_thread.is_alive():
            self._feed_thread.join(timeout=5)
        self._feed_loop = None
        self._feed_thread = None
        self._market_ws = None
        self.client = None
        self.ucc = ""
        self.authenticated = False
        self.two_fa_verified = False
        self._quotes_cache.clear()
        self._segment_instruments.clear()
        self._token_to_segment.clear()
        self._subscriptions.clear()
        with self._callback_lock:
            self._pending_ticks.clear()
            self._pending_order_events.clear()
            self._seen_order_events.clear()
        return {"status": "logged_out"}

    def _ensure_segment_cached(self, seg: str) -> list[Instrument]:
        now = time.time()
        if seg in self._segment_instruments and now - self._segment_loaded_at.get(seg, 0) < 14400:
            return self._segment_instruments[seg]
        client = self._require_client()
        csv_url = client.scrip_master(exchange_segment=seg)
        if not isinstance(csv_url, str) or not csv_url.startswith("https://"):
            raise RuntimeError(f"Kotak scrip master for {seg} is unavailable")
        resp = requests.get(csv_url, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text), low_memory=False).rename(columns=lambda x: x.strip())
        if seg.endswith("fo") and "lExpiryDate" in df.columns:
            exp_dt = pd.to_datetime(df["lExpiryDate"], unit="s", errors="coerce", utc=True)
            if seg in ("nse_fo", "cde_fo"):
                exp_dt = exp_dt + pd.to_timedelta(315511200, unit="s")
            df["expiry_formatted"] = exp_dt.dt.tz_convert(IST).dt.strftime("%d%b%Y").str.upper()
        else:
            df["expiry_formatted"] = None
        parsed: list[Instrument] = []
        for row in df.to_dict(orient="records"):
            token = str(row.get("pSymbol") or "").strip()
            if not token:
                continue
            option = str(row.get("pOptionType") or "").strip().upper()
            raw_type = str(row.get("pInstType") or "").strip().upper()
            strike_raw = row.get("dStrikePrice;")
            strike = float(strike_raw) / 100.0 if strike_raw is not None and str(strike_raw) not in ("", "nan", "-1", "-1.0") else None
            expiry = row.get("expiry_formatted")
            inst = Instrument(
                instrument_token=token,
                exchange_segment=str(row.get("pExchSeg") or seg),
                trading_symbol=str(row.get("pTrdSymbol") or token),
                symbol=str(row.get("pSymbolName") or ""),
                name=str(row.get("pDesc") or row.get("pSymbolName") or ""),
                instrument_type="CE" if option == "CE" else ("PE" if option == "PE" else ("FUT" if "FUT" in raw_type else "EQ")),
                expiry=expiry if pd.notna(expiry) else None,
                strike_price=strike,
                option_type=option if option in ("CE", "PE") else None,
                lot_size=max(1, int(float(row.get("lLotSize") or row.get("iLotSize") or 1))),
                tick_size=float(row.get("dTickSize") or 5) / 100.0,
                underlying=str(row.get("pSymbolName") or ""),
            )
            self._token_to_segment[token] = inst.exchange_segment
            parsed.append(inst)
        self._segment_instruments[seg] = parsed
        self._segment_loaded_at[seg] = now
        return parsed

    def list_instruments(self, query: str = "", exchange_segment: str | None = None, limit: int | None = 250) -> list[Instrument]:
        items = self._ensure_segment_cached(exchange_segment or "nse_cm")
        q = query.strip().upper()
        if not q:
            return items if limit is None else items[:limit]
        exact = [i for i in items if q in {i.instrument_token.upper(), i.symbol.upper(), i.trading_symbol.upper()}]
        matched = exact or [i for i in items if q in i.trading_symbol.upper() or q in i.symbol.upper() or q in i.name.upper()]
        return matched if limit is None else matched[:limit]

    def get_quotes(self, tokens: list[str]) -> list[QuoteData]:
        if len(tokens) > 200:
            raise ValueError("A maximum of 200 instrument quotes can be requested at once")
        client = self._require_client()
        requested: list[dict[str, str]] = []
        for token in dict.fromkeys(tokens):
            if not isinstance(token, str) or not token.strip() or len(token) > 100:
                raise ValueError("Instrument identifiers must be non-empty strings up to 100 characters")
            norm = "Nifty 50" if token.upper() == "NIFTY 50" else ("Nifty Bank" if token.upper() == "NIFTY BANK" else token)
            requested.append({"instrument_token": norm, "exchange_segment": self._infer_segment(norm)})
        out: list[QuoteData] = []
        for start in range(0, len(requested), 50):
            self._throttle("quote")
            resp = client.quotes(instrument_tokens=requested[start : start + 50], quote_type="all")
            err = self._extract_error(resp)
            if err:
                raise RuntimeError(f"Kotak quotes error: {err}")
            rows = resp.get("data") if isinstance(resp, dict) else None
            if not isinstance(rows, list):
                raise RuntimeError("Kotak quotes returned an invalid response")
            for row in rows:
                if not isinstance(row, dict):
                    continue
                token = str(row.get("exchange_token") or row.get("instrument_token") or "")
                if not token:
                    continue
                token = "NIFTY 50" if token.lower() == "nifty 50" else ("NIFTY BANK" if token.lower() == "nifty bank" else token)
                ohlc = row.get("ohlc") or {}
                ltp = float(self._first_present(row, "ltp", "last_price", default=0.0) or 0.0)
                close = float(self._first_present(ohlc, "close", default=ltp) or 0.0)
                change = float(self._first_present(row, "net_change", default=ltp - close) or 0.0)
                change_pct = float(self._first_present(
                    row, "per_change", default=(change / close * 100.0) if close else 0.0,
                ) or 0.0)
                depth = row.get("depth") or {}
                bids = [{"price": float(b.get("price") or 0), "qty": int(b.get("quantity") or 0), "orders": int(b.get("orders") or 0)} for b in (depth.get("buy") or [])[:5]]
                asks = [{"price": float(s.get("price") or 0), "qty": int(s.get("quantity") or 0), "orders": int(s.get("orders") or 0)} for s in (depth.get("sell") or [])[:5]]
                quote = QuoteData(
                    instrument_token=token,
                    exchange_segment=str(self._first_present(row, "exchange", default=self._infer_segment(token))),
                    trading_symbol=str(self._first_present(row, "display_symbol", "trading_symbol", default=token)),
                    ltp=round(ltp, 2),
                    open=float(self._first_present(ohlc, "open", default=ltp) or 0.0),
                    high=float(self._first_present(ohlc, "high", default=ltp) or 0.0),
                    low=float(self._first_present(ohlc, "low", default=ltp) or 0.0),
                    close=round(close, 2), change=round(change, 2), change_pct=round(change_pct, 2),
                    volume=int(row.get("volume") or 0), oi=int(row.get("oi") or 0),
                    bid=bids[0]["price"] if bids else 0, ask=asks[0]["price"] if asks else 0,
                    bid_qty=bids[0]["qty"] if bids else 0, ask_qty=asks[0]["qty"] if asks else 0,
                    depth_bids=bids, depth_asks=asks, updated_at=int(time.time()),
                )
                self._quotes_cache[token] = quote
                out.append(quote)
        return out

    def historical_candles(self, instrument: str, timeframe: str, count: int) -> list[dict[str, Any]]:
        client = self._require_client()
        interval_map = {"3m": "3min", "5m": "5min", "10m": "10min", "15m": "15min", "1h": "60min", "1D": "D"}
        interval = interval_map.get(timeframe, "1min")
        days = {"1min": 5, "3min": 15, "5min": 25, "10min": 25, "15min": 40, "30min": 60, "60min": 90, "D": 365, "W": 1825}[interval]
        end = datetime.now(IST).date()
        start = end - timedelta(days=days)
        resp = client.historical_data(neosymbol=f"{self._infer_segment(instrument)}|{instrument}", interval=interval, from_date=start.isoformat(), to_date=end.isoformat())
        rows = self._data_rows(resp, "historical_data")
        bars: list[dict[str, Any]] = []
        for row in rows:
            raw_time = row.get("time") or row.get("date") or row.get("timestamp")
            if raw_time is None:
                continue
            try:
                if isinstance(raw_time, (int, float)):
                    stamp = datetime.fromtimestamp(float(raw_time), IST)
                else:
                    stamp = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=IST)
                    stamp = stamp.astimezone(IST)
                bars.append({"time": int(stamp.timestamp()), "open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]), "close": float(row["close"]), "volume": int(row.get("volume") or 0)})
            except (KeyError, TypeError, ValueError):
                continue
        return sorted({bar["time"]: bar for bar in bars}.values(), key=lambda bar: bar["time"])[-count:]

    def place_order(self, req: PlaceOrderRequest) -> OrderRecord:
        client = self._require_client()
        if req.product in ("CO", "BO"):
            raise ValueError("Cover and bracket orders are no longer supported by Kotak Trade APIs")
        self._throttle("order")
        tag = req.client_order_id or req.tag
        if tag and len(tag) > 50:
            tag = hashlib.sha256(tag.encode("utf-8")).hexdigest()[:32]
        resp = client.place_order(
            exchange_segment=req.exchange_segment, product=req.product, price=req.price, order_type=req.order_type,
            quantity=req.quantity, validity=req.validity, trading_symbol=req.trading_symbol, transaction_type=req.transaction_type,
            amo=req.amo, disclosed_quantity=req.disclosed_quantity, trigger_price=req.trigger_price, tag=tag,
        )
        # The SDK can return transport exceptions inside an Error response.
        # Keep the idempotency reservation until the broker result is reconciled.
        order_no = str(resp.get("nOrdNo") or "") if isinstance(resp, dict) else ""
        if not order_no or not isinstance(resp, dict) or str(resp.get("stat", "")).lower() != "ok":
            raise RuntimeError("Kotak did not confirm order acceptance; check the order book before retrying")
        return OrderRecord(
            order_id=order_no, trading_symbol=req.trading_symbol, exchange_segment=req.exchange_segment,
            transaction_type=req.transaction_type, product=req.product, order_type=req.order_type,
            quantity=int(req.quantity), filled_quantity=0, pending_quantity=int(req.quantity), price=float(req.price or 0),
            trigger_price=float(req.trigger_price or 0), status="open", tag=tag, order_time=time.strftime("%d-%b-%Y %H:%M:%S"),
        )

    def modify_order(self, req: ModifyOrderRequest) -> OrderRecord:
        client = self._require_client()
        self._throttle("order")
        resp = client.modify_order(order_id=req.order_id, price=req.price, quantity=req.quantity, disclosed_quantity=req.disclosed_quantity, trigger_price=req.trigger_price, validity=req.validity, order_type=req.order_type)
        if (
            not isinstance(resp, dict)
            or str(resp.get("stat", "")).lower() != "ok"
            or str(resp.get("nOrdNo") or "") != req.order_id
        ):
            raise RuntimeError("Kotak did not confirm order modification; check order history before retrying")
        for order in self.order_book():
            if order.order_id == req.order_id:
                return order
        raise RuntimeError("Modification accepted but current order state is unavailable; refresh the order book")

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        client = self._require_client()
        self._throttle("order")
        resp = client.cancel_order(order_id=order_id)
        if (
            not isinstance(resp, dict)
            or str(resp.get("stat", "")).lower() != "ok"
            or str(resp.get("nOrdNo") or "") != order_id
        ):
            raise RuntimeError("Kotak did not confirm cancellation; check order history before retrying")
        return resp

    def order_book(self) -> list[OrderRecord]:
        rows = self._data_rows(self._require_client().order_report(), "order_report")
        return [OrderRecord(
            order_id=str(r.get("nOrdNo") or ""), exchange_order_id=str(r.get("exOrdId") or ""), trading_symbol=str(r.get("trdSym") or ""),
            exchange_segment=str(r.get("exSeg") or ""), transaction_type=str(r.get("trnsTp") or ""), product=str(r.get("prod") or ""),
            order_type=str(r.get("prcTp") or ""), quantity=int(r.get("qty") or 0), filled_quantity=int(r.get("fldQty") or 0),
            pending_quantity=int(r.get("unFldSz") or 0), price=float(r.get("prc") or 0), trigger_price=float(r.get("trgPrc") or 0),
            avg_price=float(r.get("avgPrc") or 0), status=str(r.get("ordSt") or ""), rejection_reason=str(r.get("rejRsn") or ""),
            tag=str(r.get("GuiOrdId") or "") or None, order_time=str(r.get("ordDtTm") or ""),
        ) for r in rows]

    def order_history(self, order_id: str) -> list[dict[str, Any]]:
        return self._data_rows(self._require_client().order_history(order_id=order_id), "order_history")

    def trade_book(self, order_id: str | None = None) -> list[TradeRecord]:
        rows = self._data_rows(self._require_client().trade_report(), "trade_report")
        if order_id:
            rows = [row for row in rows if str(row.get("nOrdNo")) == order_id]
        return [TradeRecord(
            trade_id=str(r.get("flId") or ""), order_id=str(r.get("nOrdNo") or ""), trading_symbol=str(r.get("trdSym") or ""),
            exchange_segment=str(r.get("exSeg") or ""), transaction_type=str(r.get("trnsTp") or ""), product=str(r.get("prod") or ""),
            quantity=int(r.get("fldQty") or 0), fill_price=float(r.get("avgPrc") or 0), fill_time=str(r.get("flTm") or r.get("exTm") or ""),
        ) for r in rows]

    def positions(self, require_ltp: bool = True) -> list[PositionRecord]:
        from neo_api_client.utils.positions_pnl import compute_position_metrics, is_derivatives_segment

        rows = self._data_rows(self._require_client().positions(), "positions")
        open_rows = [
            row for row in rows
            if int(float(row.get("cfBuyQty") or 0)) - int(float(row.get("cfSellQty") or 0))
            + int(float(row.get("flBuyQty") or 0)) - int(float(row.get("flSellQty") or 0)) != 0
        ]
        quotes: dict[str, QuoteData] = {}
        if require_ltp and open_rows:
            tokens = [str(row.get("tok") or "") for row in open_rows]
            if any(not token for token in tokens):
                raise RuntimeError("Kotak returned an open position without an instrument token")
            for row in open_rows:
                token = str(row.get("tok") or "")
                segment = str(row.get("exSeg") or "")
                if token and segment:
                    self._token_to_segment[token] = segment
            unique_tokens = list(dict.fromkeys(tokens))
            for start in range(0, len(unique_tokens), 200):
                quotes.update({quote.instrument_token: quote for quote in self.get_quotes(unique_tokens[start : start + 200])})
            missing = [token for token in tokens if token not in quotes or quotes[token].ltp <= 0]
            if missing:
                raise RuntimeError("Live LTP is unavailable for open positions; P&L was not calculated")

        holdings_by_token: dict[str, dict[str, Any]] = {}
        if require_ltp and any(
            not is_derivatives_segment(row.get("exSeg")) and row.get("exSeg") != "nse_com"
            and int(float(row.get("cfBuyQty") or 0)) != int(float(row.get("cfSellQty") or 0))
            for row in open_rows
        ):
            holdings_by_token = {
                holding.instrument_token: {
                    "averagePrice": holding.average_price,
                    "exchangeIdentifier": holding.instrument_token,
                }
                for holding in self.holdings()
            }

        out: list[PositionRecord] = []
        for row in rows:
            token = str(row.get("tok") or "")
            segment = str(row.get("exSeg") or "")
            lot_size = int(row.get("lotSz") or 1)
            multiplier = float(row.get("multiplier") or 1)
            factor = multiplier * float(row.get("genNum") or 1) / float(row.get("genDen") or 1) * float(row.get("prcNum") or 1) / float(row.get("prcDen") or 1)
            buy_qty = int(float(row.get("cfBuyQty") or 0)) + int(float(row.get("flBuyQty") or 0))
            sell_qty = int(float(row.get("cfSellQty") or 0)) + int(float(row.get("flSellQty") or 0))
            net_qty = buy_qty - sell_qty
            quote = quotes.get(token)
            if require_ltp and net_qty:
                ltp = quote.ltp if quote else 0.0
                holding = holdings_by_token.get(token)
                # The SDK P&L helper recognizes derivatives only by the _fo suffix.
                metrics_row = {**row, "exSeg": "nse_fo"} if segment == "nse_com" else row
                metrics = compute_position_metrics(metrics_row, holding, ltp)
                if metrics.get("pnlCalculationError"):
                    raise RuntimeError(f"Kotak position valuation failed for {token}: {metrics['pnlCalculationError']}")
                avg_price = float(metrics.get("averagePrice") or 0)
                pnl = float(metrics.get("positionPnl") or 0)
            else:
                ltp = float(row.get("ltp") or 0) or (self._quotes_cache[token].ltp if token in self._quotes_cache else 0)
                avg_price = 0.0
                pnl = 0.0
            day_buy_qty = int(float(row.get("flBuyQty") or 0))
            day_sell_qty = int(float(row.get("flSellQty") or 0))
            day_buy_avg = float(row.get("buyAmt") or 0) / (day_buy_qty * factor) if day_buy_qty else 0
            day_sell_avg = float(row.get("sellAmt") or 0) / (day_sell_qty * factor) if day_sell_qty else 0
            out.append(PositionRecord(
                instrument_token=token, trading_symbol=str(row.get("trdSym") or ""), exchange_segment=segment,
                product=str(row.get("prod") or ""), net_qty=net_qty, buy_qty=buy_qty, sell_qty=sell_qty,
                buy_avg_price=round(day_buy_avg, 2), sell_avg_price=round(day_sell_avg, 2), avg_price=round(avg_price, 2), ltp=round(ltp, 2),
                realized_pnl=round(pnl, 2) if net_qty == 0 else 0,
                unrealized_pnl=round(pnl, 2) if net_qty else 0, pnl=round(pnl, 2), lot_size=lot_size, multiplier=multiplier,
            ))
        return out

    def convert_position(self, req: PositionConvertRequest) -> dict[str, Any]:
        raise NotImplementedError("Kotak position conversion is not enabled; use an offsetting order or the broker platform.")

    def holdings(self) -> list[HoldingRecord]:
        rows = self._data_rows(self._require_client().holdings(), "holdings")
        active_rows = [row for row in rows if int(row.get("quantity") or 0) != 0]
        tokens: list[str] = []
        for row in active_rows:
            token = str(row.get("instrumentToken") or row.get("exchangeIdentifier") or "")
            segment = str(row.get("exchangeSegment") or "nse_cm")
            if not token:
                raise RuntimeError("Kotak returned a holding without an instrument token")
            self._token_to_segment[token] = segment
            tokens.append(token)
        quotes: dict[str, QuoteData] = {}
        unique_tokens = list(dict.fromkeys(tokens))
        for start in range(0, len(unique_tokens), 200):
            quotes.update({quote.instrument_token: quote for quote in self.get_quotes(unique_tokens[start : start + 200])})
        out: list[HoldingRecord] = []
        for row in rows:
            qty = int(row.get("quantity") or 0)
            token = str(row.get("instrumentToken") or row.get("exchangeIdentifier") or "")
            avg = float(row.get("averagePrice") or 0)
            close = float(row.get("closingPrice") or avg)
            cost = float(row.get("holdingCost") or qty * avg)
            if qty:
                quote = quotes.get(token)
                if quote is None or quote.ltp <= 0:
                    raise RuntimeError(f"Live LTP is unavailable for holding {token}; P&L was not calculated")
                ltp = quote.ltp
            else:
                ltp = close
            market = qty * ltp
            pnl = market - cost
            out.append(HoldingRecord(
                instrument_token=token, exchange_segment=str(row.get("exchangeSegment") or "nse_cm"),
                trading_symbol=str(row.get("displaySymbol") or row.get("symbol") or ""), symbol=str(row.get("symbol") or ""), quantity=qty,
                sellable_quantity=int(row.get("sellableQuantity") or qty), average_price=round(avg, 2), closing_price=round(close, 2), ltp=round(ltp, 2),
                mkt_value=round(market, 2), holding_cost=round(cost, 2), pnl=round(pnl, 2), pnl_pct=round(pnl / cost * 100, 2) if cost else 0,
            ))
        return out

    def limits(self) -> dict[str, Any]:
        raw = self._require_client().limits()
        err = self._extract_error(raw)
        if err:
            raise RuntimeError(f"Kotak limits error: {err}")
        data = raw.get("data") if isinstance(raw, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("Kotak limits returned invalid data")
        net = float(data.get("Net") or 0)
        used = float(data.get("MarginUsed") or 0)
        return {**data, "availableCash": round(net, 2), "marginUsed": round(used, 2)}

    def margin_required(self, req: PlaceOrderRequest) -> dict[str, Any]:
        if not req.instrument_token:
            raise ValueError("instrument_token is required for Kotak margin calculation")
        resp = self._require_client().margin_required(
            exchange_segment=req.exchange_segment, price=req.price, order_type=req.order_type, product=req.product,
            quantity=req.quantity, instrument_token=req.instrument_token, transaction_type=req.transaction_type, trigger_price=req.trigger_price,
        )
        err = self._extract_error(resp)
        if err:
            raise RuntimeError(f"Kotak margin_required error: {err}")
        if not isinstance(resp, dict):
            raise RuntimeError("Kotak margin_required returned an invalid response")
        return resp

    def drain_ticks(self) -> list[QuoteData]:
        with self._callback_lock:
            ticks = self._pending_ticks
            self._pending_ticks = []
        return ticks

    def drain_order_events(self) -> list[dict[str, Any]]:
        with self._callback_lock:
            events = self._pending_order_events
            self._pending_order_events = []
        return events

    def step_ticks(self) -> list[QuoteData]:
        return self.drain_ticks()
