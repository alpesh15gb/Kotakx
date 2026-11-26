package com.kotakneo.terminal

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Layers
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.PieChart
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.kotakneo.terminal.ui.TerminalViewModel
import com.kotakneo.terminal.ui.screens.AccessGate
import com.kotakneo.terminal.ui.screens.LoginDialog
import com.kotakneo.terminal.ui.screens.OptionChainScreen
import com.kotakneo.terminal.ui.screens.PortfolioScreen
import com.kotakneo.terminal.ui.screens.ScalperScreen
import com.kotakneo.terminal.ui.theme.*

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            KotakTerminalTheme {
                TerminalApp()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TerminalApp(vm: TerminalViewModel = viewModel()) {
    val state by vm.ui.collectAsState()
    var navIndex by remember { mutableIntStateOf(0) }
    var showLogin by remember { mutableStateOf(false) }

    // Trading content and feeds must never render before the server-verified access gate opens.
    if (state.accessState != com.kotakneo.terminal.ui.AccessState.READY) {
        AccessGate(
            state = state.accessState,
            error = state.accessError,
            onSubmit = vm::unlock,
            onRetry = vm::checkAccess,
        )
        return
    }

    Scaffold(
        containerColor = ObsidianBg,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(containerColor = PanelBg),
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("KotakNeo", color = Color.White, fontWeight = FontWeight.Bold, fontSize = 16.sp)
                        Text(
                            "P&L ₹%.0f".format(state.totalPnl),
                            color = if (state.totalPnl >= 0) BullGreen else BearRed,
                            fontSize = 13.sp,
                            fontWeight = FontWeight.Bold,
                        )
                    }
                },
                actions = {
                    TextButton(onClick = { vm.toggleOneTap() }) {
                        Text(
                            if (state.orderDefaults.oneTapEnabled) "1-Tap ON" else "1-Tap OFF",
                            color = if (state.orderDefaults.oneTapEnabled) WarnAmber else Color.Gray,
                            fontSize = 11.sp,
                        )
                    }
                    TextButton(onClick = { vm.toggleKillSwitch() }) {
                        Text(
                            if (state.killSwitch.active) "KS ON" else "KS OFF",
                            color = if (state.killSwitch.active) BearRed else Color.Gray,
                            fontSize = 11.sp,
                        )
                    }
                    Text(
                        state.ucc,
                        color = Color.White,
                        fontSize = 12.sp,
                        modifier = Modifier
                            .clickable { showLogin = true }
                            .padding(horizontal = 10.dp),
                    )
                    IconButton(onClick = vm::lockTerminal) {
                        Icon(Icons.Default.Lock, contentDescription = "Lock terminal", tint = Color.Gray)
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar(containerColor = PanelBg) {
                NavigationBarItem(
                    selected = navIndex == 0,
                    onClick = { navIndex = 0 },
                    icon = { Icon(Icons.Default.Bolt, contentDescription = "Scalper") },
                    label = { Text("Scalper") },
                )
                NavigationBarItem(
                    selected = navIndex == 1,
                    onClick = { navIndex = 1 },
                    icon = { Icon(Icons.Default.Layers, contentDescription = "Options") },
                    label = { Text("Option Chain") },
                )
                NavigationBarItem(
                    selected = navIndex == 2,
                    onClick = { navIndex = 2 },
                    icon = { Icon(Icons.Default.PieChart, contentDescription = "Portfolio") },
                    label = { Text("Portfolio") },
                )
            }
        },
    ) { padding ->
        Box(Modifier.padding(padding).fillMaxSize().background(ObsidianBg)) {
            when (navIndex) {
                0 -> ScalperScreen(
                    state = state,
                    onChangeUnderlying = vm::changeUnderlying,
                    onSelectContract = vm::selectContract,
                    onQuickTrade = { side, tok, sym -> vm.placeQuickOrder(side, tok, sym) },
                )
                1 -> OptionChainScreen(
                    state = state,
                    onSelectAndOpenScalper = { tok, sym ->
                        vm.selectContract(tok, sym)
                        navIndex = 0
                    },
                    onQuickTrade = { side, tok, sym -> vm.placeQuickOrder(side, tok, sym) },
                    onChangeExpiry = vm::changeExpiry,
                )
                2 -> PortfolioScreen(
                    state = state,
                    onHotExitAll = vm::hotExitAll,
                    onExitPosition = vm::exitPosition,
                    onSavePnlProtect = vm::savePnlProtect,
                )
            }

            state.statusBanner?.let { msg ->
                Snackbar(
                    modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp),
                    action = { TextButton(onClick = vm::clearBanner) { Text("OK") } },
                ) {
                    Text(msg)
                }
            }
        }
    }

    if (showLogin) {
        LoginDialog(
            currentMode = state.mode,
            onDismiss = { showLogin = false },
            onSubmit = { req -> vm.login(req) { showLogin = false } },
        )
    }
}
