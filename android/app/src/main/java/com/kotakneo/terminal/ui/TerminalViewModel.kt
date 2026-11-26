package com.kotakneo.terminal.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.google.gson.JsonParser
import com.kotakneo.terminal.data.api.ApiClient
import com.kotakneo.terminal.data.api.ReconnectingFeed
import com.kotakneo.terminal.data.model.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.util.UUID
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.absoluteValue

enum class AccessState { CHECKING, REQUIRED, READY, ERROR }

internal const val HOT_EXIT_PENDING_KEY = "hot-exit"

internal fun positionExitKey(position: PositionRecord): String = listOf(
    position.exchangeSegment,
    position.instrumentToken,
    position.product,
    position.tradingSymbol,
).joinToString(":")

internal fun positionExitPendingKey(position: PositionRecord): String = "exit:${positionExitKey(position)}"

data class TerminalUiState(
    val accessState: AccessState = AccessState.CHECKING,
    val accessError: String? = null,
    val authConfigured: Boolean = false,
    val mode: String = "paper",
    val ucc: String = "",
    val underlying: String = "NIFTY",
    val activeOptionToken: String = "",
    val activeOptionSymbol: String = "",
    val selectedExpiry: String? = null,
    val timeframe: String = "1m",
    val quotes: Map<String, QuoteData> = emptyMap(),
    val spotCandles: List<Candle> = emptyList(),
    val optionCandles: List<Candle> = emptyList(),
    val optionEma: List<IndicatorPoint> = emptyList(),
    val spotSynthetic: Boolean? = null,
    val optionSynthetic: Boolean? = null,
    val optionChain: OptionChainData? = null,
    val chainLoading: Boolean = false,
    val chainError: String? = null,
    val positions: List<PositionRecord> = emptyList(),
    val totalPnl: Double = 0.0,
    val orders: List<OrderRecord> = emptyList(),
    val holdings: List<HoldingRecord> = emptyList(),
    val orderDefaults: OrderDefaults = OrderDefaults(),
    val killSwitch: KillSwitchState = KillSwitchState(),
    val pnlProtect: PnlProtectConfig = PnlProtectConfig(),
    val pendingOrderKeys: Set<String> = emptySet(),
    val marketConnected: Boolean = false,
    val orderFeedConnected: Boolean = false,
    val statusBanner: String? = null,
)

class TerminalViewModel : ViewModel() {
    private val _ui = MutableStateFlow(TerminalUiState())
    val ui: StateFlow<TerminalUiState> = _ui.asStateFlow()

    private val authGeneration = AtomicInteger()
    private val sessionGeneration = AtomicInteger()
    private val chainGeneration = AtomicInteger()
    private val candleGeneration = AtomicInteger()
    private val portfolioGeneration = AtomicInteger()
    private val controlsGeneration = AtomicInteger()
    private val brokerSessionGeneration = AtomicInteger()
    private val feedGeneration = AtomicInteger()
    private val orderLock = Any()
    private val inFlightOrders = mutableMapOf<String, Int>()
    private val quickOrderClientIds = mutableMapOf<String, String>()
    private val positionExitClientIds = mutableMapOf<String, String>()
    private var hotExitClientId: String? = null
    private var clientIdBrokerIdentity: String? = null

    private var marketFeed: ReconnectingFeed? = null
    private var orderFeed: ReconnectingFeed? = null
    private var portfolioJob: Job? = null
    private var chainJob: Job? = null

    init {
        ApiClient.unauthorizedHandler = ::handleUnauthorized
        checkAccess()
    }

    private fun isReady(session: Int = sessionGeneration.get()): Boolean =
        session == sessionGeneration.get() && _ui.value.accessState == AccessState.READY

    private fun beginPendingOrder(key: String): Int? = synchronized(orderLock) {
        if (inFlightOrders.containsKey(key)) return null
        val session = sessionGeneration.get()
        inFlightOrders[key] = session
        _ui.update { it.copy(pendingOrderKeys = it.pendingOrderKeys + key) }
        session
    }

    private fun finishPendingOrder(key: String, session: Int) {
        synchronized(orderLock) {
            // A previous session may finish after a new submission with the same key. Only the
            // request that owns this pending entry may clear it.
            if (inFlightOrders[key] == session) {
                inFlightOrders.remove(key)
                _ui.update { it.copy(pendingOrderKeys = it.pendingOrderKeys - key) }
            }
        }
    }

