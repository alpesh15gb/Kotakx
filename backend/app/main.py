from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.auth import (
    LoginRateLimiter,
    auth_configured,
    clear_session_cookie,
    create_session_token,
    origin_allowed,
    password_matches,
    request_authenticated,
    require_websocket_session,
    set_session_cookie,
)
from app.broker.base import BrokerAdapter
from app.broker.kotak_adapter import KotakNeoBroker
from app.broker.paper_simulator import PaperBroker
from app.config import settings
from app.db import Database
from app.models import (
    AccessLoginRequest,
    AlertCreateRequest,
    BasketOrderRequest,
    HotExitRequest,
    KillSwitchState,
    ModifyOrderRequest,
    OrderDefaults,
    PlaceOrderRequest,
    PnlProtectConfig,
    PositionConvertRequest,
    PositionExitRequest,
    QuickOrderRequest,
    TotpLoginRequest,
    WatchlistUpdateRequest,
)
from app.services.candles import CandleService, compute_indicators
from app.services.option_chain import build_option_chain, parse_expiry, time_to_expiry_days
from app.services.risk_engine import BasketExecutionError, OrderRequestError, RiskEngine
from app.services.scanners import SCANNERS, create_alert, evaluate_alerts, run_scanner
from app.ws import MarketFeedHub, OrderFeedHub


logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)
login_limiter = LoginRateLimiter(settings.login_rate_limit, settings.login_rate_window_seconds)


class TerminalState:
    def __init__(self, db_path: str | None = None) -> None:
        self.db = Database(db_path or settings.db_path)
        self.paper_broker = PaperBroker()
        self.kotak_broker = KotakNeoBroker(environment=settings.kotak_environment)
        self.active_broker: BrokerAdapter = self.paper_broker
        self.candles = CandleService(self.db)
        self.risk = RiskEngine(self.db)
        self.market_hub = MarketFeedHub()
        self.order_hub = OrderFeedHub()
        self.candle_tokens: set[str] = set()
        self.last_tick_volumes: dict[tuple[str, str], int] = {}
        self.next_pnl_check = 0.0
        self.trade_lock = threading.Lock()

    def run_trade(self, broker: BrokerAdapter, action: Any, *args: Any) -> Any:
        with self.trade_lock:
            if broker is not self.active_broker:
                raise OrderRequestError("Broker mode changed. Refresh the terminal before submitting again.")
            return action(*args)


state = TerminalState()


async def _market_loop(app_state: TerminalState) -> None:
    while True:
        try:
            broker = app_state.active_broker
            ticks = await asyncio.to_thread(broker.step_ticks)
            if broker is not app_state.active_broker:
                continue
            volume_deltas: dict[str, int] = {}
            for quote in ticks:
                key = (broker.mode, quote.instrument_token)
                previous = app_state.last_tick_volumes.get(key)
                if previous is not None:
                    volume_deltas[quote.instrument_token] = max(0, quote.volume - previous)
                app_state.last_tick_volumes[key] = quote.volume
            if ticks:
                by_tok = {q.instrument_token: q for q in ticks}
                watched = set(app_state.candle_tokens)
                for subs in app_state.market_hub.subscriptions.values():
                    watched.update(subs)
                for token in watched:
                    if token in by_tok:
                        app_state.candles.update_with_tick(
                            token,
                            by_tok[token].ltp,
                            volume_delta=volume_deltas.get(token, 0),
                            synthetic=broker.mode == "paper",
                        )
                await app_state.market_hub.broadcast_ticks({k: v.model_dump() for k, v in by_tok.items()})
                triggered_alerts = evaluate_alerts(app_state.db, by_tok)
                for alert in triggered_alerts:
                    await app_state.order_hub.broadcast("alert_triggered", alert.model_dump())

            if isinstance(broker, KotakNeoBroker):
                for event in broker.drain_order_events():
                    await app_state.order_hub.broadcast("broker_order_update", event)
            now = time.monotonic()
            if now >= app_state.next_pnl_check:
                app_state.next_pnl_check = now + settings.pnl_check_interval_ms / 1000.0
                pnl_event = await asyncio.to_thread(app_state.run_trade, broker, app_state.risk.evaluate_pnl_protect, broker)
                if pnl_event:
                    await app_state.order_hub.broadcast("pnl_protect_triggered", pnl_event)
        except Exception:
            logger.exception("Market loop iteration failed")
        await asyncio.sleep(settings.tick_interval_ms / 1000.0)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_market_loop(state))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title=settings.app_name, version="2.0.0", lifespan=lifespan)


