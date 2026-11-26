from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

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


class BrokerAdapter(ABC):
    mode: str

    @abstractmethod
    def login(self, req: TotpLoginRequest) -> AuthSessionResponse:
        ...

    @abstractmethod
    def logout(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def list_instruments(
        self,
        query: str = "",
        exchange_segment: str | None = None,
        limit: int | None = 250,
    ) -> list[Instrument]:
        ...

    @abstractmethod
    def get_quotes(self, tokens: list[str]) -> list[QuoteData]:
        ...

    @abstractmethod
    def place_order(self, req: PlaceOrderRequest) -> OrderRecord:
        ...

    @abstractmethod
    def modify_order(self, req: ModifyOrderRequest) -> OrderRecord:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> dict[str, Any]:
        ...

    @abstractmethod
    def order_book(self) -> list[OrderRecord]:
        ...

    @abstractmethod
    def order_history(self, order_id: str) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def trade_book(self, order_id: str | None = None) -> list[TradeRecord]:
        ...

    @abstractmethod
    def positions(self, require_ltp: bool = True) -> list[PositionRecord]:
        ...

    @abstractmethod
    def convert_position(self, req: PositionConvertRequest) -> dict[str, Any]:
        ...

    @abstractmethod
    def holdings(self) -> list[HoldingRecord]:
        ...

    @abstractmethod
    def limits(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def margin_required(self, req: PlaceOrderRequest) -> dict[str, Any]:
        ...

    @abstractmethod
    def step_ticks(self) -> list[QuoteData]:
        """Advance synthetic ticks (paper mode) or return latest cached live ticks."""
        ...