    private fun retainedClientId(ids: MutableMap<String, String>, key: String, prefix: String): String =
        synchronized(orderLock) { ids.getOrPut(key) { "$prefix-${UUID.randomUUID()}" } }

    private fun confirmClientId(ids: MutableMap<String, String>, key: String, clientOrderId: String) {
        synchronized(orderLock) {
            if (ids[key] == clientOrderId) ids.remove(key)
        }
    }

    private fun updateBrokerIdentity(mode: String, ucc: String) {
        val identity = "$mode:$ucc"
        synchronized(orderLock) {
            // Access locks do not discard replay IDs, but a different broker account must never
            // inherit an ID retained for another account.
            if (clientIdBrokerIdentity != null && clientIdBrokerIdentity != identity) {
                quickOrderClientIds.clear()
                positionExitClientIds.clear()
                hotExitClientId = null
            }
            clientIdBrokerIdentity = identity
        }
    }

    private fun httpErrorDetail(error: HttpException): String? = runCatching {
        error.response()?.errorBody()?.string()?.let { body ->
            JsonParser.parseString(body).asJsonObject.get("detail")?.asString
        }
    }.getOrNull()

    private fun errorMessage(error: Throwable): String = when (error) {
        is HttpException -> when (error.code()) {
            401 -> "Session expired. Unlock the terminal again."
            409 -> {
                val detail = httpErrorDetail(error)
                    ?: "This client order ID is already processing or was used for another request."
                "Idempotency conflict (409): $detail Reconcile positions and the order book; do not retry with changed details."
            }
            502 -> {
                val detail = httpErrorDetail(error) ?: "The broker may have received the request."
                "Broker result uncertain (502): $detail No automatic retry was made. Reconcile positions and the order book before retrying."
            }
            else -> httpErrorDetail(error)?.let { "Request failed (${error.code()}): $it" } ?: error.message ?: "Request failed"
        }
        else -> error.message ?: "Unknown network error"
    }

    private fun reconcileAfterOrderFailure(error: Throwable) {
        if (error is HttpException && (error.code() == 409 || error.code() == 502)) refreshPortfolio()
    }

    fun checkAccess() {
        val generation = authGeneration.incrementAndGet()
        _ui.update { it.copy(accessState = AccessState.CHECKING, accessError = null) }
        viewModelScope.launch {
            runCatching { ApiClient.rest.getAuthStatus() }
                .onSuccess { status ->
                    if (generation != authGeneration.get()) return@onSuccess
                    if (status.authenticated || !status.authConfigured) {
                        _ui.update { it.copy(accessState = AccessState.READY, accessError = null, authConfigured = status.authConfigured) }
                        startTerminal()
                    } else {
                        _ui.update { it.copy(accessState = AccessState.REQUIRED, accessError = null, authConfigured = status.authConfigured) }
                    }
                }
                .onFailure { error ->
                    if (generation == authGeneration.get()) {
                        _ui.update { it.copy(accessState = AccessState.ERROR, accessError = errorMessage(error)) }
                    }
                }
        }
    }

    fun unlock(password: String) {
        if (password.isBlank() || _ui.value.accessState == AccessState.CHECKING) return
        val generation = authGeneration.incrementAndGet()
        _ui.update { it.copy(accessState = AccessState.CHECKING, accessError = null) }
        viewModelScope.launch {
            runCatching { ApiClient.rest.accessLogin(AccessLoginRequest(password)) }
                .onSuccess {
                    if (generation != authGeneration.get()) return@onSuccess
                    _ui.update { it.copy(accessState = AccessState.READY, accessError = null, authConfigured = true) }
                    startTerminal()
                }
                .onFailure { error ->
                    if (generation == authGeneration.get()) {
                        _ui.update { it.copy(accessState = AccessState.REQUIRED, accessError = errorMessage(error)) }
                    }
                }
        }
    }

    fun lockTerminal() {
        authGeneration.incrementAndGet()
        sessionGeneration.incrementAndGet()
        chainGeneration.incrementAndGet()
        candleGeneration.incrementAndGet()
        portfolioGeneration.incrementAndGet()
        controlsGeneration.incrementAndGet()
        brokerSessionGeneration.incrementAndGet()
        stopTerminal()
        _ui.update { clearTerminalData(it).copy(accessState = AccessState.REQUIRED, accessError = null, ucc = "") }
        viewModelScope.launch {
            runCatching { ApiClient.rest.accessLogout() }
                .onFailure { error -> _ui.update { it.copy(accessError = "Local access cleared, but server logout failed: ${errorMessage(error)}") } }
            ApiClient.clearCookies()
        }
    }

