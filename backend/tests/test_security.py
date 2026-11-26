from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from app import main
from app.auth import LoginRateLimiter, SESSION_COOKIE
from app.broker.kotak_adapter import KotakNeoBroker
from app.broker.paper_simulator import PaperBroker
from app.models import PlaceOrderRequest, QuoteData
from app.services.candles import CandleService, compute_indicators
from app.services.option_chain import build_option_chain, parse_expiry, time_to_expiry_days
from app.services.risk_engine import RiskEngine
from test_terminal_e2e import _reset_state


def _order_payload(client: TestClient) -> dict:
    chain = client.get("/api/v1/optchain-data/NIFTY").json()["data"]
    strike = next(row for row in chain["strikes"] if row["is_atm"])
    return {
        "instrument_token": strike["call_token"],
        "exchange_segment": "nse_fo",
        "trading_symbol": strike["call_symbol"],
        "transaction_type": "B",
        "quantity": "75",
        "product": "MIS",
    }


def test_access_authentication_origin_and_websocket(tmp_path, monkeypatch):
    client = _reset_state(tmp_path)
    monkeypatch.setattr(main, "auth_configured", lambda: True)
    monkeypatch.setattr(main, "request_authenticated", lambda request: request.cookies.get(SESSION_COOKIE) == "valid")
    monkeypatch.setattr(main, "password_matches", lambda password: password == "correct")
    monkeypatch.setattr(main, "require_websocket_session", lambda ws: ws.cookies.get(SESSION_COOKIE) == "valid")

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/orders/v1/positions").status_code == 401
    assert client.post("/api/v1/auth/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"password": "correct"}, headers={"Origin": "https://evil.example"}).status_code == 403
    login = client.post("/api/v1/auth/login", json={"password": "correct"})
    assert login.status_code == 200
    assert SESSION_COOKIE in login.cookies
    assert client.get("/api/v1/session").status_code == 401

    original_connect = main.state.order_hub.connect
    connect = AsyncMock()
    monkeypatch.setattr(main.state.order_hub, "connect", connect)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/orderfeed"):
            pass
    connect.assert_not_awaited()
    monkeypatch.setattr(main.state.order_hub, "connect", original_connect)
    client.cookies.set(SESSION_COOKIE, "valid")
    assert client.get("/api/orders/v1/positions").status_code == 200
    assert client.get("/api/v1/session", headers={"Origin": "https://evil.example"}).status_code == 403
    with client.websocket_connect("/ws/orderfeed"):
        pass


def test_login_rate_limiter():
    limiter = LoginRateLimiter(2, 60)
    assert limiter.allow("ip")
    limiter.record_failure("ip")
    assert limiter.allow("ip")
    limiter.record_failure("ip")
    assert not limiter.allow("ip")
    limiter.reset("ip")
    assert limiter.allow("ip")


def test_idempotent_order_replay_and_conflict(tmp_path):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    payload["client_order_id"] = "idempotency-test-001"
    first = client.post("/api/orders/v1/orders", json=payload)
    assert first.status_code == 200
    replay = client.post("/api/orders/v1/orders", json=payload)
    assert replay.json()["data"]["order_id"] == first.json()["data"]["order_id"]
    assert len(client.get("/api/orders/v1/orderbook").json()["data"]) == 1
    payload["quantity"] = "150"
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 409


def test_uncertain_live_submission_is_never_retried(tmp_path, monkeypatch):
    client = _reset_state(tmp_path)
    broker = PaperBroker()
    broker.mode = "kotak"
    monkeypatch.setattr(broker, "place_order", Mock(side_effect=TimeoutError("ambiguous transport failure")))
    main.state.active_broker = broker
    payload = _order_payload(client)
    payload["client_order_id"] = "uncertain-order-001"
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 502
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 409
    payload["client_order_id"] = "uncertain-order-002"
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 502
    payload.pop("client_order_id")
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 400


def test_exit_validation_and_one_tap_enforcement(tmp_path):
    client = _reset_state(tmp_path)
    chain = client.get("/api/v1/optchain-data/NIFTY").json()["data"]
    strike = next(row for row in chain["strikes"] if row["is_atm"])
    exit_payload = {
        "instrument_token": strike["call_token"], "exchange_segment": "nse_fo",
        "trading_symbol": strike["call_symbol"], "product": "MIS", "quantity": 75, "transaction_type": "S",
    }
    assert client.post("/api/orders/v1/positions/exit-order", json=exit_payload).status_code == 400
    client.put("/api/orders/v1/order-defaults", json={"one_tap_enabled": False})
    quick = {key: exit_payload[key] for key in ("instrument_token", "exchange_segment", "trading_symbol")}
    quick["transaction_type"] = "B"
    assert client.post("/api/orders/v1/quick", json=quick).status_code == 403
    client.put("/api/orders/v1/order-defaults", json={"one_tap_enabled": True})
    assert client.post("/api/orders/v1/quick", json=quick).status_code == 200
    exit_payload["transaction_type"] = "B"
    assert client.post("/api/orders/v1/positions/exit-order", json=exit_payload).status_code == 400
    exit_payload["transaction_type"] = "S"
    exit_payload["quantity"] = 150
    assert client.post("/api/orders/v1/positions/exit-order", json=exit_payload).status_code == 400
    exit_payload["quantity"] = 75
    assert client.post("/api/orders/v1/positions/exit-order", json=exit_payload).status_code == 200


def test_kill_switch_blocks_entries_but_allows_exit(tmp_path):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 200
    client.post("/api/orders/v1/kill-switch", json={"active": True})
    payload["client_order_id"] = "blocked-entry-0001"
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 403
    payload["transaction_type"] = "S"
    payload["client_order_id"] = "allowed-exit-0001"
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 200


@pytest.mark.parametrize("changes", [
    {"quantity": "0"}, {"quantity": "-1"}, {"price": "-1"}, {"product": "CO"}, {"product": "BO"},
    {"order_type": "L", "price": "0"}, {"order_type": "SL", "trigger_price": "0"},
])
def test_order_input_validation(tmp_path, changes):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    payload.update(changes)
    expected = 400 if "order_type" in changes else 422
    assert client.post("/api/orders/v1/orders", json=payload).status_code == expected


def test_synthetic_candles_are_excluded_from_live_reads(tmp_path):
    db = main.TerminalState(str(tmp_path / "candles.db")).db
    service = CandleService(db)
    real = {"time": 100, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}
    db.upsert_candles("token", "1m", [real], synthetic=False)
    assert service.ensure_history("token", 2, count=10) == [real]
    generated = service.ensure_history("token", 2, count=50, allow_synthetic=True)
    assert len(generated) == 50
    assert db.get_candles("token", "1m", synthetic=False) == [real]
    assert len(db.get_candles("token", "1m", synthetic=True)) == 50


def test_expiry_order_and_time_to_expiry():
    ist = timezone(timedelta(hours=5, minutes=30))
    first_date = datetime.now(ist).date() + timedelta(days=2)
    second_date = first_date + timedelta(days=7)
    first_expiry = first_date.strftime("%d%b%Y").upper()
    second_expiry = second_date.strftime("%d%b%Y").upper()
    assert parse_expiry(first_expiry) < parse_expiry(second_expiry)
    assert 0 < time_to_expiry_days(first_expiry) < 4
    from app.models import Instrument
    instruments = [
        Instrument(instrument_token=f"{expiry}-{side}", exchange_segment="nse_fo", trading_symbol=f"{expiry}-{side}", symbol="NIFTY", name="NIFTY", instrument_type=side, expiry=expiry, strike_price=25700, option_type=side, lot_size=75, underlying="NIFTY")
        for expiry in (second_expiry, first_expiry) for side in ("CE", "PE")
    ]
    chain = build_option_chain("NIFTY", 25750, instruments, {})
    assert chain.available_expiries == [first_expiry, second_expiry]
    assert chain.expiry == first_expiry
    assert chain.strikes[0].call_iv == 0


def test_v3_place_order_uses_supported_fields_and_requires_ack():
    broker = KotakNeoBroker()
    broker.two_fa_verified = True
    broker.client = Mock()
    broker._throttle = lambda kind: None
    req = PlaceOrderRequest(quantity="75", trading_symbol="NIFTY01DEC2625700CE", transaction_type="B", client_order_id="client-id-123456")
    broker.client.place_order.return_value = {"stat": "Ok", "nOrdNo": "123"}
    order = broker.place_order(req)
    assert order.order_id == "123"
    kwargs = broker.client.place_order.call_args.kwargs
    assert kwargs["tag"] == "client-id-123456"
    assert "scrip_token" not in kwargs and "market_protection" not in kwargs
    broker.client.place_order.return_value = {"stat": "Ok"}
    with pytest.raises(RuntimeError, match="did not confirm"):
        broker.place_order(req)


def test_sdk_wrapped_timeout_preserves_live_order_reservation(tmp_path):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    payload["client_order_id"] = "sdk-timeout-001"
    broker = KotakNeoBroker()
    broker.two_fa_verified = True
    broker.client = Mock()
    broker.client.place_order.return_value = {"Error": TimeoutError("transport timed out")}
    broker.list_instruments = main.state.paper_broker.list_instruments
    main.state.active_broker = broker

    assert client.post("/api/orders/v1/orders", json=payload).status_code == 502
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 409
    broker.client.place_order.assert_called_once()


def test_quick_order_replay_survives_changed_defaults(tmp_path):
    client = _reset_state(tmp_path)
    client.put("/api/orders/v1/order-defaults", json={"one_tap_enabled": True})
    payload = _order_payload(client)
    payload.pop("quantity")
    payload["client_order_id"] = "quick-replay-001"
    first = client.post("/api/orders/v1/quick", json=payload)
    assert first.status_code == 200

    client.put("/api/orders/v1/order-defaults", json={"one_tap_enabled": False, "default_lots": 2})
    assert client.post("/api/orders/v1/quick", json=payload).json() == first.json()
    assert len(client.get("/api/orders/v1/orderbook").json()["data"]) == 1


def test_quote_batches_and_errors():
    broker = KotakNeoBroker()
    broker.two_fa_verified = True
    broker.client = Mock()
    broker._throttle = lambda kind: None
    broker.client.quotes.return_value = {"data": []}
    broker.get_quotes([str(index) for index in range(51)])
    assert [len(call.kwargs["instrument_tokens"]) for call in broker.client.quotes.call_args_list] == [50, 1]
    broker.client.quotes.return_value = {"error": [{"message": "rate limited"}]}
    with pytest.raises(RuntimeError, match="rate limited"):
        broker.get_quotes(["1"])


def test_hot_exit_replay_and_live_request_id(tmp_path):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 200
    hot_exit = {"client_order_id": "hot-exit-test-001"}
    first = client.post("/api/orders/v1/exit-orders", json=hot_exit)
    assert first.status_code == 200
    replay = client.post("/api/orders/v1/exit-orders", json=hot_exit)
    assert replay.json()["order_ids"] == first.json()["order_ids"]
    assert len(client.get("/api/orders/v1/orderbook").json()["data"]) == 2
    assert client.get("/api/orders/v1/positions").json()["positions"][0]["net_qty"] == 0

    live_broker = PaperBroker()
    live_broker.mode = "kotak"
    main.state.active_broker = live_broker
    assert client.post("/api/orders/v1/exit-orders", json={}).status_code == 400


def test_hot_exit_waits_for_pending_order_and_uses_post_cancel_positions(tmp_path, monkeypatch):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    payload.update(order_type="L", price="0.01")
    broker = main.state.paper_broker
    pending = broker.place_order(PlaceOrderRequest(**payload)).model_copy(update={"status": "trigger pending"})
    filled = pending.model_copy(update={"status": "complete"})
    broker.mode = "kotak"
    book = Mock(side_effect=[[pending], [pending], [filled]])
    monkeypatch.setattr(broker, "order_book", book)

    def positions_after_cancel(require_ltp=True):
        assert book.call_count == 3
        return []

    positions = Mock(side_effect=positions_after_cancel)
    cancel = Mock()
    monkeypatch.setattr(broker, "positions", positions)
    monkeypatch.setattr(broker, "cancel_order", cancel)

    assert main.state.risk.exit_all_positions(broker, "hot-exit-cancel-001") == []
    cancel.assert_called_once_with(pending.order_id)
    positions.assert_called_once_with(require_ltp=False)


def test_position_exit_replays_after_position_closes(tmp_path):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 200
    position = client.get("/api/orders/v1/positions").json()["positions"][0]
    exit_payload = {
        "instrument_token": position["instrument_token"],
        "exchange_segment": position["exchange_segment"],
        "trading_symbol": position["trading_symbol"],
        "product": position["product"],
        "quantity": abs(position["net_qty"]),
        "transaction_type": "S",
        "client_order_id": "position-exit-test-001",
    }
    first = client.post("/api/orders/v1/positions/exit-order", json=exit_payload)
    assert first.status_code == 200
    replay = client.post("/api/orders/v1/positions/exit-order", json=exit_payload)
    assert replay.json()["order_id"] == first.json()["order_id"]
    assert len(client.get("/api/orders/v1/orderbook").json()["data"]) == 2


def test_position_exit_accounts_for_outstanding_sell(tmp_path):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 200
    sell = {**payload, "transaction_type": "S"}
    pending = client.post("/api/orders/v1/orders", json={**sell, "order_type": "L", "price": "10000000"})
    assert pending.json()["data"]["status"] == "open"
    response = client.post("/api/orders/v1/positions/exit-order", json={**sell, "quantity": 75})
    assert response.status_code == 403
    assert len(client.get("/api/orders/v1/orderbook").json()["data"]) == 2


def test_basket_long_id_and_identical_legs_are_collision_safe(tmp_path):
    client = _reset_state(tmp_path)
    leg = _order_payload(client)
    request_id = "basket-" + "x" * 93
    payload = {"sequence": "Sequential", "client_order_id": request_id, "orders": [leg, dict(leg)]}
    first = client.post("/api/orders/v1/orders/basket", json=payload)
    assert first.status_code == 200
    orders = first.json()["data"]
    assert len(orders) == 2
    assert len({order["tag"] for order in orders}) == 2
    assert all(len(order["tag"]) == 32 for order in orders)
    replay = client.post("/api/orders/v1/orders/basket", json=payload)
    assert [order["order_id"] for order in replay.json()["data"]] == [order["order_id"] for order in orders]
    assert len(client.get("/api/orders/v1/orderbook").json()["data"]) == 2


def test_modification_validation_and_uncertain_broker_results(tmp_path, monkeypatch):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    payload.update({"order_type": "L", "price": "0.01"})
    order_id = client.post("/api/orders/v1/orders", json=payload).json()["data"]["order_id"]
    modification = {"order_id": order_id, "order_type": "L", "price": "0.01", "quantity": "76"}
    assert client.put("/api/orders/v1/orders", json=modification).status_code == 400
    modification["quantity"] = "75"
    assert client.put("/api/orders/v1/orders", json=modification).status_code == 200
    client.post("/api/orders/v1/kill-switch", json={"active": True})
    assert client.put("/api/orders/v1/orders", json=modification).status_code == 403
    client.post("/api/orders/v1/kill-switch", json={"active": False})
    assert client.delete(f"/api/orders/v1/orders/{order_id}").status_code == 200
    assert client.put("/api/orders/v1/orders", json=modification).status_code == 400

    broker = PaperBroker()
    payload = _order_payload(client)
    payload.update({"order_type": "L", "price": "0.01"})
    pending = broker.place_order(PlaceOrderRequest(**payload))
    broker.mode = "kotak"
    monkeypatch.setattr(broker, "modify_order", Mock(side_effect=TimeoutError("ambiguous")))
    monkeypatch.setattr(broker, "cancel_order", Mock(side_effect=TimeoutError("ambiguous")))
    main.state.active_broker = broker
    modification["order_id"] = pending.order_id
    assert client.put("/api/orders/v1/orders", json=modification).status_code == 502
    assert client.delete(f"/api/orders/v1/orders/{pending.order_id}").status_code == 502


def test_pnl_protect_failure_is_not_retried(tmp_path, monkeypatch):
    client = _reset_state(tmp_path)
    payload = _order_payload(client)
    assert client.post("/api/orders/v1/orders", json=payload).status_code == 200
    client.put("/api/orders/v1/pnl-protects", json={
        "enabled": True, "max_loss": 0, "max_profit": 1000000, "auto_activate_kill_switch": True,
    })
    exit_all = Mock(side_effect=RuntimeError("exit unconfirmed"))
    monkeypatch.setattr(main.state.risk, "exit_all_positions", exit_all)
    event = main.state.risk.evaluate_pnl_protect(main.state.active_broker)
    assert event and event["ok"] is False
    assert main.state.risk.evaluate_pnl_protect(main.state.active_broker) is None
    exit_all.assert_called_once()
    assert client.get("/api/orders/v1/kill-switch").json()["data"]["active"] is True


def test_positions_require_fresh_ltp_and_use_position_segment():
    broker = KotakNeoBroker()
    broker.two_fa_verified = True
    broker.client = Mock()
    broker._throttle = lambda kind: None
    row = {"tok": "45123", "exSeg": "nse_fo", "trdSym": "NIFTYOPT", "prod": "NRML", "cfBuyQty": "75"}
    broker.client.positions.return_value = {"data": [row]}
    broker.client.quotes.return_value = {"data": []}
    with pytest.raises(RuntimeError, match="LTP is unavailable"):
        broker.positions()
    assert broker.client.quotes.call_args.kwargs["instrument_tokens"][0]["exchange_segment"] == "nse_fo"
    positions = broker.positions(require_ltp=False)
    assert positions[0].net_qty == 75


def test_position_metrics_use_fresh_ltp_and_carry_forward_cost():
    broker = KotakNeoBroker()
    broker.two_fa_verified = True
    broker.client = Mock()
    row = {
        "tok": "45123", "exSeg": "nse_fo", "trdSym": "NIFTYOPT", "prod": "NRML", "lotSz": "75",
        "cfBuyQty": "75", "cfSellQty": "0", "cfBuyAmt": "15000", "flBuyQty": "0", "flSellQty": "0",
    }
    broker.client.positions.return_value = {"data": [row]}
    quote = QuoteData(
        instrument_token="45123", exchange_segment="nse_fo", trading_symbol="NIFTYOPT", ltp=250,
        open=200, high=250, low=200, close=200, change=50, change_pct=25, updated_at=1,
    )
    broker.get_quotes = Mock(return_value=[quote])
    position = broker.positions()[0]
    assert position.avg_price == 200
    assert position.pnl == 3750


def test_zero_ticks_do_not_retain_stale_values():
    broker = KotakNeoBroker()
    previous = QuoteData(
        instrument_token="123", exchange_segment="nse_cm", trading_symbol="TEST", ltp=100,
        open=99, high=101, low=98, close=99, change=1, change_pct=1, volume=500, oi=200,
        bid=99, ask=101, bid_qty=10, ask_qty=20, updated_at=1,
    )
    broker._quotes_cache["123"] = previous
    tick = broker._normalize_ws_tick({
        "instrument_token": "123", "last_traded_price": 0, "close_price": 0,
        "net_change": 0, "net_change_percent": 0, "volume_traded_today": 0, "open_interest": 0,
    })
    assert tick is not None
    assert tick.ltp == 0 and tick.close == 0 and tick.change == 0
    assert tick.volume == 0 and tick.oi == 0


def test_indicator_validation_vwap_reset_and_rsi_warmup():
    ist = timezone(timedelta(hours=5, minutes=30))
    first_time = int(datetime(2026, 1, 1, tzinfo=ist).timestamp())
    candles = [
        {"time": first_time, "open": 10, "high": 12, "low": 9, "close": 10, "volume": 10},
        {"time": first_time + 86400, "open": 10, "high": 14, "low": 10, "close": 12, "volume": 10},
    ]
    with pytest.raises(ValueError):
        compute_indicators(candles, ["emaabc"])
    with pytest.raises(ValueError):
        compute_indicators(candles, ["sma0"])
    with pytest.raises(ValueError):
        compute_indicators([], ["unknown"])
    assert compute_indicators(candles, ["vwap"])["vwap"][-1]["value"] == 12.0
    assert compute_indicators(candles, ["rsi"])["rsi"] == []
