package com.kotakneo.terminal.data.api

import com.google.gson.Gson
import com.kotakneo.terminal.BuildConfig
import com.kotakneo.terminal.data.model.*
import okhttp3.Authenticator
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import retrofit2.http.*
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.ThreadFactory
import java.util.concurrent.TimeUnit
import kotlin.random.Random

interface TerminalRestService {
    @GET("api/v1/auth/status")
    suspend fun getAuthStatus(): AccessAuthStatus

    @GET("api/v1/session")
    suspend fun getSession(): Envelope<AuthSessionData>

    @POST("api/v1/auth/login")
    suspend fun accessLogin(@Body body: AccessLoginRequest): AccessAuthResponse

    @POST("api/v1/auth/logout")
    suspend fun accessLogout(): AccessAuthResponse

    @POST("api/v1/login")
    suspend fun login(@Body body: LoginRequest): Envelope<AuthSessionData>

    @GET("api/v1/candles")
    suspend fun getCandles(
        @Query("instrument") instrument: String,
        @Query("timeframe") timeframe: String = "1m",
        @Query("indicators") indicators: String = "ema20,vwap",
    ): CandlesResponse

    @GET("api/v1/optchain-data/{underlying}")
    suspend fun getOptionChain(
        @Path("underlying") underlying: String,
        @Query("expiry") expiry: String? = null,
    ): Envelope<OptionChainData>

    @POST("api/orders/v1/quick")
    suspend fun quickOrder(@Body body: QuickOrderRequest): Envelope<OrderRecord>

    @DELETE("api/orders/v1/orders/{orderId}")
    suspend fun cancelOrder(@Path("orderId") orderId: String): Envelope<Map<String, Any>>

    @GET("api/orders/v1/orderbook")
    suspend fun getOrderBook(): Envelope<List<OrderRecord>>

    @GET("api/orders/v1/positions")
    suspend fun getPositions(): PositionsResponse

    @POST("api/orders/v1/positions/exit-order")
    suspend fun exitPosition(@Body body: PositionExitRequest): Envelope<OrderRecord>

    @POST("api/orders/v1/exit-orders")
    suspend fun hotExitAll(@Body body: HotExitRequest): Envelope<List<OrderRecord>>

    @GET("api/orders/v1/holdings")
    suspend fun getHoldings(): Envelope<List<HoldingRecord>>

    @GET("api/orders/v1/order-defaults")
    suspend fun getOrderDefaults(): Envelope<OrderDefaults>

    @PUT("api/orders/v1/order-defaults")
    suspend fun setOrderDefaults(@Body body: OrderDefaults): Envelope<OrderDefaults>

    @GET("api/orders/v1/kill-switch")
    suspend fun getKillSwitch(): Envelope<KillSwitchState>

    @POST("api/orders/v1/kill-switch")
    suspend fun setKillSwitch(@Body body: KillSwitchState): Envelope<KillSwitchState>

    @GET("api/orders/v1/pnl-protects")
    suspend fun getPnlProtect(): Envelope<PnlProtectConfig>

    @PUT("api/orders/v1/pnl-protects")
    suspend fun setPnlProtect(@Body body: PnlProtectConfig): Envelope<PnlProtectConfig>
}