    private fun handleUnauthorized() {
        authGeneration.incrementAndGet()
        sessionGeneration.incrementAndGet()
        chainGeneration.incrementAndGet()
        candleGeneration.incrementAndGet()
        portfolioGeneration.incrementAndGet()
        controlsGeneration.incrementAndGet()
        brokerSessionGeneration.incrementAndGet()
        stopTerminal()
        _ui.update {
            clearTerminalData(it).copy(
                accessState = AccessState.REQUIRED,
                accessError = "Your session expired. Enter the access password to continue.",
                authConfigured = true,
                ucc = "",
            )
        }
    }

    private fun clearTerminalData(state: TerminalUiState): TerminalUiState = state.copy(
        activeOptionToken = "",
        activeOptionSymbol = "",
        selectedExpiry = null,
        quotes = emptyMap(),
        spotCandles = emptyList(),
        optionCandles = emptyList(),
        optionEma = emptyList(),
        spotSynthetic = null,
        optionSynthetic = null,
        optionChain = null,
        chainLoading = false,
        chainError = null,
        positions = emptyList(),
        totalPnl = 0.0,
        orders = emptyList(),
        holdings = emptyList(),
        orderDefaults = OrderDefaults(),
        killSwitch = KillSwitchState(),
        pnlProtect = PnlProtectConfig(),
        pendingOrderKeys = emptySet(),
        marketConnected = false,
        orderFeedConnected = false,
        statusBanner = null,
    )

    private fun startTerminal() {
        if (!isReady() || portfolioJob?.isActive == true) return
        refreshAll()
        connectFeeds()
        portfolioJob = viewModelScope.launch {
            while (isReady()) {
                delay(2_500)
                refreshPortfolio()
            }
        }
        chainJob = viewModelScope.launch {
            while (isReady()) {
                delay(15_000)
                loadOptionChain()
            }
        }
    }

    private fun stopTerminal() {
        portfolioJob?.cancel()
        chainJob?.cancel()
        portfolioJob = null
        chainJob = null
        feedGeneration.incrementAndGet()
        marketFeed?.close()
        orderFeed?.close()
        marketFeed = null
        orderFeed = null
        synchronized(orderLock) {
            inFlightOrders.clear()
        }
    }

    private fun currentMarketTokens(): List<String> {
        val state = _ui.value
        val spot = if (state.underlying == "NIFTY") "NIFTY 50" else "NIFTY BANK"
        return (listOf("NIFTY 50", "NIFTY BANK", spot, state.activeOptionToken) +
            state.optionChain?.strikes.orEmpty().flatMap { listOf(it.callToken, it.putToken) })
            .filterNotNull()
            .filter { it.isNotBlank() }
            .distinct()
    }

    private fun updateMarketSubscription() {
        val payload = ApiClient.gson.toJson(mapOf("action" to "replace", "tokens" to currentMarketTokens()))
        marketFeed?.updateSubscription(payload)
    }

    private fun connectFeeds() {
        if (!isReady()) return
        marketFeed?.close()
        orderFeed?.close()
        val generation = feedGeneration.incrementAndGet()
        val wsBase = com.kotakneo.terminal.BuildConfig.WS_BASE_URL.trimEnd('/')

        marketFeed = ReconnectingFeed(
            client = ApiClient.okHttp,
            url = "$wsBase/ws/marketfeed",
            onMessage = { text ->
                if (generation == feedGeneration.get()) handleMarketMessage(text)
            },
            onStateChange = { connected ->
                if (generation == feedGeneration.get()) _ui.update { it.copy(marketConnected = connected) }
            },
        )

        orderFeed = ReconnectingFeed(
            client = ApiClient.okHttp,
            url = "$wsBase/ws/orderfeed",
            onMessage = { text ->
                if (generation == feedGeneration.get()) handleOrderMessage(text)
            },
            onOpen = {
                if (generation == feedGeneration.get()) {
                    refreshControls()
                    refreshPortfolio()
                }
            },
            onStateChange = { connected ->
                if (generation == feedGeneration.get()) _ui.update { it.copy(orderFeedConnected = connected) }
            },
        )
        updateMarketSubscription()
    }

