from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, Callable

from app.broker.base import BrokerAdapter
from app.db import Database
from app.models import (
    BasketOrderRequest,
    KillSwitchState,
    ModifyOrderRequest,
    OrderRecord,
    PlaceOrderRequest,
    PnlProtectConfig,
    PositionExitRequest,
    PositionRecord,
    QuickOrderRequest,
)


logger = logging.getLogger(__name__)
TERMINAL_ORDER_STATUSES = {"complete", "completed", "traded", "cancelled", "canceled", "rejected", "expired"}


class BasketExecutionError(RuntimeError):
    def __init__(self, placed: list[OrderRecord], cause: Exception) -> None:
        super().__init__(str(cause))
        self.placed = placed
        self.cause = cause


class OrderRequestError(RuntimeError):
    status_code = 409


class RiskEngine:
    def __init__(self, db: Database) -> None:
        self.db = db

    def submit_idempotent(
        self,
        broker: BrokerAdapter,
        payload: Any,
        key: str,
        submit: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        if broker.mode == "kotak" and not key:
            raise ValueError("client_order_id is required for live orders")
        if not key:
            return submit()
        request_hash = hashlib.sha256(
            json.dumps({"mode": broker.mode, "payload": payload}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        claim = self.db.begin_order_request(key, request_hash)
        if claim["status"] == "completed":
            return claim["response"]
        if claim["status"] == "conflict":
            raise OrderRequestError("client_order_id was already used with a different request")
        if claim["status"] == "in_progress":
            raise OrderRequestError("This order request is processing or has an uncertain broker result; do not retry")
        try:
            response = submit()
        except (ValueError, PermissionError):
            self.db.abort_order_request(key)
            raise
        except Exception:
            if broker.mode == "paper":
                self.db.abort_order_request(key)
            raise
        self.db.complete_order_request(key, response)
        return response

    def assert_order_allowed(self, broker: BrokerAdapter, req: PlaceOrderRequest) -> None:
        if req.order_type in ("L", "SL") and float(req.price) <= 0:
            raise ValueError("A positive price is required for limit orders")
        if req.order_type in ("SL", "SL-M") and float(req.trigger_price) <= 0:
            raise ValueError("A positive trigger price is required for stop-loss orders")
        if req.product == "CNC" and req.exchange_segment not in ("nse_cm", "bse_cm"):
            raise ValueError("CNC orders are only valid for cash-market segments")
        if req.product == "NRML" and req.exchange_segment not in ("nse_fo", "bse_fo", "mcx_fo", "nse_com"):
            raise ValueError("NRML orders are only valid for derivatives segments")
        if req.exchange_segment == "nse_com" and req.order_type == "SL-M":
            raise ValueError("NSE commodity orders do not support SL-M")
        if req.product == "MTF" and req.exchange_segment not in ("nse_cm", "bse_cm"):
            raise ValueError("MTF orders are only valid for cash-market segments")
        if req.exchange_segment == "mcx_fo" and req.validity != "DAY":
            raise ValueError("MCX orders only support DAY validity")
        if int(req.disclosed_quantity) > int(req.quantity):
            raise ValueError("Disclosed quantity cannot exceed order quantity")
        lookup = req.instrument_token or req.trading_symbol
        instruments = broker.list_instruments(lookup, exchange_segment=req.exchange_segment, limit=20)
        instrument = next((item for item in instruments if item.instrument_token == lookup or item.trading_symbol == req.trading_symbol), None)
        if (
            instrument is None
            or instrument.trading_symbol != req.trading_symbol
            or instrument.exchange_segment != req.exchange_segment
            or (req.instrument_token is not None and instrument.instrument_token != req.instrument_token)
        ):
            raise ValueError("Trading symbol and token do not match a real instrument in the selected segment")
        if instrument.instrument_type in ("CE", "PE", "FUT") and int(req.quantity) % instrument.lot_size != 0:
            raise ValueError("Quantity must be a multiple of the contract lot size")
        ks = self.db.get_kill_switch()
        if not ks.active:
            return
        # Allow closing existing positions even when kill switch is active
        for pos in broker.positions(require_ltp=False):
            same_instrument = (
                pos.trading_symbol == req.trading_symbol
                and pos.exchange_segment == req.exchange_segment
                and pos.product == req.product
                and (not req.instrument_token or pos.instrument_token == req.instrument_token)
            )
            if same_instrument and pos.net_qty != 0:
                is_exit = (pos.net_qty > 0 and req.transaction_type == "S") or (
                    pos.net_qty < 0 and req.transaction_type == "B"
                )
                if is_exit and int(req.quantity) <= abs(pos.net_qty):
                    self.assert_exit_capacity(broker, pos, req.transaction_type, int(req.quantity))
                    return
        raise PermissionError("Kill Switch is ACTIVE. New entry orders are blocked; only position exits are permitted.")

    def assert_quick_order_allowed(self, quick: QuickOrderRequest) -> None:
        if not self.db.get_order_defaults().one_tap_enabled:
            raise PermissionError("1-Tap execution is disabled. Use a confirmed order ticket instead.")

    def validate_exit_request(self, broker: BrokerAdapter, req: PositionExitRequest) -> PositionRecord:
        for pos in broker.positions(require_ltp=False):
            same_instrument = (
                pos.trading_symbol == req.trading_symbol
                and pos.exchange_segment == req.exchange_segment
                and pos.product == req.product
                and (not req.instrument_token or pos.instrument_token == req.instrument_token)
            )
            if not same_instrument or pos.net_qty == 0:
                continue
            is_exit = (pos.net_qty > 0 and req.transaction_type == "S") or (
                pos.net_qty < 0 and req.transaction_type == "B"
            )
            if not is_exit:
                raise ValueError("Exit transaction would increase the position")
            if req.quantity > abs(pos.net_qty):
                raise ValueError("Exit quantity exceeds the open position")
            self.assert_exit_capacity(broker, pos, req.transaction_type, req.quantity)
            return pos
        raise ValueError("No matching open position found")

    def assert_exit_capacity(self, broker: BrokerAdapter, position: PositionRecord, side: str, quantity: int) -> None:
        outstanding = sum(
            max(order.pending_quantity, order.quantity - order.filled_quantity, 0)
            for order in broker.order_book()
            if order.status.strip().lower() not in TERMINAL_ORDER_STATUSES
            and order.trading_symbol == position.trading_symbol
            and order.exchange_segment == position.exchange_segment
            and order.product == position.product
            and order.transaction_type == side
        )
        if quantity + outstanding > abs(position.net_qty):
            raise PermissionError("Existing pending orders already cover part or all of this position exit.")

    def prepare_quick_order(self, broker: BrokerAdapter, quick: QuickOrderRequest) -> PlaceOrderRequest:
        self.assert_quick_order_allowed(quick)
        defaults = self.db.get_order_defaults()
        instruments = broker.list_instruments(quick.instrument_token, exchange_segment=quick.exchange_segment, limit=10)
        inst = next(
            (
                i
                for i in instruments
                if i.instrument_token == quick.instrument_token or i.trading_symbol == quick.trading_symbol
            ),
            None,
        )
        if not inst or inst.instrument_token != quick.instrument_token:
            raise ValueError(f"Unknown instrument: {quick.instrument_token}")
        if quick.trading_symbol != inst.trading_symbol or inst.exchange_segment != quick.exchange_segment:
            raise ValueError("Trading symbol does not match the selected instrument")
        lot_size = inst.lot_size
        qty = quick.quantity if quick.quantity is not None else (quick.lots or defaults.default_lots) * lot_size

        order_req = PlaceOrderRequest(
            instrument_token=inst.instrument_token,
            exchange_segment=quick.exchange_segment,
            product=quick.product or defaults.default_product,
            price=str(quick.price if quick.price is not None else 0),
            order_type=quick.order_type or defaults.default_order_type,
            quantity=str(qty),
            validity="DAY",
            trading_symbol=inst.trading_symbol,
            transaction_type=quick.transaction_type,
            tag=quick.tag,
            client_order_id=quick.client_order_id,
        )
        return order_req

    def place_quick_order(self, broker: BrokerAdapter, quick: QuickOrderRequest) -> OrderRecord:
        order_req = self.prepare_quick_order(broker, quick)
        self.assert_order_allowed(broker, order_req)
        return broker.place_order(order_req)

    def validate_modification(self, broker: BrokerAdapter, req: ModifyOrderRequest) -> None:
        if req.order_type in ("L", "SL") and float(req.price) <= 0:
            raise ValueError("A positive price is required for limit modifications")
        if req.order_type in ("SL", "SL-M") and float(req.trigger_price) <= 0:
            raise ValueError("A positive trigger price is required for stop-loss modifications")
        if int(req.disclosed_quantity) > int(req.quantity):
            raise ValueError("Disclosed quantity cannot exceed order quantity")
        order = next((item for item in broker.order_book() if item.order_id == req.order_id), None)
        if order is None:
            raise ValueError("Order not found")
        if order.status.strip().lower() in TERMINAL_ORDER_STATUSES:
            raise ValueError("Completed or closed orders cannot be modified")
        if int(req.quantity) < order.filled_quantity:
            raise ValueError("Modified quantity cannot be less than the filled quantity")
        if order.exchange_segment == "mcx_fo" and req.validity != "DAY":
            raise ValueError("MCX orders only support DAY validity")
        if order.exchange_segment == "nse_com" and req.order_type == "SL-M":
            raise ValueError("NSE commodity orders do not support SL-M")
        instrument = next(
            (item for item in broker.list_instruments(order.trading_symbol, exchange_segment=order.exchange_segment, limit=20)
             if item.trading_symbol == order.trading_symbol),
            None,
        )
        if instrument is None:
            raise ValueError("Instrument metadata is unavailable for this order")
        if instrument.instrument_type in ("CE", "PE", "FUT") and int(req.quantity) % instrument.lot_size != 0:
            raise ValueError("Quantity must be a multiple of the contract lot size")

    def place_basket(self, broker: BrokerAdapter, req: BasketOrderRequest) -> list[OrderRecord]:
        orders = list(req.orders)
        if broker.mode == "kotak" and any(not order.client_order_id for order in orders):
            raise ValueError("Every live basket leg requires a client_order_id")
        if req.sequence == "Optimised":
            orders.sort(key=lambda o: 0 if o.transaction_type == "B" else 1)

        for order in orders:
            self.assert_order_allowed(broker, order)

        results: list[OrderRecord] = []
        try:
            for order in orders:
                def place(order: PlaceOrderRequest = order) -> dict[str, Any]:
                    if self.db.get_kill_switch().active:
                        self.assert_order_allowed(broker, order)
                    return {"data": broker.place_order(order).model_dump()}

                response = self.submit_idempotent(
                    broker,
                    order.model_dump(),
                    f"order:{order.client_order_id}" if order.client_order_id else "",
                    place,
                )
                results.append(OrderRecord(**response["data"]))
        except Exception as exc:
            raise BasketExecutionError(results, exc) from exc
        return results

    def basket_margin(self, broker: BrokerAdapter, req: BasketOrderRequest) -> dict[str, Any]:
        total_reqd = 0.0
        per_order = []
        for order in req.orders:
            margin = broker.margin_required(order)
            value = float((margin.get("data") or {}).get("reqdMrgn") or 0.0)
            total_reqd += value
            per_order.append(
                {
                    "trading_symbol": order.trading_symbol,
                    "transaction_type": order.transaction_type,
                    "margin": value,
                }
            )
        final_margin = round(total_reqd, 2)
        limits = broker.limits()
        available = float(limits.get("availableCash") or 0.0)
        return {
            "gross_margin": final_margin,
            "hedge_benefit": 0.0,
            "final_margin": final_margin,
            "available_cash": available,
            "sufficient": available >= final_margin,
            "method": "sum_of_leg_margins",
            "legs": per_order,
        }

    def bulk_cancel(self, broker: BrokerAdapter, order_ids: list[str] | None = None) -> list[dict[str, Any]]:
        targets = order_ids if order_ids is not None else [
            order.order_id for order in broker.order_book()
            if order.status.strip().lower() not in TERMINAL_ORDER_STATUSES
        ]
        return [broker.cancel_order(oid) for oid in targets]

    def exit_all_positions(self, broker: BrokerAdapter, client_order_id: str | None = None) -> list[OrderRecord]:
        if broker.mode == "kotak" and not client_order_id:
            raise ValueError("client_order_id is required for live Hot Exit")
        self.bulk_cancel(broker)
        if broker.mode == "kotak":
            for attempt in range(5):
                if all(order.status.strip().lower() in TERMINAL_ORDER_STATUSES for order in broker.order_book()):
                    break
                if attempt < 4:
                    time.sleep(0.4)
            else:
                raise RuntimeError("Pending orders remain after cancellation; positions were not submitted for exit")

        exits: list[OrderRecord] = []
        requests: list[PlaceOrderRequest] = []
        for pos in broker.positions(require_ltp=False):
            if pos.net_qty == 0:
                continue
            leg_id = (
                uuid.uuid5(uuid.NAMESPACE_URL, f"{client_order_id}:{pos.exchange_segment}:{pos.instrument_token}:{pos.product}").hex
                if client_order_id else None
            )
            requests.append(PlaceOrderRequest(
                instrument_token=pos.instrument_token,
                exchange_segment=pos.exchange_segment,  # type: ignore[arg-type]
                product=pos.product,  # type: ignore[arg-type]
                price="0", order_type="MKT", quantity=str(abs(pos.net_qty)), validity="DAY",
                trading_symbol=pos.trading_symbol, transaction_type="S" if pos.net_qty > 0 else "B",
                tag=leg_id, client_order_id=leg_id,
            ))
        for req in requests:
            self.assert_order_allowed(broker, req)
        for req in requests:
            response = self.submit_idempotent(
                broker,
                req.model_dump(),
                f"order:{req.client_order_id}" if req.client_order_id else "",
                lambda req=req: {"data": broker.place_order(req).model_dump()},
            )
            exits.append(OrderRecord(**response["data"]))
        return exits

    def evaluate_pnl_protect(self, broker: BrokerAdapter) -> dict[str, Any] | None:
        cfg: PnlProtectConfig = self.db.get_pnl_protect()
        if not cfg.enabled or cfg.triggered:
            return None

        positions = broker.positions()
        open_positions = [p for p in positions if p.net_qty != 0]
        if not open_positions:
            return None

        total_pnl = sum(p.pnl for p in positions)
        if total_pnl > cfg.peak_pnl:
            cfg.peak_pnl = round(total_pnl, 2)
            self.db.set_pnl_protect(cfg)

        trigger_reason: str | None = None
        if total_pnl <= cfg.max_loss:
            trigger_reason = f"Max loss breached ({total_pnl:.2f} <= {cfg.max_loss:.2f})"
        elif total_pnl >= cfg.max_profit:
            trigger_reason = f"Max profit target reached ({total_pnl:.2f} >= {cfg.max_profit:.2f})"
        elif cfg.trailing_sl_enabled and cfg.peak_pnl >= cfg.lock_profit_at:
            floor_pnl = cfg.peak_pnl - cfg.trailing_sl_step
            if total_pnl <= floor_pnl:
                trigger_reason = f"Trailing SL hit ({total_pnl:.2f} <= {floor_pnl:.2f} from peak {cfg.peak_pnl:.2f})"

        if not trigger_reason:
            return None

        cfg.triggered = True
        cfg.enabled = False
        cfg.triggered_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.db.set_pnl_protect(cfg)

        if cfg.auto_activate_kill_switch:
            self.db.set_kill_switch(
                KillSwitchState(
                    active=True,
                    activated_at=cfg.triggered_at,
                    reason=f"Auto-activated by P&L Protect: {trigger_reason}",
                )
            )

        try:
            exits = self.exit_all_positions(broker, client_order_id=f"pnl-{uuid.uuid4()}")
        except Exception:
            logger.exception("P&L Protect triggered but automatic exit was not confirmed")
            return {
                "event": "pnl_protect_triggered",
                "ok": False,
                "reason": trigger_reason,
                "total_pnl": round(total_pnl, 2),
                "exit_orders": [],
                "error": "Automatic exit was not confirmed. Reconcile positions, then use Hot Exit.",
            }

        return {
            "event": "pnl_protect_triggered",
            "ok": True,
            "reason": trigger_reason,
            "total_pnl": round(total_pnl, 2),
            "exit_orders": [e.order_id for e in exits],
        }
