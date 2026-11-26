package com.kotakneo.terminal.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kotakneo.terminal.data.model.PnlProtectConfig
import com.kotakneo.terminal.data.model.PositionRecord
import com.kotakneo.terminal.ui.HOT_EXIT_PENDING_KEY
import com.kotakneo.terminal.ui.TerminalUiState
import com.kotakneo.terminal.ui.positionExitPendingKey
import com.kotakneo.terminal.ui.theme.*

@Composable
fun PortfolioScreen(
    state: TerminalUiState,
    onHotExitAll: () -> Unit,
    onExitPosition: (PositionRecord) -> Unit,
    onSavePnlProtect: (PnlProtectConfig) -> Unit,
) {
    var tab by remember { mutableIntStateOf(0) }
    var maxLossInput by remember(state.pnlProtect) { mutableStateOf(state.pnlProtect.maxLoss.toString()) }
    var maxProfitInput by remember(state.pnlProtect) { mutableStateOf(state.pnlProtect.maxProfit.toString()) }
    var protectEnabled by remember(state.pnlProtect) { mutableStateOf(state.pnlProtect.enabled) }
    var trailingEnabled by remember(state.pnlProtect) { mutableStateOf(state.pnlProtect.trailingSlEnabled) }
    var trailingStep by remember(state.pnlProtect) { mutableStateOf(state.pnlProtect.trailingSlStep.toString()) }
    var lockProfitAt by remember(state.pnlProtect) { mutableStateOf(state.pnlProtect.lockProfitAt.toString()) }
    var autoKillSwitch by remember(state.pnlProtect) { mutableStateOf(state.pnlProtect.autoActivateKillSwitch) }

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        TabRow(selectedTabIndex = tab, containerColor = PanelBg) {
            listOf("Positions", "Orders", "Holdings", "P&L Protect").forEachIndexed { idx, label ->
                Tab(selected = tab == idx, onClick = { tab = idx }, text = { Text(label, fontSize = 11.sp) })
            }
        }
        Spacer(Modifier.height(10.dp))

        when (tab) {
            0 -> {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        "Total P&L: ₹%.2f".format(state.totalPnl),
                        color = if (state.totalPnl >= 0) BullGreen else BearRed,
                        fontWeight = FontWeight.Bold,
                    )
                    val hotExitPending = HOT_EXIT_PENDING_KEY in state.pendingOrderKeys
                    Button(
                        onClick = onHotExitAll,
                        enabled = !hotExitPending,
                        colors = ButtonDefaults.buttonColors(containerColor = BearRed),
                    ) {
                        Text(
                            if (hotExitPending) "EXITING..." else "Hot Exit All",
                            fontSize = 12.sp,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                }
                Spacer(Modifier.height(8.dp))
                LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    items(state.positions) { pos ->
                        Card(colors = CardDefaults.cardColors(containerColor = PanelBg)) {
                            Row(
                                Modifier.fillMaxWidth().padding(12.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                            ) {
                                Column {
                                    Text(pos.tradingSymbol, color = Color.White, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                                    Text("Qty ${pos.netQty} • ${pos.product} • Avg ₹%.2f".format(pos.avgPrice), color = Color.Gray, fontSize = 11.sp)
                                }
                                Column(horizontalAlignment = Alignment.End) {
                                    Text(
                                        "₹%.2f".format(pos.pnl),
                                        color = if (pos.pnl >= 0) BullGreen else BearRed,
                                        fontWeight = FontWeight.Bold,
                                    )
                                    Text("LTP ₹%.2f".format(pos.ltp), color = Color.LightGray, fontSize = 11.sp)
                                    val exitPending = positionExitPendingKey(pos) in state.pendingOrderKeys
                                    TextButton(
                                        onClick = { onExitPosition(pos) },
                                        enabled = pos.netQty != 0 && !exitPending,
                                    ) {
                                        Text(
                                            if (exitPending) "EXITING..." else "EXIT",
                                            color = BearRed,
                                            fontSize = 10.sp,
                                            fontWeight = FontWeight.Bold,
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }

            1 -> LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(state.orders) { ord ->
                    Card(colors = CardDefaults.cardColors(containerColor = PanelBg)) {
                        Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                            Column {
                                Text("${ord.transactionType} ${ord.tradingSymbol}", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 13.sp)
                                Text("${ord.product}/${ord.orderType} • Qty ${ord.filledQuantity}/${ord.quantity}", color = Color.Gray, fontSize = 11.sp)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(ord.status.uppercase(), color = AccentBlue, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                                Text("₹%.2f".format(if (ord.avgPrice > 0) ord.avgPrice else ord.price), color = Color.LightGray, fontSize = 11.sp)
                            }
                        }
                    }
                }
            }

            2 -> LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(state.holdings) { h ->
                    Card(colors = CardDefaults.cardColors(containerColor = PanelBg)) {
                        Row(Modifier.fillMaxWidth().padding(12.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                            Column {
                                Text(h.tradingSymbol, color = Color.White, fontWeight = FontWeight.Bold)
                                Text("Qty ${h.quantity} • Avg ₹%.2f".format(h.averagePrice), color = Color.Gray, fontSize = 11.sp)
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text("₹%.2f (%.1f%%)".format(h.pnl, h.pnlPct), color = if (h.pnl >= 0) BullGreen else BearRed, fontWeight = FontWeight.Bold)
                                Text("Val ₹%.2f".format(h.mktValue), color = Color.LightGray, fontSize = 11.sp)
                            }
                        }
                    }
                }
            }

            3 -> Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text("P&L Protect Auto Square-Off", color = Color.White, fontWeight = FontWeight.Bold)
                OutlinedTextField(
                    value = maxLossInput,
                    onValueChange = { maxLossInput = it },
                    label = { Text("Max Loss (₹, negative)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = maxProfitInput,
                    onValueChange = { maxProfitInput = it },
                    label = { Text("Max Profit Target (₹)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = protectEnabled, onCheckedChange = { protectEnabled = it })
                    Text("Enable Auto Square-Off", color = Color.White)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = trailingEnabled, onCheckedChange = { trailingEnabled = it })
                    Text("Trailing Stop Loss", color = Color.White)
                }
                OutlinedTextField(
                    value = trailingStep,
                    onValueChange = { trailingStep = it },
                    label = { Text("Trailing SL Step (₹)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = lockProfitAt,
                    onValueChange = { lockProfitAt = it },
                    label = { Text("Lock Profit At (₹)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = autoKillSwitch, onCheckedChange = { autoKillSwitch = it })
                    Text("Auto-Activate Kill Switch on Trigger", color = Color.White)
                }
                if (state.pnlProtect.triggered) {
                    Text("Triggered at ${state.pnlProtect.triggeredAt ?: "unknown time"}; peak P&L ₹${state.pnlProtect.peakPnl}", color = WarnAmber, fontSize = 11.sp)
                }
                Button(
                    onClick = {
                        onSavePnlProtect(
                            state.pnlProtect.copy(
                                enabled = protectEnabled,
                                maxLoss = maxLossInput.toDoubleOrNull() ?: state.pnlProtect.maxLoss,
                                maxProfit = maxProfitInput.toDoubleOrNull() ?: state.pnlProtect.maxProfit,
                                trailingSlEnabled = trailingEnabled,
                                trailingSlStep = trailingStep.toDoubleOrNull() ?: state.pnlProtect.trailingSlStep,
                                lockProfitAt = lockProfitAt.toDoubleOrNull() ?: state.pnlProtect.lockProfitAt,
                                autoActivateKillSwitch = autoKillSwitch,
                            )
                        )
                    },
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text("Save P&L Protect")
                }
            }
        }
    }
}