@app.middleware("http")
async def access_control(request: Request, call_next: Any) -> Response:
    if request.url.path in {"/health", "/api/health", "/api/v1/auth/status", "/api/v1/auth/login"}:
        return await call_next(request)
    if not origin_allowed(request):
        return Response(status_code=403, content='{"detail":"Origin is not allowed"}', media_type="application/json")
    if auth_configured() and not request_authenticated(request):
        return Response(status_code=401, content='{"detail":"Authentication required"}', media_type="application/json")
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok"}


@app.get("/api/v1/egress-ip")
async def egress_ip() -> dict[str, Any]:
    """Reports the server's outbound public IPv4 (to whitelist as Static IP in Kotak Neo Trade API)."""
    if settings.public_static_ip:
        return {"ok": True, "ip": settings.public_static_ip, "source": "configured"}
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get("https://api.ipify.org?format=json")
            ip = resp.json().get("ip", "")
            return {"ok": True, "ip": ip, "source": "ipify"}
    except Exception as exc:
        return {"ok": False, "ip": "", "error": str(exc)}


# --- Auth Endpoints ---
@app.get("/api/v1/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    configured = auth_configured()
    return {"authenticated": not configured or request_authenticated(request), "auth_configured": configured}


@app.post("/api/v1/auth/login")
def access_login(req: AccessLoginRequest, request: Request, response: Response) -> dict[str, Any]:
    if not origin_allowed(request):
        raise HTTPException(status_code=403, detail="Origin is not allowed")
    if not auth_configured():
        raise HTTPException(status_code=400, detail="Application access authentication is not configured")
    key = request.client.host if request.client else "unknown"
    if not login_limiter.allow(key):
        raise HTTPException(status_code=429, detail="Too many login attempts; try again later")
    if not password_matches(req.password):
        login_limiter.record_failure(key)
        raise HTTPException(status_code=401, detail="Invalid access password")
    login_limiter.reset(key)
    set_session_cookie(response, create_session_token())
    return {"ok": True}


@app.post("/api/v1/auth/logout")
def access_logout(response: Response) -> dict[str, Any]:
    clear_session_cookie(response)
    return {"ok": True}


@app.post("/api/v1/login")
def login(req: TotpLoginRequest, request: Request) -> dict[str, Any]:
    key = request.client.host if request.client else "unknown"
    if not login_limiter.allow(key):
        raise HTTPException(status_code=429, detail="Too many login attempts; try again later")
    try:
        with state.trade_lock:
            if req.mode == "kotak":
                session = state.kotak_broker.login(req)
                state.active_broker = state.kotak_broker
                subscriptions = set().union(*state.market_hub.subscriptions.values()) if state.market_hub.subscriptions else set()
                if subscriptions:
                    state.kotak_broker.subscribe_tokens(sorted(subscriptions), replace=True)
            else:
                if state.active_broker is state.kotak_broker:
                    state.kotak_broker.logout()
                session = state.paper_broker.login(req)
                state.active_broker = state.paper_broker
        login_limiter.reset(key)
        return {"ok": True, "data": session.model_dump()}
    except Exception as exc:
        login_limiter.record_failure(key)
        logger.warning("Broker login failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/logout")
def logout() -> dict[str, Any]:
    with state.trade_lock:
        res = state.active_broker.logout()
        state.active_broker = state.paper_broker
    return {"ok": True, "data": res}


@app.get("/api/v1/session")
def session_info() -> dict[str, Any]:
    return {
        "ok": True,
        "data": {
            "mode": state.active_broker.mode,
            "ucc": getattr(state.active_broker, "ucc", "DEMO01"),
            "authenticated": getattr(state.active_broker, "authenticated", True),
            "two_fa_verified": getattr(state.active_broker, "two_fa_verified", True),
        },
    }


# --- Instruments, Quotes, Candles, Option Chain ---
@app.get("/api/v1/instruments")
def search_instruments(q: str = "", segment: str | None = None) -> dict[str, Any]:
    items = state.active_broker.list_instruments(q, segment)
    return {"ok": True, "data": [i.model_dump() for i in items]}


@app.post("/api/v1/quotes")
def get_quotes(payload: dict[str, list[str]]) -> dict[str, Any]:
    ids = payload.get("ids") or []
    quotes = state.active_broker.get_quotes(ids)
    return {"ok": True, "quotes": [q.model_dump() for q in quotes]}


@app.get("/api/v1/candles")
def get_candles(
    instrument: str = Query(min_length=1, max_length=100),
    timeframe: str = "1m",
    limit: int = Query(default=160, ge=10, le=500),
    indicators: str = "",
) -> dict[str, Any]:
    state.candle_tokens.add(instrument)
    paper = state.active_broker.mode == "paper"
    if paper:
        quotes = state.active_broker.get_quotes([instrument])
        if not quotes:
            raise HTTPException(status_code=404, detail="Instrument not found")
        bars = state.candles.ensure_history(instrument, quotes[0].ltp, timeframe=timeframe, count=limit, allow_synthetic=True)
    else:
        if timeframe not in ("1m", "3m", "5m", "10m", "15m", "1h", "1D"):
            raise HTTPException(status_code=422, detail="Unsupported live candle timeframe")
        try:
            bars = state.kotak_broker.historical_candles(instrument, timeframe, limit)
        except Exception as exc:
            logger.exception("Live historical candle request failed")
            raise HTTPException(status_code=502, detail="Live historical candles are unavailable") from exc
        if bars:
            state.candles.upsert_candles(instrument, timeframe, bars, synthetic=False)
        bars = state.candles.get_candles(instrument, timeframe, limit=limit, synthetic=False)
    ind_list = [i.strip() for i in indicators.split(",") if i.strip()]
    try:
        ind_data = compute_indicators(bars, ind_list) if ind_list else {}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "instrument": instrument, "timeframe": timeframe, "candles": bars, "indicators": ind_data, "synthetic": paper}


