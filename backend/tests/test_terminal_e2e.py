from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import TerminalState, app, state


def _reset_state(tmp_path) -> TestClient:
    fresh = TerminalState(db_path=str(tmp_path / "test_terminal.db"))
    state.db = fresh.db
    state.paper_broker = fresh.paper_broker
    state.kotak_broker = fresh.kotak_broker
    state.active_broker = fresh.active_broker
    state.candles = fresh.candles
    state.risk = fresh.risk
    state.market_hub = fresh.market_hub
    state.order_hub = fresh.order_hub
    state.candle_tokens = fresh.candle_tokens
    state.last_tick_volumes = fresh.last_tick_volumes
    state.next_pnl_check = fresh.next_pnl_check
    return TestClient(app)


def test_end_to_end_trading_flow(tmp_path):
    client = _reset_state(tmp_path)

    # 1. Health & Session
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

    # 2. Option Chain + Greeks
    oc = client.get("/api/v1/optchain-data/NIFTY")
    assert oc.status_code == 200
    oc_data = oc.json()["data"]
    assert oc_data["underlying"] == "NIFTY"
    assert len(oc_data["strikes"]) >= 10
    atm = next(s for s in oc_data["strikes"] if s["is_atm"])
    assert atm["call_ltp"] > 0
    assert 0.2 < atm["call_delta"] < 0.8
    assert -0.8 < atm["put_delta"] < -0.2

    # 3. Candles & Indicators
    cd = client.get(f"/api/v1/candles?instrument={atm['call_token']}&timeframe=1m&indicators=ema20,vwap,rsi")
    assert cd.status_code == 200
    cd_json = cd.json()
    assert len(cd_json["candles"]) >= 40
    assert "ema20" in cd_json["indicators"]
    assert "vwap" in cd_json["indicators"]
    assert "rsi" in cd_json["indicators"]

    # 4. 1-Tap Quick Order (uses saved order defaults: 1 lot = 75 qty)
    client.put("/api/orders/v1/order-defaults", json={"one_tap_enabled": True})
    q_ord = client.post(
        "/api/orders/v1/quick",
        json={
            "instrument_token": atm["call_token"],
            "exchange_segment": "nse_fo",
            "trading_symbol": atm["call_symbol"],
            "transaction_type": "B",
        },
    )
    assert q_ord.status_code == 200
    assert q_ord.json()["data"]["status"] == "complete"
    assert q_ord.json()["data"]["quantity"] == 75

    # 5. Positions check
    pos = client.get("/api/orders/v1/positions").json()
    assert len(pos["positions"]) == 1
    assert pos["positions"][0]["net_qty"] == 75

    # 6. Kill Switch blocks new entries, allows exit
    ks = client.post("/api/orders/v1/kill-switch", json={"active": True, "reason": "Manual test"})
    assert ks.status_code == 200
    blocked = client.post(
        "/api/orders/v1/quick",
        json={
            "instrument_token": atm["put_token"],
            "exchange_segment": "nse_fo",
            "trading_symbol": atm["put_symbol"],
            "transaction_type": "B",
        },
    )
    assert blocked.status_code == 403

    # Exit via Hot Exit
    hx = client.post("/api/orders/v1/exit-orders", json={})
    assert hx.status_code == 200
    assert len(hx.json()["order_ids"]) == 1
    pos_after = client.get("/api/orders/v1/positions").json()
    assert pos_after["positions"][0]["net_qty"] == 0

    # Turn off Kill Switch
    client.post("/api/orders/v1/kill-switch", json={"active": False})

    # 7. Basket Margin & Optimised Basket Execution
    basket_payload = {
        "sequence": "Optimised",
        "orders": [
            {
                "exchange_segment": "nse_fo",
                "product": "MIS",
                "order_type": "MKT",
                "quantity": "75",
                "trading_symbol": atm["call_symbol"],
                "transaction_type": "S",
                "instrument_token": atm["call_token"],
            },
            {
                "exchange_segment": "nse_fo",
                "product": "MIS",
                "order_type": "MKT",
                "quantity": "75",
                "trading_symbol": oc_data["strikes"][-1]["call_symbol"],
                "transaction_type": "B",
                "instrument_token": oc_data["strikes"][-1]["call_token"],
            },
        ],
    }
    bm = client.post("/api/orders/v1/margin/basket", json=basket_payload)
    assert bm.status_code == 200
    assert bm.json()["data"]["hedge_benefit"] == 0

    b_ord = client.post("/api/orders/v1/orders/basket", json=basket_payload)
    assert b_ord.status_code == 200
    # Buy leg executed first due to Optimised sequence
    assert b_ord.json()["data"][0]["transaction_type"] == "B"

    # 8. P&L Protect trigger test
    client.put(
        "/api/orders/v1/pnl-protects",
        json={
            "enabled": True,
            "max_loss": -1.0,
            "max_profit": 200000.0,
            "auto_activate_kill_switch": True,
        },
    )
    ev = state.risk.evaluate_pnl_protect(state.active_broker)
    assert ev is not None
    assert ev["event"] == "pnl_protect_triggered"
    assert client.get("/api/orders/v1/kill-switch").json()["data"]["active"] is True

    # 9. Scanners & Alerts
    sc = client.get("/api/v1/scanners/top_gainers/results")
    assert sc.status_code == 200
    assert len(sc.json()["instruments"]) > 0

    al = client.post(
        "/api/alerts/v1/alerts",
        json={
            "instrument_token": "NIFTY 50",
            "trading_symbol": "NIFTY 50",
            "condition": "ABOVE",
            "target_price": 26000.0,
            "note": "Breakout",
        },
    )
    assert al.status_code == 200
    assert len(client.get("/api/alerts/v1/alerts").json()["data"]) == 1

    # 10. WebSocket MarketFeed subscription
    with client.websocket_connect("/ws/marketfeed") as ws:
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert len(snap["data"]) >= 1
        ws.send_json({"action": "subscribe", "tokens": ["2885"]})
        snap2 = ws.receive_json()
        assert "2885" in snap2["subscribed"]


