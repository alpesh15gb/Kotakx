package com.kotakneo.terminal.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.kotakneo.terminal.data.model.LoginRequest
import com.kotakneo.terminal.ui.AccessState
import com.kotakneo.terminal.ui.theme.*

@Composable
fun AccessGate(
    state: AccessState,
    error: String?,
    onSubmit: (String) -> Unit,
    onRetry: () -> Unit,
) {
    var password by remember { mutableStateOf("") }
    val busy = state == AccessState.CHECKING

    Box(
        modifier = Modifier.fillMaxSize().background(ObsidianBg).padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier.fillMaxWidth().widthIn(max = 380.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Text("Terminal access", color = Color.White, fontSize = 20.sp, fontWeight = FontWeight.Bold)
            Text(
                "Enter the server access password. Market data, portfolio calls, and order feeds stay disconnected until access is verified.",
                color = Color.Gray,
                fontSize = 12.sp,
            )
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                enabled = !busy,
                singleLine = true,
                label = { Text("Access password") },
                visualTransformation = PasswordVisualTransformation(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = androidx.compose.ui.text.input.ImeAction.Done),
                modifier = Modifier.fillMaxWidth(),
            )
            error?.let {
                Text(it, color = BearRed, fontSize = 12.sp)
            }
            Button(
                onClick = {
                    onSubmit(password)
                    password = ""
                },
                enabled = !busy && password.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text(if (busy) "Checking access..." else "Unlock terminal", fontWeight = FontWeight.Bold)
            }
            if (busy) CircularProgressIndicator(color = AccentBlue)
            if (state == AccessState.ERROR) {
                TextButton(onClick = onRetry) { Text("Retry access check") }
            }
        }
    }
}

@Composable
fun LoginDialog(
    currentMode: String,
    onDismiss: () -> Unit,
    onSubmit: (LoginRequest) -> Unit,
) {
    var mode by remember { mutableStateOf(currentMode) }
    var consumerKey by remember { mutableStateOf("") }
    var mobile by remember { mutableStateOf("") }
    var ucc by remember { mutableStateOf("DEMO01") }
    var totp by remember { mutableStateOf("") }
    var mpin by remember { mutableStateOf("") }

    fun clearSecrets() {
        consumerKey = ""
        totp = ""
        mpin = ""
    }

    val dismiss = {
        clearSecrets()
        onDismiss()
    }
    val submit: (LoginRequest) -> Unit = { request ->
        clearSecrets()
        onSubmit(request)
    }

    AlertDialog(
        onDismissRequest = dismiss,
        title = { Text("Kotak Neo API v3 Auth") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = mode == "paper", onClick = { mode = "paper" }, label = { Text("Paper") })
                    FilterChip(selected = mode == "kotak", onClick = { mode = "kotak" }, label = { Text("Kotak Live") })
                }
                if (mode == "kotak") {
                    OutlinedTextField(value = consumerKey, onValueChange = { consumerKey = it }, label = { Text("Consumer Key") }, visualTransformation = PasswordVisualTransformation(), singleLine = true)
                    OutlinedTextField(value = mobile, onValueChange = { mobile = it }, label = { Text("Mobile (+91...)") }, singleLine = true)
                    OutlinedTextField(value = ucc, onValueChange = { ucc = it }, label = { Text("UCC") }, singleLine = true)
                    OutlinedTextField(
                        value = totp,
                        onValueChange = { totp = it.filter { char -> char in '0'..'9' }.take(6) },
                        label = { Text("TOTP") },
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                        singleLine = true,
                    )
                    OutlinedTextField(
                        value = mpin,
                        onValueChange = { mpin = it.filter { char -> char in '0'..'9' }.take(6) },
                        label = { Text("MPIN") },
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
                        singleLine = true,
                    )
                } else {
                    Text("Connects to Paper Trading Simulator on FastAPI backend.")
                }
            }
        },
        confirmButton = {
            Button(onClick = { submit(LoginRequest(mode, consumerKey, mobile, ucc, totp, mpin)) }) {
                Text("Connect")
            }
        },
        dismissButton = {
            TextButton(onClick = dismiss) { Text("Cancel") }
        },
    )
}