@app.get("/api/v1/optchain-data/{underlying}")
def get_option_chain(underlying: str, expiry: str | None = None) -> dict[str, Any]:
    u = underlying.upper()
    spot_tokens = {
        "NIFTY": "NIFTY 50",
        "BANKNIFTY": "NIFTY BANK",
        "FINNIFTY": "NIFTY FIN SERVICE",
        "MIDCPNIFTY": "NIFTY MID SELECT",
    }
    spot_tok = spot_tokens.get(u, u)
    spot_q = state.active_broker.get_quotes([spot_tok])
    if not spot_q or spot_q[0].ltp <= 0:
        raise HTTPException(status_code=502, detail="A live underlying quote is required to build the option chain")
    spot_price = spot_q[0].ltp
    instruments = state.active_broker.list_instruments(u, exchange_segment="nse_fo", limit=None)
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    opts = [
        i
        for i in instruments
        if i.instrument_type in ("CE", "PE")
        and (i.underlying or "").upper() == u
        and i.strike_price is not None
        and i.expiry
        and (parse_expiry(i.expiry) or today) >= today
        and time_to_expiry_days(i.expiry) > 0
    ]
    expiries = sorted({i.expiry for i in opts if i.expiry}, key=lambda value: parse_expiry(value) or datetime.max.date())
    if not expiries:
        raise HTTPException(status_code=502, detail="No valid unexpired option contracts are available")
    if expiry and expiry not in expiries:
        raise HTTPException(status_code=400, detail="Requested expiry is not available")
    selected_exp = expiry or (expiries[0] if expiries else None)
    exp_opts = [i for i in opts if i.expiry == selected_exp] if selected_exp else opts
    strikes_sorted = sorted({i.strike_price for i in exp_opts if i.strike_price is not None})
    if len(strikes_sorted) > 30:
        atm_idx = min(range(len(strikes_sorted)), key=lambda idx: abs(strikes_sorted[idx] - spot_price))
        window = set(strikes_sorted[max(0, atm_idx - 15) : min(len(strikes_sorted), atm_idx + 16)])
        exp_opts = [i for i in exp_opts if i.strike_price in window]
    tokens = [i.instrument_token for i in exp_opts]
    quotes = {q.instrument_token: q for q in state.active_broker.get_quotes(tokens)}
    chain = build_option_chain(u, spot_price, exp_opts, quotes, requested_expiry=selected_exp)
    return {"ok": True, "data": chain.model_dump()}


