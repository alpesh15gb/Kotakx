package com.kotakneo.terminal.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

val ObsidianBg = Color(0xFF0A0C10)
val PanelBg = Color(0xFF11141B)
val PanelBorder = Color(0xFF1E232E)
val BullGreen = Color(0xFF16C784)
val BearRed = Color(0xFFEA3943)
val AccentBlue = Color(0xFF3B82F6)
val WarnAmber = Color(0xFFF59E0B)

private val DarkScheme = darkColorScheme(
    primary = AccentBlue,
    secondary = BullGreen,
    error = BearRed,
    background = ObsidianBg,
    surface = PanelBg,
    onPrimary = Color.White,
    onBackground = Color(0xFFE6E8EC),
    onSurface = Color(0xFFE6E8EC),
)

@Composable
fun KotakTerminalTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DarkScheme,
        content = content,
    )
}
