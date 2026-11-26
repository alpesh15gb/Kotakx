package com.kotakneo.terminal.data.model

import com.google.gson.annotations.SerializedName

data class LoginRequest(
    val mode: String = "paper",
    @SerializedName("consumer_key") val consumerKey: String = "",
    @SerializedName("mobile_number") val mobileNumber: String = "",
    val ucc: String = "DEMO01",
    val totp: String = "",
    val mpin: String = "",
)

data class AccessAuthStatus(
    val authenticated: Boolean = false,
    @SerializedName("auth_configured") val authConfigured: Boolean = false,
)

data class AccessLoginRequest(val password: String)

data class AccessAuthResponse(val ok: Boolean = false)

data class AuthSessionData(
    @SerializedName("session_id") val sessionId: String = "",
    val mode: String = "paper",
    val ucc: String = "DEMO01",
    val authenticated: Boolean = false,
    @SerializedName("two_fa_verified") val twoFaVerified: Boolean = false,
    val message: String = "",
)

data class QuoteData(
    @SerializedName("instrument_token") val instrumentToken: String,
    @SerializedName("exchange_segment") val exchangeSegment: String,
    @SerializedName("trading_symbol") val tradingSymbol: String,
    val ltp: Double,
    val open: Double,
    val high: Double,
    val low: Double,
    val close: Double,
    val change: Double,
    @SerializedName("change_pct") val changePct: Double,
    val volume: Long,
    val oi: Long,
    @SerializedName("updated_at") val updatedAt: Long,
)

data class Candle(
    val time: Long,
    val open: Double,
    val high: Double,
    val low: Double,
    val close: Double,
    val volume: Long,
)

data class IndicatorPoint(
    val time: Long,
    val value: Double,
)

data class CandlesResponse(
    val ok: Boolean,
    val instrument: String,
    val timeframe: String,
    val candles: List<Candle>,
    val indicators: Map<String, List<IndicatorPoint>>,
    val synthetic: Boolean = false,
)

private fun validateClientOrderId(clientOrderId: String?) {
    require(clientOrderId == null || (clientOrderId.isNotBlank() && clientOrderId.length in 8..100)) {
        "client_order_id must contain 8 to 100 non-blank characters when supplied"
    }
}

data class QuickOrderRequest(
    @SerializedName("instrument_token") val instrumentToken: String,
    @SerializedName("exchange_segment") val exchangeSegment: String,
    @SerializedName("trading_symbol") val tradingSymbol: String,
    @SerializedName("transaction_type") val transactionType: String,
    @SerializedName("client_order_id") val clientOrderId: String? = null,
) {
    init {
        validateClientOrderId(clientOrderId)
    }
}

data class PositionExitRequest(
    @SerializedName("instrument_token") val instrumentToken: String? = null,
    @SerializedName("trading_symbol") val tradingSymbol: String,
    @SerializedName("exchange_segment") val exchangeSegment: String,
    val product: String,
    val quantity: Int,
    @SerializedName("transaction_type") val transactionType: String,
    @SerializedName("client_order_id") val clientOrderId: String? = null,
) {
    init {
        validateClientOrderId(clientOrderId)
    }
}

data class HotExitRequest(
    @SerializedName("client_order_id") val clientOrderId: String? = null,
) {
    init {
        validateClientOrderId(clientOrderId)
    }
}

data class OrderRecord(
    @SerializedName("order_id") val orderId: String,
    @SerializedName("trading_symbol") val tradingSymbol: String,
    @SerializedName("exchange_segment") val exchangeSegment: String,
    @SerializedName("transaction_type") val transactionType: String,
    val product: String,
    @SerializedName("order_type") val orderType: String,
    val quantity: Int,
    @SerializedName("filled_quantity") val filledQuantity: Int,
    val price: Double,
    @SerializedName("avg_price") val avgPrice: Double,
    val status: String,
    @SerializedName("order_time") val orderTime: String,
)

data class PositionRecord(
    @SerializedName("instrument_token") val instrumentToken: String,
    @SerializedName("trading_symbol") val tradingSymbol: String,
    @SerializedName("exchange_segment") val exchangeSegment: String,
    val product: String,
    @SerializedName("net_qty") val netQty: Int,
    @SerializedName("avg_price") val avgPrice: Double,
    val ltp: Double,
    val pnl: Double,
)

data class HoldingRecord(
    @SerializedName("instrument_token") val instrumentToken: String,
    @SerializedName("trading_symbol") val tradingSymbol: String,
    val quantity: Int,
    @SerializedName("average_price") val averagePrice: Double,
    val ltp: Double,
    @SerializedName("mkt_value") val mktValue: Double,
    val pnl: Double,
    @SerializedName("pnl_pct") val pnlPct: Double,
)

data class OptionChainStrike(
    @SerializedName("strike_price") val strikePrice: Double,
    @SerializedName("is_atm") val isAtm: Boolean,
    @SerializedName("call_token") val callToken: String?,
    @SerializedName("call_symbol") val callSymbol: String?,
    @SerializedName("call_ltp") val callLtp: Double,
    @SerializedName("call_oi") val callOi: Long,
    @SerializedName("call_iv") val callIv: Double,
    @SerializedName("call_delta") val callDelta: Double,
    @SerializedName("put_token") val putToken: String?,
    @SerializedName("put_symbol") val putSymbol: String?,
    @SerializedName("put_ltp") val putLtp: Double,
    @SerializedName("put_oi") val putOi: Long,
    @SerializedName("put_iv") val putIv: Double,
    @SerializedName("put_delta") val putDelta: Double,
)

data class OptionChainData(
    val underlying: String,
    @SerializedName("spot_price") val spotPrice: Double,
    val expiry: String,
    @SerializedName("available_expiries") val availableExpiries: List<String> = emptyList(),
    val pcr: Double,
    @SerializedName("max_call_oi_strike") val maxCallOiStrike: Double,
    @SerializedName("max_put_oi_strike") val maxPutOiStrike: Double,
    @SerializedName("expected_move_pct") val expectedMovePct: Double = 0.0,
    val strikes: List<OptionChainStrike>,
)

data class OrderDefaults(
    @SerializedName("default_lots") val defaultLots: Int = 1,
    @SerializedName("default_product") val defaultProduct: String = "MIS",
    @SerializedName("default_order_type") val defaultOrderType: String = "MKT",
    @SerializedName("one_tap_enabled") val oneTapEnabled: Boolean = false,
)

data class PnlProtectConfig(
    val enabled: Boolean = false,
    @SerializedName("max_loss") val maxLoss: Double = -5000.0,
    @SerializedName("max_profit") val maxProfit: Double = 10000.0,
    @SerializedName("trailing_sl_enabled") val trailingSlEnabled: Boolean = false,
    @SerializedName("trailing_sl_step") val trailingSlStep: Double = 1000.0,
    @SerializedName("lock_profit_at") val lockProfitAt: Double = 2000.0,
    @SerializedName("auto_activate_kill_switch") val autoActivateKillSwitch: Boolean = false,
    @SerializedName("peak_pnl") val peakPnl: Double = 0.0,
    val triggered: Boolean = false,
    @SerializedName("triggered_at") val triggeredAt: String? = null,
)

data class KillSwitchState(
    val active: Boolean = false,
    @SerializedName("activated_at") val activatedAt: String? = null,
    val reason: String? = null,
)

data class PositionsResponse(
    val ok: Boolean,
    @SerializedName("total_pnl") val totalPnl: Double,
    val positions: List<PositionRecord>,
)

data class Envelope<T>(
    val ok: Boolean,
    val data: T,
    val message: String? = null,
)
