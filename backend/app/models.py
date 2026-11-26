from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


ExchangeSegment = Literal["nse_cm", "nse_com", "bse_cm", "nse_fo", "bse_fo", "mcx_fo"]
ProductType = Literal["NRML", "CNC", "MIS", "MTF"]
OrderType = Literal["L", "MKT", "SL", "SL-M"]
ValidityType = Literal["DAY", "IOC"]
TransactionType = Literal["B", "S"]


class TotpLoginRequest(BaseModel):
    consumer_key: str = Field(default="", max_length=500)
    mobile_number: str = Field(default="", max_length=30)
    ucc: str = Field(default="", max_length=100)
    totp: str = Field(default="", max_length=6)
    mpin: str = Field(default="", max_length=6)
    mode: Literal["paper", "kotak"] = "paper"


class AccessLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=500)


class AuthSessionResponse(BaseModel):
    session_id: str
    mode: Literal["paper", "kotak"]
    ucc: str
    authenticated: bool
    two_fa_verified: bool
    message: str


class Instrument(BaseModel):
    instrument_token: str
    exchange_segment: str
    trading_symbol: str
    symbol: str
    name: str
    instrument_type: Literal["INDEX", "EQ", "FUT", "CE", "PE"]
    expiry: Optional[str] = None
    strike_price: Optional[float] = None
    option_type: Optional[Literal["CE", "PE"]] = None
    lot_size: int = 1
    tick_size: float = 0.05
    underlying: Optional[str] = None


class QuoteData(BaseModel):
    instrument_token: str
    exchange_segment: str
    trading_symbol: str
    ltp: float
    open: float
    high: float
    low: float
    close: float
    change: float
    change_pct: float
    volume: int = 0
    oi: int = 0
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: int = 0
    ask_qty: int = 0
    depth_bids: list[dict[str, float | int]] = Field(default_factory=list)
    depth_asks: list[dict[str, float | int]] = Field(default_factory=list)
    updated_at: int


class Candle(BaseModel):
    time: int  # epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


def _positive_int_string(value: str) -> str:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError("must be a positive integer")
    return str(parsed)


def _non_negative_decimal_string(value: str) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("must be a non-negative number") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("must be a non-negative number")
    return str(value)


def _non_negative_int_string(value: str) -> str:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError("must be a non-negative integer")
    return str(parsed)


class PlaceOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange_segment: ExchangeSegment = "nse_fo"
    product: ProductType = "MIS"
    price: str = "0"
    order_type: OrderType = "MKT"
    quantity: str
    validity: ValidityType = "DAY"
    trading_symbol: str = Field(min_length=1, max_length=100)
    transaction_type: TransactionType
    amo: Literal["YES", "NO"] = "NO"
    disclosed_quantity: str = "0"
    trigger_price: str = "0"
    tag: Optional[str] = Field(default=None, max_length=50)
    client_order_id: Optional[str] = Field(default=None, min_length=8, max_length=100)
    instrument_token: Optional[str] = Field(default=None, max_length=100)

    _validate_quantity = field_validator("quantity")(_positive_int_string)
    _validate_disclosed_quantity = field_validator("disclosed_quantity")(_non_negative_int_string)
    _validate_price = field_validator("price", "trigger_price")(_non_negative_decimal_string)


class QuickOrderRequest(BaseModel):
    instrument_token: str = Field(min_length=1, max_length=100)
    exchange_segment: ExchangeSegment = "nse_fo"
    trading_symbol: str = Field(min_length=1, max_length=100)
    transaction_type: TransactionType
    lots: Optional[int] = Field(default=None, gt=0)
    quantity: Optional[int] = Field(default=None, gt=0)
    product: Optional[ProductType] = None
    order_type: Optional[OrderType] = None
    price: Optional[float] = Field(default=None, ge=0)
    tag: str = Field(default="1-tap", max_length=50)
    client_order_id: Optional[str] = Field(default=None, min_length=8, max_length=100)


class ModifyOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(min_length=1, max_length=100)
    price: str = "0"
    quantity: str
    disclosed_quantity: str = "0"
    trigger_price: str = "0"
    validity: ValidityType = "DAY"
    order_type: OrderType = "L"

    _validate_quantity = field_validator("quantity")(_positive_int_string)
    _validate_disclosed_quantity = field_validator("disclosed_quantity")(_non_negative_int_string)
    _validate_numbers = field_validator("price", "trigger_price")(_non_negative_decimal_string)


class BasketOrderRequest(BaseModel):
    sequence: Literal["Optimised", "Sequential"] = "Optimised"
    orders: list[PlaceOrderRequest] = Field(min_length=1, max_length=20)
    client_order_id: Optional[str] = Field(default=None, min_length=8, max_length=100)