# --- Orders, Basket, Margin, Book, Trades ---
@app.post("/api/orders/v1/orders")
async def place_order(req: PlaceOrderRequest) -> dict[str, Any]:
    broker = state.active_broker

    def submit() -> dict[str, Any]:
        def place() -> dict[str, Any]:
            state.risk.assert_order_allowed(broker, req)
            return {"ok": True, "data": broker.place_order(req).model_dump()}

        key = f"order:{req.client_order_id}" if req.client_order_id else ""
        return state.risk.submit_idempotent(broker, req.model_dump(), key, place)

    try:
        result = await asyncio.to_thread(state.run_trade, broker, submit)
        await state.order_hub.broadcast("order_update", result["data"])
        return result
    except OrderRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Order submission failed")
        raise HTTPException(status_code=502, detail="Order submission was not confirmed. Check the order book before retrying.") from exc


@app.post("/api/orders/v1/quick")
async def place_quick_order(req: QuickOrderRequest) -> dict[str, Any]:
    broker = state.active_broker
    try:
        def submit() -> dict[str, Any]:
            def place() -> dict[str, Any]:
                order = state.risk.place_quick_order(broker, req)
                return {"ok": True, "data": order.model_dump()}

            key = f"quick:{req.client_order_id}" if req.client_order_id else ""
            return state.risk.submit_idempotent(broker, req.model_dump(), key, place)

        result = await asyncio.to_thread(state.run_trade, broker, submit)
        await state.order_hub.broadcast("order_update", result["data"])
        return result
    except OrderRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Quick order submission failed")
        raise HTTPException(status_code=502, detail="Quick order was not confirmed. Check the order book before retrying.") from exc


@app.put("/api/orders/v1/orders")
async def modify_order(req: ModifyOrderRequest) -> dict[str, Any]:
    broker = state.active_broker

    def modify():
        if state.db.get_kill_switch().active:
            raise PermissionError("Kill Switch is ACTIVE. Order modifications are blocked; only exits and cancellations are permitted.")
        state.risk.validate_modification(broker, req)
        return broker.modify_order(req)

    try:
        order = await asyncio.to_thread(state.run_trade, broker, modify)
        await state.order_hub.broadcast("order_update", order.model_dump())
        return {"ok": True, "data": order.model_dump()}
    except OrderRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Order modification was not confirmed")
        raise HTTPException(status_code=502, detail="Order modification was not confirmed. Check order history before retrying.") from exc


@app.delete("/api/orders/v1/orders/{order_id}")
async def cancel_order(order_id: str) -> dict[str, Any]:
    broker = state.active_broker
    try:
        res = await asyncio.to_thread(state.run_trade, broker, broker.cancel_order, order_id)
        await state.order_hub.broadcast("order_cancelled", res)
        return {"ok": True, "data": res}
    except OrderRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Order cancellation was not confirmed")
        raise HTTPException(status_code=502, detail="Order cancellation was not confirmed. Check order history before retrying.") from exc