    private fun handleMarketMessage(text: String) {
        if (!isReady()) return
        runCatching {
            val root = JsonParser.parseString(text).asJsonObject
            val type = root.get("type")?.asString
            if (type != "snapshot" && type != "ticks") return
            val array = root.getAsJsonArray("data") ?: return
            val ticks = ApiClient.gson.fromJson(array, Array<QuoteData>::class.java).orEmpty().toList()
            if (ticks.isEmpty()) return
            _ui.update { current ->
                val quotes = current.quotes.toMutableMap()
                for (tick in ticks) {
                    val prior = quotes[tick.instrumentToken]
                    if (prior == null || tick.updatedAt >= prior.updatedAt) quotes[tick.instrumentToken] = tick
                }
                val spotToken = if (current.underlying == "NIFTY") "NIFTY 50" else "NIFTY BANK"
                val spotTick = quotes[spotToken]
                val optionTick = quotes[current.activeOptionToken]
                current.copy(
                    quotes = quotes,
                    spotCandles = spotTick?.let { mergeTick(current.spotCandles, it, current.timeframe) } ?: current.spotCandles,
                    optionCandles = optionTick?.let { mergeTick(current.optionCandles, it, current.timeframe) } ?: current.optionCandles,
                    optionChain = mergeChainTicks(current.optionChain, quotes),
                )
            }
        }
    }

    private fun handleOrderMessage(text: String) {
        if (!isReady()) return
        runCatching {
            val root = JsonParser.parseString(text).asJsonObject
            val type = root.get("type")?.asString ?: return
            val data = root.getAsJsonObject("data")
            when (type) {
                "kill_switch" -> {
                    val killSwitch = data?.let { ApiClient.gson.fromJson(it, KillSwitchState::class.java) }
                    if (killSwitch != null) {
                        _ui.update {
                            it.copy(killSwitch = killSwitch, statusBanner = if (killSwitch.active) "Kill Switch ACTIVE" else "Kill Switch OFF")
                        }
                    }
                    refreshControls()
                }
                "pnl_protect_triggered", "risk_event" -> {
                    val reason = data?.get("reason")?.asString ?: data?.get("message")?.asString ?: "Risk protection triggered"
                    _ui.update { it.copy(statusBanner = reason) }
                    data?.getAsJsonObject("kill_switch")?.let {
                        val killSwitch = ApiClient.gson.fromJson(it, KillSwitchState::class.java)
                        _ui.update { current -> current.copy(killSwitch = killSwitch) }
                    }
                    refreshControls()
                    refreshPortfolio()
                }
                "alert_triggered" -> {
                    val symbol = data?.get("trading_symbol")?.asString ?: "Instrument"
                    val price = data?.get("target_price")?.asString ?: ""
                    _ui.update { it.copy(statusBanner = "Alert triggered: $symbol @ $price") }
                    refreshControls()
                }
                "broker_order_update", "order_update", "order_cancelled", "hot_exit" -> refreshPortfolio()
                "connected" -> {
                    refreshControls()
                    refreshPortfolio()
                }
            }
        }
    }

    fun refreshAll() {
        if (!isReady()) return
        refreshSession()
        loadCandles()
        loadOptionChain()
        refreshPortfolio()
        refreshControls()
    }

    private fun refreshSession() {
        if (!isReady()) return
        val session = sessionGeneration.get()
        val generation = brokerSessionGeneration.incrementAndGet()
        viewModelScope.launch {
            runCatching {
                val response = ApiClient.rest.getSession()
                check(response.ok) { "Session response was not confirmed" }
                response.data
            }.onSuccess { brokerSession ->
                if (isReady(session) && generation == brokerSessionGeneration.get()) {
                    updateBrokerIdentity(brokerSession.mode, brokerSession.ucc)
                    _ui.update {
                        it.copy(
                            mode = brokerSession.mode,
                            ucc = brokerSession.ucc,
                            statusBanner = when {
                                !brokerSession.authenticated -> "Broker session is not authenticated"
                                !brokerSession.twoFaVerified -> "Broker two-factor verification is incomplete"
                                else -> it.statusBanner
                            },
                        )
                    }
                }
            }.onFailure { error ->
                if (isReady(session) && generation == brokerSessionGeneration.get()) {
                    _ui.update { it.copy(statusBanner = "Session refresh failed: ${errorMessage(error)}") }
                }
            }
        }
    }