class OrderRecord(BaseModel):
    order_id: str
    exchange_order_id: str = ""
    trading_symbol: str
    exchange_segment: str
    transaction_type: str
    product: str
    order_type: str
    quantity: int
    filled_quantity: int
    pending_quantity: int
    price: float
    trigger_price: float = 0.0
    avg_price: float = 0.0
    status: str  # open, complete, cancelled, rejected
    rejection_reason: str = ""
    tag: Optional[str] = None
    order_time: str


class TradeRecord(BaseModel):
    trade_id: str
    order_id: str
    trading_symbol: str
    exchange_segment: str
    transaction_type: str
    product: str
    quantity: int
    fill_price: float
    fill_time: str


class PositionRecord(BaseModel):
    instrument_token: str
    trading_symbol: str
    exchange_segment: str
    product: str
    net_qty: int
    buy_qty: int
    sell_qty: int
    buy_avg_price: float
    sell_avg_price: float
    avg_price: float
    ltp: float
    realized_pnl: float
    unrealized_pnl: float
    pnl: float
    lot_size: int = 1
    multiplier: float = 1.0


class HoldingRecord(BaseModel):
    instrument_token: str
    exchange_segment: str
    trading_symbol: str
    symbol: str
    quantity: int
    sellable_quantity: int
    average_price: float
    closing_price: float
    ltp: float
    mkt_value: float
    holding_cost: float
    pnl: float
    pnl_pct: float


class PositionConvertRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=100)
    exchange_segment: ExchangeSegment
    from_product: ProductType
    to_product: ProductType
    qty: int = Field(gt=0)


class PositionExitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_token: Optional[str] = Field(default=None, max_length=100)
    trading_symbol: str = Field(min_length=1, max_length=100)
    exchange_segment: ExchangeSegment
    product: ProductType
    quantity: int = Field(gt=0)
    transaction_type: TransactionType
    client_order_id: Optional[str] = Field(default=None, min_length=8, max_length=100)


class HotExitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_order_id: Optional[str] = Field(default=None, min_length=8, max_length=100)


class OrderDefaults(BaseModel):
    default_lots: int = Field(default=1, gt=0)
    default_product: ProductType = "MIS"
    default_order_type: OrderType = "MKT"
    one_tap_enabled: bool = False


class PnlProtectConfig(BaseModel):
    enabled: bool = False
    max_loss: float = Field(default=-5000.0, le=0)
    max_profit: float = Field(default=10000.0, ge=0)
    trailing_sl_enabled: bool = False
    trailing_sl_step: float = Field(default=1000.0, gt=0)
    lock_profit_at: float = Field(default=2000.0, ge=0)
    auto_activate_kill_switch: bool = False
    peak_pnl: float = 0.0
    triggered: bool = False
    triggered_at: Optional[str] = None


class KillSwitchState(BaseModel):
    active: bool = False
    activated_at: Optional[str] = None
    reason: Optional[str] = None


class AlertCreateRequest(BaseModel):
    instrument_token: str = Field(min_length=1, max_length=100)
    trading_symbol: str = Field(min_length=1, max_length=100)
    condition: Literal["ABOVE", "BELOW", "CROSS_ABOVE", "CROSS_BELOW"]
    target_price: float = Field(gt=0)
    note: str = Field(default="", max_length=500)


class AlertRecord(BaseModel):
    id: str
    instrument_token: str
    trading_symbol: str
    condition: str
    target_price: float
    note: str
    status: Literal["ACTIVE", "TRIGGERED", "DELETED"]
    created_at: str
    triggered_at: Optional[str] = None


class OptionChainStrike(BaseModel):
    strike_price: float
    is_atm: bool = False
    call_token: Optional[str] = None
    call_symbol: Optional[str] = None
    call_ltp: float = 0.0
    call_change_pct: float = 0.0
    call_oi: int = 0
    call_oi_change: int = 0
    call_volume: int = 0
    call_iv: float = 0.0
    call_delta: float = 0.0
    call_gamma: float = 0.0
    call_theta: float = 0.0
    call_vega: float = 0.0
    put_token: Optional[str] = None
    put_symbol: Optional[str] = None
    put_ltp: float = 0.0
    put_change_pct: float = 0.0
    put_oi: int = 0
    put_oi_change: int = 0
    put_volume: int = 0
    put_iv: float = 0.0
    put_delta: float = 0.0
    put_gamma: float = 0.0
    put_theta: float = 0.0
    put_vega: float = 0.0


class OptionChainResponse(BaseModel):
    underlying: str
    spot_price: float
    expiry: str
    available_expiries: list[str]
    pcr: float
    max_call_oi_strike: float
    max_put_oi_strike: float
    expected_move_pct: float
    strikes: list[OptionChainStrike]


class WatchlistUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tokens: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("tokens")
    @classmethod
    def validate_tokens(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError("tokens must contain non-empty instrument identifiers up to 100 characters")
        return list(dict.fromkeys(values))


class ApiEnvelope(BaseModel):
    ok: bool = True
    data: Any = None
    message: Optional[str] = None
