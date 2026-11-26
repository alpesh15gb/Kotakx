package com.kotakneo.terminal.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kotakneo.terminal.ui.TerminalUiState
import com.kotakneo.terminal.ui.theme.*

@Composable
private fun ChainQuickButton(
    side: String,
    token: String,
    symbol: String,
    enabled: Boolean,
    pending: Boolean,
    onQuickTrade: (String, String, String) -> Unit,
) {
    val color = if (side == "B") BullGreen else BearRed
    val modifier = if (enabled && !pending) {
        Modifier.clickable { onQuickTrade(side, token, symbol) }
    } else Modifier
    Text(
        if (enabled) side else "OFF",
        color = if (enabled) (if (side == "B") Color.Black else Color.White) else Color.Gray,
        fontSize = 10.sp,
        fontWeight = FontWeight.Bold,
        modifier = modifier
            .background(if (enabled) color.copy(alpha = if (pending) 0.35f else 1f) else PanelBorder, RoundedCornerShape(3.dp))
            .padding(horizontal = 5.dp, vertical = 2.dp),
    )
}

@Composable
fun OptionChainScreen(
    state: TerminalUiState,
    onSelectAndOpenScalper: (String, String) -> Unit,
    onQuickTrade: (String, String, String) -> Unit,
    onChangeExpiry: (String) -> Unit,
) {
    val chain = state.optionChain
    if (chain == null) {
        Column(
            modifier = Modifier.fillMaxSize().padding(16.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text(if (state.chainLoading) "Loading Option Chain..." else "No valid option chain loaded", color = Color.Gray)
            state.chainError?.let { Text(it, color = BearRed, fontSize = 11.sp) }
        }
        return
    }

    Column(Modifier.fillMaxSize().padding(10.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                "${chain.underlying} (${chain.expiry}) • Spot ₹%.2f".format(chain.spotPrice),
                color = Color.White,
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold,
            )
            Text(
                "PCR: %.2f".format(chain.pcr),
                color = WarnAmber,
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
            )
        }

        Row(
            modifier = Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(bottom = 8.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            chain.availableExpiries.ifEmpty { listOf(chain.expiry) }.forEach { expiry ->
                FilterChip(
                    selected = expiry == chain.expiry,
                    onClick = { if (expiry != chain.expiry) onChangeExpiry(expiry) },
                    label = { Text(expiry, fontSize = 10.sp) },
                )
            }
        }

        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(PanelBg)
                .padding(vertical = 6.dp, horizontal = 4.dp),
        ) {
            Text("CE OI/IV", color = Color.Gray, fontSize = 10.sp, modifier = Modifier.weight(1f))
            Text("CE LTP", color = Color.Gray, fontSize = 10.sp, modifier = Modifier.weight(1f), textAlign = TextAlign.End)
            Text("STRIKE", color = Color.White, fontSize = 10.sp, fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f), textAlign = TextAlign.Center)
            Text("PE LTP", color = Color.Gray, fontSize = 10.sp, modifier = Modifier.weight(1f))
            Text("PE OI/IV", color = Color.Gray, fontSize = 10.sp, modifier = Modifier.weight(1f), textAlign = TextAlign.End)
        }

        LazyColumn(modifier = Modifier.fillMaxSize()) {
            items(chain.strikes) { row ->
                val bg = if (row.isAtm) WarnAmber.copy(alpha = 0.12f) else ObsidianBg
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .background(bg)
                        .padding(vertical = 8.dp, horizontal = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("%.1fL".format(row.callOi / 100000.0), color = Color.LightGray, fontSize = 10.sp)
                        Text("%.1f%% Δ%.2f".format(row.callIv, row.callDelta), color = Color.Gray, fontSize = 9.sp)
                    }
                    Row(
                        modifier = Modifier.weight(1f),
                        horizontalArrangement = Arrangement.End,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(
                            "₹%.1f".format(row.callLtp),
                            color = Color.White,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.clickable {
                                if (row.callToken != null && row.callSymbol != null) {
                                    onSelectAndOpenScalper(row.callToken, row.callSymbol)
                                }
                            },
                        )
                        Spacer(Modifier.width(4.dp))
                        if (row.callToken != null && row.callSymbol != null) {
                            ChainQuickButton(
                                side = "B",
                                token = row.callToken,
                                symbol = row.callSymbol,
                                enabled = state.orderDefaults.oneTapEnabled,
                                pending = state.pendingOrderKeys.any { it.endsWith(":${row.callToken}") },
                                onQuickTrade = onQuickTrade,
                            )
                        }
                    }

                    Text(
                        "${row.strikePrice.toInt()}",
                        color = if (row.isAtm) WarnAmber else Color.White,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.weight(1f),
                    )

                    Row(
                        modifier = Modifier.weight(1f),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        if (row.putToken != null && row.putSymbol != null) {
                            ChainQuickButton(
                                side = "B",
                                token = row.putToken,
                                symbol = row.putSymbol,
                                enabled = state.orderDefaults.oneTapEnabled,
                                pending = state.pendingOrderKeys.any { it.endsWith(":${row.putToken}") },
                                onQuickTrade = onQuickTrade,
                            )
                        }
                        Spacer(Modifier.width(4.dp))
                        Text(
                            "₹%.1f".format(row.putLtp),
                            color = Color.White,
                            fontSize = 12.sp,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.clickable {
                                if (row.putToken != null && row.putSymbol != null) {
                                    onSelectAndOpenScalper(row.putToken, row.putSymbol)
                                }
                            },
                        )
                    }
                    Column(Modifier.weight(1f), horizontalAlignment = Alignment.End) {
                        Text("%.1fL".format(row.putOi / 100000.0), color = Color.LightGray, fontSize = 10.sp)
                        Text("%.1f%% Δ%.2f".format(row.putIv, row.putDelta), color = Color.Gray, fontSize = 9.sp)
                    }
                }
                HorizontalDivider(color = PanelBorder)
            }
        }
    }
}