    fun loadCandles() {
        if (!isReady()) return
        val session = sessionGeneration.get()
        val generation = candleGeneration.incrementAndGet()
        val state = _ui.value
        val spotToken = if (state.underlying == "NIFTY") "NIFTY 50" else "NIFTY BANK"
        _ui.update { it.copy(spotSynthetic = null, optionSynthetic = null) }
        viewModelScope.launch {
            runCatching {
                val spot = ApiClient.rest.getCandles(spotToken, state.timeframe, "ema20")
                if (!isReady(session) || generation != candleGeneration.get()) return@launch
                val option = if (state.activeOptionToken.isNotBlank()) {
                    ApiClient.rest.getCandles(state.activeOptionToken, state.timeframe, "ema20")
                } else null
                if (!isReady(session) || generation != candleGeneration.get()) return@launch
                _ui.update {
                    it.copy(
                        spotCandles = spot.candles,
                        optionCandles = option?.candles.orEmpty(),
                        optionEma = option?.indicators?.get("ema20").orEmpty(),
                        spotSynthetic = spot.synthetic,
                        optionSynthetic = option?.synthetic,
                    )
                }
            }.onFailure { error ->
                if (isReady(session) && generation == candleGeneration.get()) {
                    _ui.update { it.copy(statusBanner = "Candle load failed: ${errorMessage(error)}") }
                }
            }
        }
    }

    fun loadOptionChain() {
        if (!isReady()) return
        val session = sessionGeneration.get()
        val generation = chainGeneration.incrementAndGet()
        val underlying = _ui.value.underlying
        val expiry = _ui.value.selectedExpiry
        _ui.update { it.copy(chainLoading = true, chainError = null) }
        viewModelScope.launch {
            runCatching { ApiClient.rest.getOptionChain(underlying, expiry).data }
                .onSuccess { chain ->
                    val current = _ui.value
                    if (!isReady(session) || generation != chainGeneration.get()
                        || current.underlying != underlying || current.selectedExpiry != expiry
                        || chain.underlying != underlying
                    ) return@onSuccess
                    val atm = atmCall(chain)
                    val selectionValid = current.activeOptionToken.isNotBlank() && chain.strikes.any { strike ->
                        (strike.callToken == current.activeOptionToken && strike.callSymbol == current.activeOptionSymbol) ||
                            (strike.putToken == current.activeOptionToken && strike.putSymbol == current.activeOptionSymbol)
                    }
                    val token = if (selectionValid) current.activeOptionToken else atm?.first.orEmpty()
                    val symbol = if (selectionValid) current.activeOptionSymbol else atm?.second.orEmpty()
                    val selectionChanged = token != current.activeOptionToken || symbol != current.activeOptionSymbol
                    _ui.update {
                        it.copy(
                            optionChain = chain,
                            selectedExpiry = chain.expiry,
                            activeOptionToken = token,
                            activeOptionSymbol = symbol,
                            chainLoading = false,
                            chainError = null,
                        )
                    }
                    updateMarketSubscription()
                    if (selectionChanged) loadCandles()
                }
                .onFailure { error ->
                    if (isReady(session) && generation == chainGeneration.get()) {
                        _ui.update { it.copy(chainLoading = false, chainError = errorMessage(error)) }
                    }
                }
        }
    }

    fun refreshPortfolio() {
        if (!isReady()) return
        val session = sessionGeneration.get()
        val generation = portfolioGeneration.incrementAndGet()
        viewModelScope.launch {
            runCatching {
                coroutineScope {
                    val positions = async { ApiClient.rest.getPositions() }
                    val orders = async { ApiClient.rest.getOrderBook().data }
                    val holdings = async { ApiClient.rest.getHoldings().data }
                    Triple(positions.await(), orders.await(), holdings.await())
                }
            }.onSuccess { (positions, orders, holdings) ->
                if (isReady(session) && generation == portfolioGeneration.get()) {
                    _ui.update {
                        it.copy(positions = positions.positions, totalPnl = positions.totalPnl, orders = orders, holdings = holdings)
                    }
                }
            }
        }
    }

