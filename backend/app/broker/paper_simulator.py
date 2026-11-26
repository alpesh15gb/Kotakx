from __future__ import annotations

import math
import random
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

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
from app.services.option_chain import black_scholes_price_and_greeks, time_to_expiry_days


class PaperBroker(BrokerAdapter):
    """
    Full-featured simulated Indian market & Kotak Neo OMS broker for immediate
    end-to-end testing of Web & Android apps (live NIFTY/BANKNIFTY Spot + F&O chains + Equities).
    """

    mode = "paper"

    def __init__(self) -> None:
        self._rng = random.Random(42)
        self.ucc = "DEMO01"
        self.authenticated = True
        self.two_fa_verified = True
        self.available_cash = 500000.0
        self.collateral = 125000.0
        self.instruments: dict[str, Instrument] = {}
        self.quotes: dict[str, QuoteData] = {}
        self._orders: dict[str, OrderRecord] = {}
        self._order_history: dict[str, list[dict[str, Any]]] = {}
        self._trades: list[TradeRecord] = []
        self._positions: dict[tuple[str, str], dict[str, Any]] = {}
        self._holdings: list[HoldingRecord] = []
        self._seed_market()

    def _seed_market(self) -> None:
        now = int(time.time())
        base_instruments = [
            Instrument(
                instrument_token="NIFTY 50",
                exchange_segment="nse_cm",
                trading_symbol="NIFTY 50",
                symbol="NIFTY50",
                name="NIFTY 50 Index",
                instrument_type="INDEX",
                lot_size=75,
                tick_size=0.05,
                underlying="NIFTY",
            ),
            Instrument(
                instrument_token="NIFTY BANK",
                exchange_segment="nse_cm",
                trading_symbol="NIFTY BANK",
                symbol="BANKNIFTY",
                name="NIFTY Bank Index",
                instrument_type="INDEX",
                lot_size=30,
                tick_size=0.05,
                underlying="BANKNIFTY",
            ),
            Instrument(
                instrument_token="2885",
                exchange_segment="nse_cm",
                trading_symbol="RELIANCE-EQ",
                symbol="RELIANCE",
                name="Reliance Industries Ltd",
                instrument_type="EQ",
                lot_size=1,
                tick_size=0.05,
            ),
            Instrument(
                instrument_token="1333",
                exchange_segment="nse_cm",
                trading_symbol="HDFCBANK-EQ",
                symbol="HDFCBANK",
                name="HDFC Bank Ltd",
                instrument_type="EQ",
                lot_size=1,
                tick_size=0.05,
            ),
            Instrument(
                instrument_token="11536",
                exchange_segment="nse_cm",
                trading_symbol="TCS-EQ",
                symbol="TCS",
                name="Tata Consultancy Services Ltd",
                instrument_type="EQ",
                lot_size=1,
                tick_size=0.05,
            ),
            Instrument(
                instrument_token="1594",
                exchange_segment="nse_cm",
                trading_symbol="INFY-EQ",
                symbol="INFY",
                name="Infosys Ltd",
                instrument_type="EQ",
                lot_size=1,
                tick_size=0.05,
            ),
            Instrument(
                instrument_token="3045",
                exchange_segment="nse_cm",
                trading_symbol="SBIN-EQ",
                symbol="SBIN",
                name="State Bank of India",
                instrument_type="EQ",
                lot_size=1,
                tick_size=0.05,
            ),
        ]

        initial_prices = {
            "NIFTY 50": (25734.15, 25350.15, 18500000),
            "NIFTY BANK": (53620.40, 53110.00, 9200000),
            "2885": (1312.40, 1296.10, 4812000),
            "1333": (1764.80, 1749.20, 6120000),
            "11536": (4148.50, 4110.00, 1980000),
            "1594": (1892.35, 1868.90, 3210000),
            "3045": (842.60, 831.40, 8450000),
        }

        for inst in base_instruments:
            self.instruments[inst.instrument_token] = inst
            ltp, prev_close, vol = initial_prices[inst.instrument_token]
            self.quotes[inst.instrument_token] = self._make_quote(inst, ltp, prev_close, vol, 0, now)

        # Seed NIFTY and BANKNIFTY option chains
        expiries = self._next_expiries()
        self._seed_options("NIFTY", 25734.15, range(25300, 26150, 50), 75, expiries, now)
        self._seed_options("BANKNIFTY", 53620.40, range(53000, 54300, 100), 30, expiries, now)

        # Seed sample holdings
        self._holdings = [
            HoldingRecord(
                instrument_token="2885",
                exchange_segment="nse_cm",
                trading_symbol="RELIANCE-EQ",
                symbol="RELIANCE",
                quantity=25,
                sellable_quantity=25,
                average_price=1265.00,
                closing_price=1296.10,
                ltp=1312.40,
                mkt_value=round(25 * 1312.40, 2),
                holding_cost=round(25 * 1265.00, 2),
                pnl=round(25 * (1312.40 - 1265.00), 2),
                pnl_pct=round(((1312.40 - 1265.00) / 1265.00) * 100.0, 2),
            ),
            HoldingRecord(
                instrument_token="11536",
                exchange_segment="nse_cm",
                trading_symbol="TCS-EQ",
                symbol="TCS",
                quantity=10,
                sellable_quantity=10,
                average_price=4020.00,
                closing_price=4110.00,
                ltp=4148.50,
                mkt_value=round(10 * 4148.50, 2),
                holding_cost=round(10 * 4020.00, 2),
                pnl=round(10 * (4148.50 - 4020.00), 2),
                pnl_pct=round(((4148.50 - 4020.00) / 4020.00) * 100.0, 2),
            ),
        ]

    @staticmethod
    def _next_expiries() -> list[str]:
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
        days_to_tuesday = (1 - today.weekday()) % 7
        first = today + timedelta(days=days_to_tuesday or 7)
        return [(first + timedelta(days=offset)).strftime("%d%b%Y").upper() for offset in (0, 7)]

    def _seed_options(
        self,
        underlying: str,
        spot: float,
        strikes: range,
        lot_size: int,
        expiries: list[str],
        now: int,
    ) -> None:
        for expiry in expiries:
            tte = time_to_expiry_days(expiry) / 365.0
            exp_short = expiry[:5]
            for strike in strikes:
                dist = abs(strike - spot)
                iv = 0.135 + min(0.09, (dist / spot) * 0.65)
                for opt_type in ("CE", "PE"):
                    token = f"{underlying}{exp_short}{strike}{opt_type}"
                    trd_sym = f"{underlying}{expiry[:2]}{expiry[2:5]}{expiry[7:]}{strike}{opt_type}"
                    inst = Instrument(
                        instrument_token=token,
                        exchange_segment="nse_fo",
                        trading_symbol=trd_sym,
                        symbol=underlying,
                        name=f"{underlying} {expiry} {strike} {opt_type}",
                        instrument_type=opt_type,
                        expiry=expiry,
                        strike_price=float(strike),
                        option_type=opt_type,
                        lot_size=lot_size,
                        tick_size=0.05,
                        underlying=underlying,
                    )
                    self.instruments[token] = inst
                    bs = black_scholes_price_and_greeks(spot, float(strike), tte, 0.065, iv, opt_type)
                    ltp = bs["price"]
                    prev_close = round(max(0.5, ltp * (0.88 if opt_type == "CE" else 1.12)), 2)
                    # Higher OI at round strikes (e.g. 25700, 25800, 26000)
                    round_bonus = 2.2 if strike % 500 == 0 else (1.5 if strike % 100 == 0 else 1.0)
                    oi_base = int(max(120000, (3200000 * math.exp(-dist / 350.0)) * round_bonus))
                    vol = int(oi_base * 0.45)
                    self.quotes[token] = self._make_quote(inst, ltp, prev_close, vol, oi_base, now)

    def _make_quote(self, inst: Instrument, ltp: float, prev_close: float, vol: int, oi: int, now: int) -> QuoteData:
        chg = round(ltp - prev_close, 2)
        chg_pct = round((chg / prev_close) * 100.0, 2) if prev_close else 0.0
        spread = inst.tick_size * (1 if ltp < 500 else 2)
        bid = round(max(inst.tick_size, ltp - spread), 2)
        ask = round(ltp + spread, 2)
        depth_bids = [
            {"price": round(max(inst.tick_size, bid - i * inst.tick_size), 2), "qty": inst.lot_size * (4 + i * 3), "orders": 2 + i}
            for i in range(5)
        ]
        depth_asks = [
            {"price": round(ask + i * inst.tick_size, 2), "qty": inst.lot_size * (3 + i * 4), "orders": 1 + i}
            for i in range(5)
        ]
        return QuoteData(
            instrument_token=inst.instrument_token,
            exchange_segment=inst.exchange_segment,
            trading_symbol=inst.trading_symbol,
            ltp=round(ltp, 2),
            open=round(prev_close * 1.003, 2),
            high=round(max(ltp, prev_close * 1.012), 2),
            low=round(min(ltp, prev_close * 0.992), 2),
            close=round(prev_close, 2),
            change=chg,
            change_pct=chg_pct,
            volume=vol,
            oi=oi,
            bid=bid,
            ask=ask,
            bid_qty=depth_bids[0]["qty"],
            ask_qty=depth_asks[0]["qty"],
            depth_bids=depth_bids,
            depth_asks=depth_asks,
            updated_at=now,
        )

    def login(self, req: TotpLoginRequest) -> AuthSessionResponse:
        self.ucc = req.ucc or "DEMO01"
        self.authenticated = True
        self.two_fa_verified = True
        return AuthSessionResponse(
            session_id=f"paper_{self.ucc}_{int(time.time())}",
            mode="paper",
            ucc=self.ucc,
            authenticated=True,
            two_fa_verified=True,
            message="Connected in Paper Trading Simulator mode; no broker orders are sent.",
        )

    def logout(self) -> dict[str, Any]:
        return {"status": "logged_out", "mode": "paper"}

    def list_instruments(
        self,
        query: str = "",
        exchange_segment: str | None = None,
        limit: int | None = 250,
    ) -> list[Instrument]:
        q = query.strip().upper()
        out = []
        for inst in self.instruments.values():
            if exchange_segment and inst.exchange_segment != exchange_segment:
                continue
            if not q or q in inst.trading_symbol.upper() or q in inst.symbol.upper() or q in inst.name.upper() or q in inst.instrument_token.upper():
                out.append(inst)
        return out if limit is None else out[:limit]

    def _resolve_instrument(self, token_or_symbol: str) -> Instrument | None:
        if token_or_symbol in self.instruments:
            return self.instruments[token_or_symbol]
        upper = token_or_symbol.upper()
        for inst in self.instruments.values():
            if inst.trading_symbol.upper() == upper or inst.symbol.upper() == upper:
                return inst
        return None

    def get_quotes(self, tokens: list[str]) -> list[QuoteData]:
        out: list[QuoteData] = []
        for tok in tokens:
            if tok in self.quotes:
                out.append(self.quotes[tok])
            else:
                inst = self._resolve_instrument(tok)
                if inst and inst.instrument_token in self.quotes:
                    out.append(self.quotes[inst.instrument_token])
        return out

    def place_order(self, req: PlaceOrderRequest) -> OrderRecord:
        inst = self._resolve_instrument(req.instrument_token or req.trading_symbol)
        if not inst:
            raise ValueError(f"Unknown instrument: {req.trading_symbol}")

        qty = int(req.quantity)
        if qty <= 0:
            raise ValueError("Order quantity must be positive.")
        if inst.exchange_segment.endswith("_fo") and qty % inst.lot_size != 0:
            raise ValueError(f"Quantity must be a multiple of the lot size ({inst.lot_size})")
        if req.order_type != "MKT" and float(req.price or 0) <= 0:
            raise ValueError("A positive limit price is required for this order type")
        if req.order_type in {"SL", "SL-M"} and float(req.trigger_price or 0) <= 0:
            raise ValueError("A positive trigger price is required for stop-loss orders")
        if req.transaction_type == "S" and req.product == "CNC":
            position_qty = self._positions.get((inst.instrument_token, "CNC"), {}).get("buy_qty", 0) - self._positions.get(
                (inst.instrument_token, "CNC"), {}
            ).get("sell_qty", 0)
            holding_qty = sum(h.sellable_quantity for h in self._holdings if h.instrument_token == inst.instrument_token)
            if qty > position_qty + holding_qty:
                raise ValueError("Insufficient CNC holdings or position for this sell order")
        if req.transaction_type == "B" or req.product != "CNC":
            margin = self.margin_required(req)
            if (margin.get("data") or {}).get("rmsVldtd") != "OK":
                raise ValueError("Insufficient paper trading funds for this order")

        q = self.quotes[inst.instrument_token]
        limit_px = float(req.price or 0.0)
        trig_px = float(req.trigger_price or 0.0)
        order_id = f"{time.strftime('%y%m%d')}{self._rng.randint(10000000, 99999999)}"
        now_str = time.strftime("%d-%b-%Y %H:%M:%S")

        should_fill = req.order_type == "MKT"
        if req.order_type == "L":
            if req.transaction_type == "B" and limit_px >= q.ask:
                should_fill = True
            elif req.transaction_type == "S" and limit_px <= q.bid:
                should_fill = True

        fill_px = (q.ask if req.transaction_type == "B" else q.bid) if req.order_type == "MKT" else limit_px
        fill_px = round(max(inst.tick_size, fill_px), 2)

        order = OrderRecord(
            order_id=order_id,
            exchange_order_id=f"110000{self._rng.randint(10000000, 99999999)}" if should_fill else "",
            trading_symbol=inst.trading_symbol,
            exchange_segment=inst.exchange_segment,
            transaction_type=req.transaction_type,
            product=req.product,
            order_type=req.order_type,
            quantity=qty,
            filled_quantity=qty if should_fill else 0,
            pending_quantity=0 if should_fill else qty,
            price=fill_px if req.order_type == "MKT" else limit_px,
            trigger_price=trig_px,
            avg_price=fill_px if should_fill else 0.0,
            status="complete" if should_fill else "open",
            tag=req.client_order_id or req.tag,
            order_time=now_str,
        )
        self._orders[order_id] = order
        self._order_history[order_id] = [
            {"order_id": order_id, "status": "received", "time": now_str, "price": order.price, "qty": qty},
            {"order_id": order_id, "status": order.status, "time": now_str, "price": order.avg_price or order.price, "qty": qty},
        ]

        if should_fill:
            self._record_fill(order, inst, fill_px, now_str)

        return order

    def _record_fill(self, order: OrderRecord, inst: Instrument, fill_px: float, now_str: str) -> None:
        self._trades.insert(
            0,
            TradeRecord(
                trade_id=f"TRD{uuid.uuid4().hex[:10].upper()}",
                order_id=order.order_id,
                trading_symbol=order.trading_symbol,
                exchange_segment=order.exchange_segment,
                transaction_type=order.transaction_type,
                product=order.product,
                quantity=order.quantity,
                fill_price=fill_px,
                fill_time=now_str,
            ),
        )
        key = (inst.instrument_token, order.product)
        pos = self._positions.get(
            key,
            {
                "instrument_token": inst.instrument_token,
                "trading_symbol": inst.trading_symbol,
                "exchange_segment": inst.exchange_segment,
                "product": order.product,
                "buy_qty": 0,
                "sell_qty": 0,
                "buy_amt": 0.0,
                "sell_amt": 0.0,
                "lot_size": inst.lot_size,
            },
        )
        if order.transaction_type == "B":
            pos["buy_qty"] += order.quantity
            pos["buy_amt"] += order.quantity * fill_px
        else:
            pos["sell_qty"] += order.quantity
            pos["sell_amt"] += order.quantity * fill_px
        self._positions[key] = pos

    def modify_order(self, req: ModifyOrderRequest) -> OrderRecord:
        order = self._orders.get(req.order_id)
        if not order:
            raise ValueError(f"Order {req.order_id} not found")
        if order.status != "open":
            raise ValueError(f"Cannot modify order in status {order.status}")
        order.price = float(req.price or order.price)
        order.quantity = int(req.quantity)
        order.pending_quantity = int(req.quantity) - order.filled_quantity
        order.trigger_price = float(req.trigger_price or order.trigger_price)
        order.order_type = req.order_type
        self._order_history.setdefault(req.order_id, []).append(
            {"order_id": req.order_id, "status": "modified", "time": time.strftime("%d-%b-%Y %H:%M:%S"), "price": order.price, "qty": order.quantity}
        )
        if order.order_type == "MKT":
            inst = self._resolve_instrument(order.trading_symbol)
            if inst:
                q = self.quotes[inst.instrument_token]
                fill_px = q.ask if order.transaction_type == "B" else q.bid
                order.filled_quantity = order.quantity
                order.pending_quantity = 0
                order.avg_price = fill_px
                order.status = "complete"
                self._record_fill(order, inst, fill_px, time.strftime("%d-%b-%Y %H:%M:%S"))
        return order

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        order = self._orders.get(order_id)
        if not order:
            raise ValueError(f"Order {order_id} not found")
        if order.status != "open":
            return {"stat": "NotOk", "order_id": order_id, "status": order.status, "message": f"Order already {order.status}"}
        order.status = "cancelled"
        order.pending_quantity = 0
        self._order_history.setdefault(order_id, []).append(
            {"order_id": order_id, "status": "cancelled", "time": time.strftime("%d-%b-%Y %H:%M:%S")}
        )
        return {"stat": "Ok", "order_id": order_id, "status": "cancelled"}

    def order_book(self) -> list[OrderRecord]:
        return list(reversed(list(self._orders.values())))

    def order_history(self, order_id: str) -> list[dict[str, Any]]:
        return self._order_history.get(order_id, [])

    def trade_book(self, order_id: str | None = None) -> list[TradeRecord]:
        if order_id:
            return [t for t in self._trades if t.order_id == order_id]
        return list(self._trades)

    def positions(self, require_ltp: bool = True) -> list[PositionRecord]:
        out: list[PositionRecord] = []
        for pos in self._positions.values():
            tok = pos["instrument_token"]
            ltp = self.quotes[tok].ltp if tok in self.quotes else 0.0
            buy_qty = pos["buy_qty"]
            sell_qty = pos["sell_qty"]
            net_qty = buy_qty - sell_qty
            buy_amt = pos["buy_amt"]
            sell_amt = pos["sell_amt"]
            buy_avg = buy_amt / buy_qty if buy_qty > 0 else 0.0
            sell_avg = sell_amt / sell_qty if sell_qty > 0 else 0.0
            avg_px = buy_avg if net_qty > 0 else (sell_avg if net_qty < 0 else 0.0)
            total_pnl = (sell_amt - buy_amt) + (net_qty * ltp)
            realized = (min(buy_qty, sell_qty) * (sell_avg - buy_avg)) if (buy_qty > 0 and sell_qty > 0) else 0.0
            unrealized = total_pnl - realized
            out.append(
                PositionRecord(
                    instrument_token=tok,
                    trading_symbol=pos["trading_symbol"],
                    exchange_segment=pos["exchange_segment"],
                    product=pos["product"],
                    net_qty=net_qty,
                    buy_qty=buy_qty,
                    sell_qty=sell_qty,
                    buy_avg_price=round(buy_avg, 2),
                    sell_avg_price=round(sell_avg, 2),
                    avg_price=round(avg_px, 2),
                    ltp=round(ltp, 2),
                    realized_pnl=round(realized, 2),
                    unrealized_pnl=round(unrealized, 2),
                    pnl=round(total_pnl, 2),
                    lot_size=pos["lot_size"],
                )
            )
        return out

    def convert_position(self, req: PositionConvertRequest) -> dict[str, Any]:
        inst = self._resolve_instrument(req.symbol)
        if not inst:
            raise ValueError(f"Unknown symbol {req.symbol}")
        src_key = (inst.instrument_token, req.from_product)
        src = self._positions.get(src_key)
        if not src:
            raise ValueError(f"No {req.from_product} position found for {req.symbol}")
        net = src["buy_qty"] - src["sell_qty"]
        if abs(net) < req.qty:
            raise ValueError("Convert quantity exceeds open position quantity")

        dst_key = (inst.instrument_token, req.to_product)
        dst = self._positions.setdefault(
            dst_key,
            {
                "instrument_token": inst.instrument_token,
                "trading_symbol": inst.trading_symbol,
                "exchange_segment": inst.exchange_segment,
                "product": req.to_product,
                "buy_qty": 0,
                "sell_qty": 0,
                "buy_amt": 0.0,
                "sell_amt": 0.0,
                "lot_size": inst.lot_size,
            },
        )
        if net > 0:
            avg = src["buy_amt"] / src["buy_qty"]
            src["buy_qty"] -= req.qty
            src["buy_amt"] -= req.qty * avg
            dst["buy_qty"] += req.qty
            dst["buy_amt"] += req.qty * avg
        else:
            avg = src["sell_amt"] / src["sell_qty"]
            src["sell_qty"] -= req.qty
            src["sell_amt"] -= req.qty * avg
            dst["sell_qty"] += req.qty
            dst["sell_amt"] += req.qty * avg
        return {"status": "converted", "symbol": req.symbol, "from": req.from_product, "to": req.to_product, "qty": req.qty}

    def holdings(self) -> list[HoldingRecord]:
        updated: list[HoldingRecord] = []
        for h in self._holdings:
            q = self.quotes.get(h.instrument_token)
            ltp = q.ltp if q else h.ltp
            mkt = round(h.quantity * ltp, 2)
            pnl = round(mkt - h.holding_cost, 2)
            pnl_pct = round((pnl / h.holding_cost * 100.0) if h.holding_cost else 0.0, 2)
            updated.append(
                h.model_copy(update={"ltp": ltp, "mkt_value": mkt, "pnl": pnl, "pnl_pct": pnl_pct})
            )
        return updated

    def limits(self) -> dict[str, Any]:
        positions = self.positions()
        margin_used = 0.0
        total_pnl = 0.0
        for p in positions:
            total_pnl += p.pnl
            margin_used += abs(p.net_qty) * p.ltp * (0.2 if p.product == "MIS" else 1.0)
        net = round(self.available_cash + self.collateral - margin_used + total_pnl, 2)
        return {
            "Category": "PAPER_TRADING",
            "Net": f"{net:.2f}",
            "CollateralValue": f"{self.collateral:.2f}",
            "MarginUsed": f"{margin_used:.2f}",
            "FoUnRlsMtomPrsnt": f"{total_pnl:.2f}",
            "availableCash": round(self.available_cash - margin_used, 2),
            "marginUsed": round(margin_used, 2),
            "totalPnl": round(total_pnl, 2),
            "stat": "Ok",
            "stCode": 200,
        }

    def margin_required(self, req: PlaceOrderRequest) -> dict[str, Any]:
        inst = self._resolve_instrument(req.instrument_token or req.trading_symbol)
        q = self.quotes.get(inst.instrument_token) if inst else None
        px = float(req.price or 0.0) or (q.ltp if q else 100.0)
        qty = int(req.quantity or 1)
        leverage = 5.0 if req.product == "MIS" else (1.0 if req.transaction_type == "B" and inst and inst.instrument_type in ("CE", "PE") else 1.0)
        if inst and inst.instrument_type in ("CE", "PE") and req.transaction_type == "S":
            reqd = round(max(35000.0, qty * 1150.0), 2)
        else:
            reqd = round((qty * px) / leverage, 2)
        lim = self.limits()
        avl = float(lim["availableCash"])
        return {
            "stat": "Ok",
            "stCode": 200,
            "data": {
                "avlCash": f"{avl:.2f}",
                "reqdMrgn": f"{reqd:.2f}",
                "ordMrgn": f"{reqd:.2f}",
                "insufFund": f"{max(0.0, reqd - avl):.2f}",
                "rmsVldtd": "OK" if avl >= reqd else "INSUFFICIENT_FUNDS",
            },
        }

    def step_ticks(self) -> list[QuoteData]:
        now = int(time.time())
        changed: list[QuoteData] = []

        # Step underlying & equities first
        for tok, inst in self.instruments.items():
            if inst.instrument_type in ("INDEX", "EQ"):
                q = self.quotes[tok]
                drift = self._rng.gauss(0.0, 0.00055)
                new_ltp = round(max(1.0, q.ltp * (1.0 + drift)), 2)
                updated = self._make_quote(
                    inst,
                    new_ltp,
                    q.close,
                    q.volume + self._rng.randint(50, 800),
                    q.oi,
                    now,
                )
                updated.open = q.open
                updated.high = round(max(q.high, new_ltp), 2)
                updated.low = round(min(q.low, new_ltp), 2)
                self.quotes[tok] = updated
                changed.append(updated)

        # Reprice options smoothly from their underlying spot + small noise
        nifty_spot = self.quotes["NIFTY 50"].ltp
        bnf_spot = self.quotes["NIFTY BANK"].ltp
        for tok, inst in self.instruments.items():
            if inst.instrument_type in ("CE", "PE") and inst.strike_price is not None:
                q = self.quotes[tok]
                spot = nifty_spot if inst.underlying == "NIFTY" else bnf_spot
                tte = time_to_expiry_days(inst.expiry or "") / 365.0
                iv = 0.14 + self._rng.uniform(-0.002, 0.002)
                bs = black_scholes_price_and_greeks(spot, inst.strike_price, tte, 0.065, iv, inst.option_type or "CE")
                new_ltp = round(max(0.10, bs["price"] + self._rng.uniform(-0.35, 0.35)), 2)
                updated = self._make_quote(
                    inst,
                    new_ltp,
                    q.close,
                    q.volume + self._rng.randint(100, 2500),
                    max(1000, q.oi + self._rng.randint(-1500, 2500)),
                    now,
                )
                updated.open = q.open
                updated.high = round(max(q.high, new_ltp), 2)
                updated.low = round(min(q.low, new_ltp), 2)
                self.quotes[tok] = updated
                changed.append(updated)

        # Check open limit orders for fills
        for order in self._orders.values():
            if order.status != "open":
                continue
            inst = self._resolve_instrument(order.trading_symbol)
            if not inst:
                continue
            q = self.quotes[inst.instrument_token]
            filled = False
            if order.order_type == "L":
                if order.transaction_type == "B" and q.ask <= order.price:
                    filled = True
                elif order.transaction_type == "S" and q.bid >= order.price:
                    filled = True
            elif order.order_type in ("SL", "SL-M"):
                if order.transaction_type == "B" and q.ltp >= order.trigger_price:
                    filled = True
                elif order.transaction_type == "S" and q.ltp <= order.trigger_price:
                    filled = True
            if filled:
                fill_px = q.ask if order.transaction_type == "B" else q.bid
                order.filled_quantity = order.quantity
                order.pending_quantity = 0
                order.avg_price = fill_px
                order.status = "complete"
                self._record_fill(order, inst, fill_px, time.strftime("%d-%b-%Y %H:%M:%S"))

        return changed