/** Reconnects both feed types and replays the most recent subscription after every handshake. */
class ReconnectingFeed(
    private val client: OkHttpClient,
    private val url: String,
    private val onMessage: (String) -> Unit,
    private val onOpen: (WebSocket) -> Unit = {},
    private val onStateChange: (Boolean) -> Unit = {},
) {
    private val lock = Any()
    private val scheduler = Executors.newSingleThreadScheduledExecutor(ThreadFactory { runnable ->
        Thread(runnable, "terminal-feed-reconnect").apply { isDaemon = true }
    })
    private var socket: WebSocket? = null
    private var connected = false
    private var subscriptionJson: String? = null
    private var reconnectScheduled = false
    private var connectionGeneration = 0L
    private var attempt = 0
    @Volatile private var closed = false

    init {
        connect()
    }

    fun updateSubscription(json: String) {
        synchronized(lock) {
            if (closed) return
            subscriptionJson = json
            if (connected) socket?.send(json)
        }
    }

    private fun connect() {
        // The listener captures its generation before newWebSocket returns. OkHttp may invoke
        // onOpen before the caller can assign `socket`, so callbacks must not depend on that assignment.
        val generation = synchronized(lock) {
            if (closed) return
            connectionGeneration += 1
            connectionGeneration
        }
        onStateChange(false)
        val request = Request.Builder().url(url).build()
        val webSocket = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                synchronized(lock) {
                    if (closed || generation != connectionGeneration) return
                    socket = webSocket
                    connected = true
                    attempt = 0
                    subscriptionJson?.let { webSocket.send(it) }
                    onStateChange(true)
                }
                onOpen(webSocket)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val active = synchronized(lock) { !closed && generation == connectionGeneration }
                if (active) onMessage(text)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                scheduleReconnect(generation)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                scheduleReconnect(generation)
            }
        })
        synchronized(lock) {
            if (closed || generation != connectionGeneration || reconnectScheduled) {
                webSocket.cancel()
                return
            }
            socket = webSocket
        }
    }

    private fun scheduleReconnect(expectedGeneration: Long) {
        synchronized(lock) {
            if (closed || expectedGeneration != connectionGeneration || reconnectScheduled) return
            socket = null
            connected = false
            reconnectScheduled = true
            onStateChange(false)
            val base = minOf(30_000L, 1_000L shl attempt.coerceAtMost(5))
            attempt += 1
            val delay = base + Random.nextLong((base / 2).coerceAtLeast(1L))
            scheduler.schedule({
                synchronized(lock) { reconnectScheduled = false }
                connect()
            }, delay, TimeUnit.MILLISECONDS)
        }
    }

    fun close() {
        val webSocket = synchronized(lock) {
            if (closed) return
            closed = true
            connected = false
            socket.also { socket = null }
        }
        scheduler.shutdownNow()
        webSocket?.cancel()
        onStateChange(false)
    }
}

object ApiClient {
    val gson = Gson()

    // In-memory only: the HttpOnly access cookie is shared by Retrofit and both WebSockets.
    private val cookies = ConcurrentHashMap<String, Cookie>()
    private fun cookieKey(cookie: Cookie) = "${cookie.name}@${cookie.domain}@${cookie.path}"
    val cookieJar: CookieJar = object : CookieJar {
        override fun saveFromResponse(url: HttpUrl, responseCookies: List<Cookie>) {
            responseCookies.forEach { cookie ->
                if (cookie.expiresAt < System.currentTimeMillis()) cookies.remove(cookieKey(cookie))
                else cookies[cookieKey(cookie)] = cookie
            }
        }

        override fun loadForRequest(url: HttpUrl): List<Cookie> = cookies.values.filter { cookie ->
            cookie.expiresAt >= System.currentTimeMillis() && cookie.matches(url)
        }
    }

    fun clearCookies() = cookies.clear()

    @Volatile
    var unauthorizedHandler: (() -> Unit)? = null

    private val authenticator = Authenticator { _, response ->
        val isPasswordLogin = response.request.url.encodedPath.endsWith("/api/v1/auth/login")
        if (response.code == 401 && !isPasswordLogin) {
            clearCookies()
            unauthorizedHandler?.invoke()
        }
        null
    }

    val okHttp: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(20, TimeUnit.SECONDS)
        .cookieJar(cookieJar)
        .authenticator(authenticator)
        .build()

    init {
        require(BuildConfig.API_BASE_URL.isNotBlank()) { "Set RELEASE_API_BASE_URL for release builds" }
        require(BuildConfig.WS_BASE_URL.isNotBlank()) { "Set RELEASE_WS_BASE_URL for release builds" }
    }

    val rest: TerminalRestService = Retrofit.Builder()
        .baseUrl(BuildConfig.API_BASE_URL)
        .client(okHttp)
        .addConverterFactory(GsonConverterFactory.create(gson))
        .build()
        .create(TerminalRestService::class.java)

}