    fun refreshControls() {
        if (!isReady()) return
        val session = sessionGeneration.get()
        val generation = controlsGeneration.incrementAndGet()
        viewModelScope.launch {
            runCatching {
                coroutineScope {
                    val defaults = async { ApiClient.rest.getOrderDefaults().data }
                    val killSwitch = async { ApiClient.rest.getKillSwitch().data }
                    val pnlProtect = async { ApiClient.rest.getPnlProtect().data }
                    Triple(defaults.await(), killSwitch.await(), pnlProtect.await())
                }
            }.onSuccess { (defaults, killSwitch, pnlProtect) ->
                if (isReady(session) && generation == controlsGeneration.get()) {
                    _ui.update { it.copy(orderDefaults = defaults, killSwitch = killSwitch, pnlProtect = pnlProtect) }
                }
            }
        }
    }

    fun selectContract(token: String, symbol: String) {
        if (!isReady()) return
        val chain = _ui.value.optionChain
        val valid = chain != null && chain.strikes.any { strike ->
            (strike.callToken == token && strike.callSymbol == symbol) || (strike.putToken == token && strike.putSymbol == symbol)
        }
        if (!valid || token.isBlank() || symbol.isBlank()) {
            _ui.update { it.copy(statusBanner = "Select a valid contract from the loaded option chain.") }
            return
        }
        candleGeneration.incrementAndGet()
        _ui.update {
            it.copy(activeOptionToken = token, activeOptionSymbol = symbol, optionCandles = emptyList(), optionEma = emptyList(), optionSynthetic = null)
        }
        updateMarketSubscription()
        loadCandles()
    }

    fun changeUnderlying(underlying: String) {
        if (!isReady()) return
        chainGeneration.incrementAndGet()
        candleGeneration.incrementAndGet()
        _ui.update {
            it.copy(
                underlying = underlying,
                activeOptionToken = "",
                activeOptionSymbol = "",
                selectedExpiry = null,
                optionChain = null,
                chainLoading = false,
                chainError = null,
                spotCandles = emptyList(),
                optionCandles = emptyList(),
                optionEma = emptyList(),
                spotSynthetic = null,
                optionSynthetic = null,
            )
        }
        updateMarketSubscription()
        loadCandles()
        loadOptionChain()
    }

    fun changeExpiry(expiry: String) {
        if (!isReady()) return
        chainGeneration.incrementAndGet()
        candleGeneration.incrementAndGet()
        _ui.update {
            it.copy(
                selectedExpiry = expiry,
                activeOptionToken = "",
                activeOptionSymbol = "",
                optionChain = null,
                chainLoading = false,
                chainError = null,
                optionCandles = emptyList(),
                optionEma = emptyList(),
                optionSynthetic = null,
            )
        }
        updateMarketSubscription()
        loadCandles()
        loadOptionChain()
    }

    fun setTimeframe(timeframe: String) {
        if (!isReady() || timeframe == _ui.value.timeframe) return
        _ui.update { it.copy(timeframe = timeframe) }
        loadCandles()
    }

    fun placeQuickOrder(side: String, token: String, symbol: String, segment: String = "nse_fo") {
        if (!isReady()) return
        if (!_ui.value.orderDefaults.oneTapEnabled) {
            _ui.update { it.copy(statusBanner = "Order blocked: 1-Tap is OFF") }
            return
        }
        if (token.isBlank() || symbol.isBlank()) {
            _ui.update { it.copy(statusBanner = "Order blocked: no valid contract selected") }
            return
        }
        val key = "$side:$token"
        val session = beginPendingOrder(key) ?: return
        val clientOrderId = retainedClientId(quickOrderClientIds, key, "android-quick")
        if (!isReady(session)) {
            finishPendingOrder(key, session)
            return
        }
        viewModelScope.launch {
            runCatching {
                val response = ApiClient.rest.quickOrder(
                    QuickOrderRequest(
                        instrumentToken = token,
                        exchangeSegment = segment,
                        tradingSymbol = symbol,
                        transactionType = side,
                        clientOrderId = clientOrderId,
                    )
                )
                check(response.ok && response.data.orderId.isNotBlank()) { "Order submission was not confirmed" }
                response
            }.onSuccess { response ->
                if (isReady(session)) {
                    confirmClientId(quickOrderClientIds, key, clientOrderId)
                    _ui.update {
                        it.copy(statusBanner = "${if (side == "B") "BUY" else "SELL"} $symbol -> ${response.data.status}")
                    }
                    refreshPortfolio()
                }
            }.onFailure { error ->
                if (isReady(session)) {
                    _ui.update { it.copy(statusBanner = "Order error: ${errorMessage(error)}") }
                    reconcileAfterOrderFailure(error)
                }
            }
            finishPendingOrder(key, session)
        }
    }