@app.post("/api/orders/v1/orders/bulk-cancel")
async def bulk_cancel(payload: dict[str, list[str]] | None = None) -> dict[str, Any]:
    broker = state.active_broker
    ids = (payload or {}).get("order_ids")
    try:
        res = await asyncio.to_thread(state.run_trade, broker, state.risk.bulk_cancel, broker, ids)
    except OrderRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Bulk cancellation was not confirmed")
        raise HTTPException(status_code=502, detail="Bulk cancellation was not fully confirmed. Check the order book before retrying.") from exc
    await state.order_hub.broadcast("bulk_cancel", res)
    return {"ok": True, "data": res}


@app.post("/api/orders/v1/orders/basket")
async def place_basket(req: BasketOrderRequest) -> dict[str, Any]:
    broker = state.active_broker
    try:
        orders = [order.model_copy(deep=True) for order in req.orders]
        if req.client_order_id:
            for index, order in enumerate(orders):
                canonical_leg = json.dumps(order.model_dump(), sort_keys=True, separators=(",", ":"))
                leg_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{req.client_order_id}:{index}:{canonical_leg}").hex
                order.client_order_id = leg_id
                order.tag = leg_id
        prepared = BasketOrderRequest(sequence=req.sequence, orders=orders, client_order_id=req.client_order_id)

        def submit() -> dict[str, Any]:
            def place() -> dict[str, Any]:
                try:
                    placed = state.risk.place_basket(broker, prepared)
                except BasketExecutionError as exc:
                    return {"ok": False, "partial": True, "message": str(exc), "data": [o.model_dump() for o in exc.placed]}
                return {"ok": True, "data": [o.model_dump() for o in placed]}

            key = f"basket:{req.client_order_id}" if req.client_order_id else ""
            return state.risk.submit_idempotent(broker, prepared.model_dump(), key, place)

        result = await asyncio.to_thread(state.run_trade, broker, submit)
        event = "basket_placed" if result["ok"] else "basket_partial"
        await state.order_hub.broadcast(event, result["data"])
        return result
    except OrderRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Basket submission failed")
        raise HTTPException(status_code=502, detail="Basket was not confirmed. Check the order book before retrying.") from exc


@app.post("/api/orders/v1/margin")
def calc_margin(req: PlaceOrderRequest) -> dict[str, Any]:
    return {"ok": True, "data": state.active_broker.margin_required(req)}


@app.post("/api/orders/v1/margin/basket")
def calc_basket_margin(req: BasketOrderRequest) -> dict[str, Any]:
    return {"ok": True, "data": state.risk.basket_margin(state.active_broker, req)}


@app.get("/api/orders/v1/orderbook")
def order_book() -> dict[str, Any]:
    return {"ok": True, "data": [o.model_dump() for o in state.active_broker.order_book()]}


@app.get("/api/orders/v1/order-history/{order_id}")
def order_history(order_id: str) -> dict[str, Any]:
    return {"ok": True, "data": state.active_broker.order_history(order_id)}


@app.get("/api/orders/v1/trades")
def trade_book(order_id: str | None = None) -> dict[str, Any]:
    return {"ok": True, "data": [t.model_dump() for t in state.active_broker.trade_book(order_id)]}


# --- Positions, Exit, Holdings, Limits ---
@app.get("/api/orders/v1/positions")
def positions() -> dict[str, Any]:
    try:
        pos_list = state.active_broker.positions()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    total_pnl = round(sum(p.pnl for p in pos_list), 2)
    return {"ok": True, "total_pnl": total_pnl, "positions": [p.model_dump() for p in pos_list]}


@app.post("/api/orders/v1/positions/exit-order")
async def exit_position(req: PositionExitRequest) -> dict[str, Any]:
    broker = state.active_broker
    try:
        order_req = PlaceOrderRequest(
            instrument_token=req.instrument_token,
            exchange_segment=req.exchange_segment,
            product=req.product,
            price="0",
            order_type="MKT",
            quantity=str(req.quantity),
            validity="DAY",
            trading_symbol=req.trading_symbol,
            transaction_type=req.transaction_type,
            tag="position-exit",
            client_order_id=req.client_order_id,
        )

        def submit() -> dict[str, Any]:
            def place() -> dict[str, Any]:
                state.risk.validate_exit_request(broker, req)
                order = broker.place_order(order_req)
                return {"ok": True, "order_id": order.order_id, "data": order.model_dump()}

            key = f"exit:{req.client_order_id}" if req.client_order_id else ""
            return state.risk.submit_idempotent(broker, req.model_dump(), key, place)

        result = await asyncio.to_thread(state.run_trade, broker, submit)
        await state.order_hub.broadcast("order_update", result["data"])
        return result
    except OrderRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Position exit failed")
        raise HTTPException(status_code=502, detail="Position exit was not confirmed; check the order book before retrying") from exc


