package com.kotakneo.terminal.ui.screens

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kotakneo.terminal.data.model.Candle
import com.kotakneo.terminal.data.model.IndicatorPoint
import com.kotakneo.terminal.ui.TerminalUiState
import com.kotakneo.terminal.ui.theme.*
import kotlin.math.max
import kotlin.math.min

@Composable
fun ScalperScreen(
    state: TerminalUiState,
    onChangeUnderlying: (String) -> Unit,
    onSelectContract: (String, String) -> Unit,
    onQuickTrade: (String, String, String) -> Unit,
) {
    val spotTok = if (state.underlying == "NIFTY") "NIFTY 50" else "NIFTY BANK"
    val spotQuote = state.quotes[spotTok]
    val optQuote = state.quotes[state.activeOptionToken]

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        // Index Switcher + Spot LTP
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf("NIFTY", "BANKNIFTY").forEach { u ->
                    val selected = state.underlying == u
                    Box(
                        modifier = Modifier
                            .background(if (selected) AccentBlue else PanelBg, RoundedCornerShape(6.dp))
                            .border(1.dp, PanelBorder, RoundedCornerShape(6.dp))
                            .clickable { onChangeUnderlying(u) }
                            .padding(horizontal = 12.dp, vertical = 6.dp),
                    ) {
                        Text(u, color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.Bold)
                    }
                }
            }
            if (spotQuote != null) {
                Text(
                    text = "Spot ₹%.2f (%.2f%%)".format(spotQuote.ltp, spotQuote.changePct),
                    color = if (spotQuote.changePct >= 0) BullGreen else BearRed,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.Bold,
                )
            }
        }

        // Spot Mini Chart
        Card(
            colors = CardDefaults.cardColors(containerColor = PanelBg),
            modifier = Modifier
                .fillMaxWidth()
                .height(170.dp),
        ) {
            Column(Modifier.padding(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("${state.underlying} Spot (${state.timeframe})", color = Color.Gray, fontSize = 11.sp)
                    state.spotSynthetic?.let { synthetic ->
                        Spacer(Modifier.width(6.dp))
                        Text(if (synthetic) "SYNTHETIC" else "LIVE", color = if (synthetic) WarnAmber else BullGreen, fontSize = 9.sp, fontWeight = FontWeight.Bold)
                    }
                }
                CandlestickCanvas(
                    candles = state.spotCandles.takeLast(55),
                    ema = emptyList(),
                    modifier = Modifier.fillMaxSize(),
                )
            }
        }

        // Quick ATM Strike Strip
        val strikes = state.optionChain?.strikes.orEmpty()
        val atmIdx = strikes.indexOfFirst { it.isAtm }.coerceAtLeast(0)
        val nearby = if (strikes.isNotEmpty()) strikes.subList(max(0, atmIdx - 2), min(strikes.size, atmIdx + 3)) else emptyList()
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            nearby.forEach { s ->
                s.callToken?.let { tok ->
                    FilterChip(
                        selected = state.activeOptionToken == tok,
                        onClick = { s.callSymbol?.let { symbol -> onSelectContract(tok, symbol) } },
                        label = { Text("${s.strikePrice.toInt()} CE", fontSize = 11.sp) },
                    )
                }
                s.putToken?.let { tok ->
                    FilterChip(
                        selected = state.activeOptionToken == tok,
                        onClick = { s.putSymbol?.let { symbol -> onSelectContract(tok, symbol) } },
                        label = { Text("${s.strikePrice.toInt()} PE", fontSize = 11.sp) },
                    )
                }
            }
        }

        // Active Option Chart + 1-Tap Buy / Sell
        Card(
            colors = CardDefaults.cardColors(containerColor = PanelBg),
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f),
        ) {
            Column(Modifier.padding(10.dp)) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            state.activeOptionSymbol.ifBlank { "Loading ATM contract..." },
                            color = Color.White,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                        )
                        state.optionSynthetic?.let { synthetic ->
                            Spacer(Modifier.width(6.dp))
                            Text(if (synthetic) "SYNTHETIC" else "LIVE", color = if (synthetic) WarnAmber else BullGreen, fontSize = 9.sp, fontWeight = FontWeight.Bold)
                        }
                    }
                    if (optQuote != null) {
                        Text(
                            "₹%.2f".format(optQuote.ltp),
                            color = if (optQuote.changePct >= 0) BullGreen else BearRed,
                            fontSize = 14.sp,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                }
                Spacer(Modifier.height(6.dp))
                CandlestickCanvas(
                    candles = state.optionCandles.takeLast(55),
                    ema = state.optionEma.takeLast(55),
                    modifier = Modifier
                        .fillMaxWidth()
                        .weight(1f),
                )
                state.chainError?.let {
                    Spacer(Modifier.height(4.dp))
                    Text("Option chain: $it", color = BearRed, fontSize = 10.sp)
                }
                Spacer(Modifier.height(10.dp))
                val quickPending = state.pendingOrderKeys.any { it.endsWith(":${state.activeOptionToken}") }
                val quickEnabled = state.orderDefaults.oneTapEnabled && state.activeOptionToken.isNotBlank() && !quickPending
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Button(
                        onClick = { onQuickTrade("B", state.activeOptionToken, state.activeOptionSymbol) },
                        colors = ButtonDefaults.buttonColors(containerColor = BullGreen, contentColor = Color.Black),
                        enabled = quickEnabled,
                        modifier = Modifier.weight(1f),
                    ) {
                        Text(
                            when {
                                quickPending -> "ORDER PENDING"
                                state.activeOptionToken.isBlank() -> "LOADING CONTRACT"
                                !state.orderDefaults.oneTapEnabled -> "BUY (1-TAP OFF)"
                                else -> "1-TAP BUY (${state.orderDefaults.defaultLots}L)"
                            },
                            fontWeight = FontWeight.Bold,
                        )
                    }
                    Button(
                        onClick = { onQuickTrade("S", state.activeOptionToken, state.activeOptionSymbol) },
                        colors = ButtonDefaults.buttonColors(containerColor = BearRed, contentColor = Color.White),
                        enabled = quickEnabled,
                        modifier = Modifier.weight(1f),
                    ) {
                        Text(
                            when {
                                quickPending -> "ORDER PENDING"
                                state.activeOptionToken.isBlank() -> "LOADING CONTRACT"
                                !state.orderDefaults.oneTapEnabled -> "SELL (1-TAP OFF)"
                                else -> "1-TAP SELL (${state.orderDefaults.defaultLots}L)"
                            },
                            fontWeight = FontWeight.Bold,
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun CandlestickCanvas(
    candles: List<Candle>,
    ema: List<IndicatorPoint>,
    modifier: Modifier = Modifier,
) {
    Canvas(modifier = modifier) {
        if (candles.isEmpty()) return@Canvas
        val minLow = candles.minOf { it.low }
        val maxHigh = candles.maxOf { it.high }
        val range = max(0.05, maxHigh - minLow)
        val wPerBar = size.width / candles.size.toFloat()
        val bodyWidth = max(2f, wPerBar * 0.6f)

        fun yFor(price: Double): Float {
            val norm = ((price - minLow) / range).toFloat()
            return size.height - norm * size.height
        }

        candles.forEachIndexed { idx, c ->
            val cx = idx * wPerBar + wPerBar / 2f
            val color = if (c.close >= c.open) BullGreen else BearRed
            drawLine(
                color = color,
                start = Offset(cx, yFor(c.high)),
                end = Offset(cx, yFor(c.low)),
                strokeWidth = 2f,
            )
            val topY = yFor(max(c.open, c.close))
            val botY = yFor(min(c.open, c.close))
            drawRect(
                color = color,
                topLeft = Offset(cx - bodyWidth / 2f, topY),
                size = Size(bodyWidth, max(2f, botY - topY)),
            )
        }

        if (ema.size >= 2) {
            val path = Path()
            ema.forEachIndexed { idx, pt ->
                val cx = idx * wPerBar + wPerBar / 2f
                val py = yFor(pt.value)
                if (idx == 0) path.moveTo(cx, py) else path.lineTo(cx, py)
            }
            drawPath(path = path, color = AccentBlue, style = Stroke(width = 2.5f))
        }
    }
}