    fun exitPosition(position: PositionRecord) {
        if (!isReady()) return
        if (position.netQty == 0) {
            _ui.update { it.copy(statusBanner = "Exit blocked: position quantity is zero") }
            return
        }
        val identity = positionExitKey(position)
        val key = positionExitPendingKey(position)
        val session = beginPendingOrder(key) ?: return
        val clientOrderId = retainedClientId(positionExitClientIds, identity, "android-exit")
        if (!isReady(session)) {
            finishPendingOrder(key, session)
            return
        }
        val request = PositionExitRequest(
            instrumentToken = position.instrumentToken.takeIf { it.isNotBlank() },
            tradingSymbol = position.tradingSymbol,
            exchangeSegment = position.exchangeSegment,
            product = position.product,
            quantity = position.netQty.absoluteValue,
            transactionType = if (position.netQty > 0) "S" else "B",
            clientOrderId = clientOrderId,
        )
        viewModelScope.launch {
            runCatching {
                val response = ApiClient.rest.exitPosition(request)
                check(response.ok && response.data.orderId.isNotBlank()) { "Position exit was not confirmed" }
                response
            }.onSuccess { response ->
                if (isReady(session)) {
                    confirmClientId(positionExitClientIds, identity, clientOrderId)
                    _ui.update {
                        it.copy(statusBanner = "Exit ${position.tradingSymbol} -> ${response.data.status}")
                    }
                    refreshPortfolio()
                }
            }.onFailure { error ->
                if (isReady(session)) {
                    _ui.update { it.copy(statusBanner = "Exit error: ${errorMessage(error)}") }
                    reconcileAfterOrderFailure(error)
                }
            }
            finishPendingOrder(key, session)
        }
    }

    fun toggleOneTap() {
        if (!isReady()) return
        viewModelScope.launch {
            runCatching {
                val next = _ui.value.orderDefaults.copy(oneTapEnabled = !_ui.value.orderDefaults.oneTapEnabled)
                ApiClient.rest.setOrderDefaults(next).data
            }.onSuccess { saved ->
                if (isReady()) _ui.update { it.copy(orderDefaults = saved) }
            }.onFailure { error ->
                if (isReady()) _ui.update { it.copy(statusBanner = "Could not update 1-Tap: ${errorMessage(error)}") }
            }
        }
    }

    fun toggleKillSwitch() {
        if (!isReady()) return
        viewModelScope.launch {
            runCatching {
                val next = !_ui.value.killSwitch.active
                ApiClient.rest.setKillSwitch(
                    KillSwitchState(active = next, reason = if (next) "Android header toggle" else null),
                ).data
            }.onSuccess { saved ->
                if (isReady()) _ui.update { it.copy(killSwitch = saved, statusBanner = if (saved.active) "Kill Switch ACTIVE" else "Kill Switch OFF") }
            }.onFailure { error ->
                if (isReady()) _ui.update { it.copy(statusBanner = "Kill Switch update failed: ${errorMessage(error)}") }
            }
        }
    }

    fun hotExitAll() {
        if (!isReady()) return
        val session = beginPendingOrder(HOT_EXIT_PENDING_KEY) ?: return
        val clientOrderId = synchronized(orderLock) {
            hotExitClientId ?: "android-hot-exit-${UUID.randomUUID()}".also { hotExitClientId = it }
        }
        if (!isReady(session)) {
            finishPendingOrder(HOT_EXIT_PENDING_KEY, session)
            return
        }
        viewModelScope.launch {
            runCatching {
                val response = ApiClient.rest.hotExitAll(HotExitRequest(clientOrderId = clientOrderId))
                check(response.ok) { "Hot Exit was not confirmed" }
                response
            }.onSuccess { response ->
                if (isReady(session)) {
                    synchronized(orderLock) {
                        if (hotExitClientId == clientOrderId) hotExitClientId = null
                    }
                    _ui.update {
                        it.copy(statusBanner = "Hot Exit accepted (${response.data.size} exit orders)")
                    }
                    refreshPortfolio()
                }
            }.onFailure { error ->
                if (isReady(session)) {
                    _ui.update { it.copy(statusBanner = "Hot Exit failed: ${errorMessage(error)}") }
                    reconcileAfterOrderFailure(error)
                }
            }
            finishPendingOrder(HOT_EXIT_PENDING_KEY, session)
        }
    }