@app.post("/api/orders/v1/exit-orders")
async def hot_exit_all(req: HotExitRequest) -> dict[str, Any]:
    broker = state.active_broker

    def submit() -> dict[str, Any]:
        def exit_all() -> dict[str, Any]:
            orders = state.risk.exit_all_positions(broker, client_order_id=req.client_order_id)
            return {
                "ok": True,
                "order_ids": [order.order_id for order in orders],
                "data": [order.model_dump() for order in orders],
            }

        key = f"hot-exit:{req.client_order_id}" if req.client_order_id else ""
        return state.risk.submit_idempotent(broker, req.model_dump(), key, exit_all)

    try:
        result = await asyncio.to_thread(state.run_trade, broker, submit)
        await state.order_hub.broadcast("hot_exit", result["order_ids"])
        return result
    except OrderRequestError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Hot Exit failed")
        raise HTTPException(status_code=502, detail="Hot Exit was not fully confirmed. Reconcile positions and the order book before retrying.") from exc


@app.post("/api/orders/v1/positions/convert")
def convert_position(req: PositionConvertRequest) -> dict[str, Any]:
    try:
        return {"ok": True, "data": state.active_broker.convert_position(req)}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/orders/v1/holdings")
def holdings() -> dict[str, Any]:
    items = state.active_broker.holdings()
    total_val = round(sum(h.mkt_value for h in items), 2)
    total_pnl = round(sum(h.pnl for h in items), 2)
    return {"ok": True, "total_value": total_val, "total_pnl": total_pnl, "data": [h.model_dump() for h in items]}


@app.get("/api/v1/limits")
def limits() -> dict[str, Any]:
    return {"ok": True, "data": state.active_broker.limits()}


# --- Sahi-style Risk, Defaults, Watchlists, Scanners, Alerts ---
@app.get("/api/orders/v1/order-defaults")
def get_order_defaults() -> dict[str, Any]:
    return {"ok": True, "data": state.db.get_order_defaults().model_dump()}


@app.put("/api/orders/v1/order-defaults")
def set_order_defaults(req: OrderDefaults) -> dict[str, Any]:
    saved = state.db.set_order_defaults(req)
    return {"ok": True, "data": saved.model_dump()}


@app.get("/api/orders/v1/kill-switch")
def get_kill_switch() -> dict[str, Any]:
    return {"ok": True, "data": state.db.get_kill_switch().model_dump()}


@app.post("/api/orders/v1/kill-switch")
async def set_kill_switch(req: KillSwitchState) -> dict[str, Any]:
    def save() -> KillSwitchState:
        with state.trade_lock:
            return state.db.set_kill_switch(req)

    saved = await asyncio.to_thread(save)
    await state.order_hub.broadcast("kill_switch", saved.model_dump())
    return {"ok": True, "data": saved.model_dump()}


@app.get("/api/orders/v1/pnl-protects")
def get_pnl_protect() -> dict[str, Any]:
    return {"ok": True, "data": state.db.get_pnl_protect().model_dump()}


@app.put("/api/orders/v1/pnl-protects")
def set_pnl_protect(req: PnlProtectConfig) -> dict[str, Any]:
    saved = state.db.set_pnl_protect(req)
    return {"ok": True, "data": saved.model_dump()}


@app.get("/api/v1/watchlists")
def list_watchlists() -> dict[str, Any]:
    wls = state.db.list_watchlists()
    for wl in wls:
        wl["quotes"] = [q.model_dump() for q in state.active_broker.get_quotes(wl["tokens"])]
    return {"ok": True, "data": wls}