def test_kotak_adapter_contract():
    from app.broker.kotak_adapter import KotakNeoBroker

    # Verify error extraction from Kotak SDK error dicts
    assert KotakNeoBroker._extract_error({"Error": "Invalid TOTP"}) == "Invalid TOTP"
    assert KotakNeoBroker._extract_error({"error": [{"message": "MPIN invalid"}]}) == "MPIN invalid"
    assert KotakNeoBroker._extract_error({"stat": "Ok", "nOrdNo": "123"}) is None

    from neo_api_client.websocket.feed.models import DepthLevel, SFeedIndex, SFeedScrip

    kb = KotakNeoBroker()
    q = kb._normalize_ws_tick(SFeedIndex(
        exchange_segment="nse_cm", instrument_token="Nifty 50", name="NIFTY 50",
        last_traded_price=25750.50, open_price=25450, high_price=25800, low_price=25380,
        close_price=25400, change=350.50, net_change_percent=1.38, yearly_high=26000,
        yearly_low=24000, last_trade_time=1, precision=2, multiplier=1,
    ))
    assert q is not None
    assert q.instrument_token == "NIFTY 50"
    assert q.ltp == 25750.50

    q_depth = kb._normalize_ws_tick(SFeedScrip(
        exchange_segment="nse_fo", instrument_token="45123", trading_symbol="NIFTY01DEC2625700CE",
        level=8, last_traded_price=265.40, open_price=250, high_price=270, low_price=240,
        close_price=220, average_trade_price=255, last_trade_time=1, last_update_time=1,
        last_trade_qty=75, total_buy_quantity=375, total_sell_quantity=375,
        volume_traded_today=10000, open_interest=5000, net_change=45.4, net_change_percent=20.63,
        upper_circuit_limit=300, lower_circuit_limit=200, yearly_high=300, yearly_low=100,
        total_traded_value=2500000, market_lot=75, precision=2, multiplier=1,
        buy=[DepthLevel(quantity=75, price=265.20, orders=1)] + [DepthLevel(quantity=75, price=265.20 - i * 0.2, orders=i + 2) for i in range(1, 5)],
        sell=[DepthLevel(quantity=75, price=265.60, orders=1)] + [DepthLevel(quantity=75, price=265.60 + i * 0.2, orders=i + 2) for i in range(1, 5)],
    ))
    assert q_depth is not None
    assert len(q_depth.depth_bids) == 5
    assert q_depth.depth_bids[0]["price"] == 265.20
    assert q_depth.depth_asks[0]["price"] == 265.60
    assert q_depth.volume == 10000