    fun savePnlProtect(config: PnlProtectConfig) {
        if (!isReady()) return
        viewModelScope.launch {
            runCatching { ApiClient.rest.setPnlProtect(config).data }
                .onSuccess { saved -> if (isReady()) _ui.update { it.copy(pnlProtect = saved, statusBanner = "P&L Protect updated") } }
                .onFailure { error -> if (isReady()) _ui.update { it.copy(statusBanner = "P&L Protect save failed: ${errorMessage(error)}") } }
        }
    }

    fun login(request: LoginRequest, onDone: () -> Unit) {
        if (!isReady()) return
        val session = sessionGeneration.get()
        viewModelScope.launch {
            runCatching { ApiClient.rest.login(request).data }
                .onSuccess { result ->
                    if (!isReady(session)) return@onSuccess
                    updateBrokerIdentity(result.mode, result.ucc)
                    _ui.update { it.copy(mode = result.mode, ucc = result.ucc, statusBanner = result.message) }
                    refreshAll()
                    onDone()
                }
                .onFailure { error -> if (isReady(session)) _ui.update { it.copy(statusBanner = "Login failed: ${errorMessage(error)}") } }
        }
    }

    fun clearBanner() {
        _ui.update { it.copy(statusBanner = null) }
    }

    override fun onCleared() {
        ApiClient.unauthorizedHandler = null
        sessionGeneration.incrementAndGet()
        brokerSessionGeneration.incrementAndGet()
        stopTerminal()
        super.onCleared()
    }

    private fun atmCall(chain: OptionChainData): Pair<String, String>? {
        val candidates = chain.strikes.filter { !it.callToken.isNullOrBlank() && !it.callSymbol.isNullOrBlank() }
        val strike = candidates.firstOrNull { it.isAtm }
            ?: candidates.minByOrNull { kotlin.math.abs(it.strikePrice - chain.spotPrice) }
        return strike?.let { it.callToken!! to it.callSymbol!! }
    }

    private fun mergeTick(candles: List<Candle>, quote: QuoteData, timeframe: String): List<Candle> {
        if (candles.isEmpty() || !quote.ltp.isFinite()) return candles
        val rawTime = if (quote.updatedAt > 1_000_000_000_000L) quote.updatedAt / 1_000L else quote.updatedAt
        val normalizedTimeframe = timeframe.trim().lowercase()
        val step = when {
            normalizedTimeframe == "1d" -> 86_400L
            normalizedTimeframe.endsWith("h") ->
                (normalizedTimeframe.removeSuffix("h").toLongOrNull() ?: 1L).coerceAtLeast(1L) * 3_600L
            normalizedTimeframe.endsWith("m") ->
                (normalizedTimeframe.removeSuffix("m").toLongOrNull() ?: 1L).coerceAtLeast(1L) * 60L
            else -> 60L
        }
        val bucket = rawTime / step * step
        val previous = candles.last()
        if (bucket < previous.time) return candles
        if (bucket == previous.time) {
            val updated = previous.copy(high = maxOf(previous.high, quote.ltp), low = minOf(previous.low, quote.ltp), close = quote.ltp)
            return candles.dropLast(1) + updated
        }
        return candles + Candle(bucket, quote.ltp, quote.ltp, quote.ltp, quote.ltp, 0L)
    }

    private fun mergeChainTicks(chain: OptionChainData?, quotes: Map<String, QuoteData>): OptionChainData? = chain?.let { current ->
        current.copy(strikes = current.strikes.map { strike ->
            val call = strike.callToken?.let(quotes::get)
            val put = strike.putToken?.let(quotes::get)
            strike.copy(
                callLtp = call?.ltp ?: strike.callLtp,
                callOi = call?.oi ?: strike.callOi,
                putLtp = put?.ltp ?: strike.putLtp,
                putOi = put?.oi ?: strike.putOi,
            )
        })
    }
}