@app.put("/api/v1/watchlists/{wl_id}")
def update_watchlist(wl_id: str, req: WatchlistUpdateRequest) -> dict[str, Any]:
    saved = state.db.upsert_watchlist(wl_id, req.name, req.tokens)
    return {"ok": True, "data": saved}


@app.get("/api/v1/scanners")
def list_scanners() -> dict[str, Any]:
    return {"ok": True, "data": SCANNERS}


@app.get("/api/v1/scanners/{scanner_id}/results")
def scanner_results(scanner_id: str, segment: str | None = None) -> dict[str, Any]:
    if scanner_id not in {scanner["id"] for scanner in SCANNERS}:
        raise HTTPException(status_code=404, detail="Scanner not found")
    return {"ok": True, "instruments": run_scanner(state.active_broker, scanner_id, segment)}


@app.get("/api/alerts/v1/alerts")
def list_alerts() -> dict[str, Any]:
    return {"ok": True, "data": [a.model_dump() for a in state.db.list_alerts()]}


@app.post("/api/alerts/v1/alerts")
def add_alert(req: AlertCreateRequest) -> dict[str, Any]:
    return {"ok": True, "data": create_alert(state.db, req).model_dump()}


@app.delete("/api/alerts/v1/alerts/{alert_id}")
def remove_alert(alert_id: str) -> dict[str, Any]:
    deleted = state.db.delete_alert(alert_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"ok": True}


# --- WebSocket Streams ---
@app.websocket("/ws/marketfeed")
async def ws_marketfeed(ws: WebSocket) -> None:
    if not require_websocket_session(ws):
        await ws.close(code=1008)
        return
    await state.market_hub.connect(ws)
    try:
        subs = sorted(state.market_hub.subscriptions.get(ws, set()))
        if state.active_broker.mode == "kotak":
            await asyncio.to_thread(state.kotak_broker.subscribe_tokens, subs)
        snap = await asyncio.to_thread(state.active_broker.get_quotes, subs)
        await ws.send_text(json.dumps({"type": "snapshot", "data": [q.model_dump() for q in snap]}))
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60)
            except TimeoutError:
                if not require_websocket_session(ws):
                    await ws.close(code=1008)
                    break
                continue
            if not require_websocket_session(ws):
                await ws.close(code=1008)
                break
            if len(raw) > 16000:
                raise ValueError("Subscription message is too large")
            msg = json.loads(raw)
            if not isinstance(msg, dict):
                raise ValueError("Subscription message must be an object")
            action = msg.get("action", "subscribe")
            tokens = msg.get("tokens") or []
            updated_subs = state.market_hub.update_subscription(ws, action, tokens)
            if state.active_broker.mode == "kotak":
                combined = set().union(*state.market_hub.subscriptions.values())
                await asyncio.to_thread(state.kotak_broker.subscribe_tokens, sorted(combined), True)
            snap = await asyncio.to_thread(state.active_broker.get_quotes, updated_subs)
            await ws.send_text(json.dumps({"type": "snapshot", "subscribed": updated_subs, "data": [q.model_dump() for q in snap]}))
    except WebSocketDisconnect:
        pass
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
    finally:
        state.market_hub.disconnect(ws)
        remaining = set().union(*state.market_hub.subscriptions.values()) if state.market_hub.subscriptions else set()
        if state.active_broker.mode == "kotak":
            await asyncio.to_thread(state.kotak_broker.subscribe_tokens, sorted(remaining), True)


@app.websocket("/ws/orderfeed")
async def ws_orderfeed(ws: WebSocket) -> None:
    if not require_websocket_session(ws):
        await ws.close(code=1008)
        return
    await state.order_hub.connect(ws)
    try:
        while True:
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=60)
            except TimeoutError:
                if not require_websocket_session(ws):
                    await ws.close(code=1008)
                    break
                continue
            if not require_websocket_session(ws):
                await ws.close(code=1008)
                break
    except WebSocketDisconnect:
        pass
    finally:
        state.order_hub.disconnect(ws)
