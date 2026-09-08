package com.ronin.ai.ui.chat

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.ui.theme.VyRxColors

// ---------------------------------------------------------------------------
// Message bubble (mockup style) with long-press actions + feedback row
// ---------------------------------------------------------------------------

@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
fun VyRxMessageBubble(message: ChatMessage, controller: ChatController, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    var menuOpen by remember { mutableStateOf(false) }
    val bubbleBg = if (message.mine) Color(0xFF12233A) else Color(0xFF171226)
    val strokeColor = if (message.mine) VyRxColors.BlueDeep.copy(alpha = 0.35f) else VyRxColors.Primary.copy(alpha = 0.35f)
    val authorColor = if (message.mine) VyRxColors.Blue else VyRxColors.PrimaryBright

    Box(modifier.fillMaxWidth().padding(vertical = 5.dp), contentAlignment = if (message.mine) Alignment.CenterEnd else Alignment.CenterStart) {
        Column(
            Modifier
                .widthIn(max = 420.dp)
                .clip(RoundedCornerShape(18.dp))
                .background(bubbleBg)
                .border(1.dp, strokeColor, RoundedCornerShape(18.dp))
                .combinedClickable(
                    onClick = {},
                    onLongClick = { menuOpen = true }
                )
                .padding(14.dp)
        ) {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text(
                    if (message.mine) "You" else "VYRX",
                    color = authorColor,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold
                )
                Text(message.time, color = VyRxColors.TextFaint, fontSize = 10.sp)
            }
            Spacer(Modifier.height(6.dp))
            Text(message.text, color = VyRxColors.TextPrimary, fontSize = 13.5.sp, lineHeight = 19.sp)
            if (!message.mine) {
                Spacer(Modifier.height(8.dp))
                Row(horizontalArrangement = Arrangement.End) {
                    BubbleAction(Icons.Filled.ContentCopy) {
                        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        clipboard.setPrimaryClip(ClipData.newPlainText("VYRX", message.text))
                    }
                    BubbleAction(Icons.Filled.ThumbUp) { controller.feedback(true, message) }
                    BubbleAction(Icons.Filled.ThumbDown) { controller.feedback(false, message) }
                }
            }
        }
        DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
            DropdownMenuItem(
                leadingIcon = { Icon(Icons.Filled.ContentCopy, null, Modifier.size(16.dp)) },
                text = { Text("Copy") },
                onClick = {
                    menuOpen = false
                    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                    clipboard.setPrimaryClip(ClipData.newPlainText("VYRX", message.text))
                }
            )
            if (!message.mine) {
                DropdownMenuItem(
                    leadingIcon = { Icon(Icons.Filled.AutoAwesome, null, Modifier.size(16.dp)) },
                    text = { Text("Regenerate") },
                    onClick = { menuOpen = false; controller.regenerate() }
                )
            }
            DropdownMenuItem(
                leadingIcon = { Icon(Icons.Filled.PushPin, null, Modifier.size(16.dp)) },
                text = { Text("Save Memory") },
                onClick = { menuOpen = false; controller.saveAsMemory(message) }
            )
            DropdownMenuItem(
                leadingIcon = { Icon(Icons.Filled.Mic, null, Modifier.size(16.dp)) },
                text = { Text("Explain") },
                onClick = { menuOpen = false; controller.explain(message) }
            )
        }
    }
}

@Composable
private fun BubbleAction(icon: androidx.compose.ui.graphics.vector.ImageVector, onClick: () -> Unit) {
    Box(
        Modifier
            .clip(CircleShape)
            .clickable(onClick = onClick)
            .padding(6.dp),
        contentAlignment = Alignment.Center
    ) {
        Icon(icon, null, tint = VyRxColors.TextDim, modifier = Modifier.size(16.dp))
    }
}

// ---------------------------------------------------------------------------
// Chat input bar: "Type ai message..." + mic + send
// ---------------------------------------------------------------------------

@Composable
fun ChatInputBar(
    controller: ChatController,
    onVoiceStart: () -> Unit,
    modifier: Modifier = Modifier
) {
    val context = LocalContext.current
    val settingsRepo = (context.applicationContext as com.ronin.ai.RoninApp).settingsRepository
    fun startVoice() {
        if (!settingsRepo.settings.value.voiceEnabled) {
            Toast.makeText(context, "Enable Voice Assistant in Settings first.", Toast.LENGTH_SHORT).show()
            return
        }
        val receiver = object : android.os.ResultReceiver(android.os.Handler(android.os.Looper.getMainLooper())) {
            override fun onReceiveResult(code: Int, data: android.os.Bundle?) {
                val result = data?.getString("text").orEmpty()
                if (code == 1 && result.isNotBlank()) controller.send(result, fromVoice = true)
                else Toast.makeText(context, result, Toast.LENGTH_SHORT).show()
            }
        }
        try {
            androidx.core.content.ContextCompat.startForegroundService(context,
                android.content.Intent(context, com.ronin.ai.services.ForegroundVoiceService::class.java)
                    .putExtra("receiver", receiver))
            onVoiceStart()
        } catch (e: Exception) {
            Toast.makeText(context, "Cannot start voice input: ${e.localizedMessage}", Toast.LENGTH_SHORT).show()
        }
    }
    val permissionLauncher = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestPermission()
    ) { granted ->
        if (granted) startVoice()
        else Toast.makeText(context, "Microphone permission denied.", Toast.LENGTH_SHORT).show()
    }
    androidx.compose.runtime.DisposableEffect(context) {
        onDispose {
            context.stopService(android.content.Intent(context, com.ronin.ai.services.ForegroundVoiceService::class.java))
        }
    }
    var text by remember { mutableStateOf("") }
    androidx.compose.runtime.LaunchedEffect(controller.prefill) {
        if (controller.prefill.isNotEmpty()) {
            text = controller.prefill
            controller.prefill = ""
        }
    }
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            Modifier
                .weight(1f)
                .clip(RoundedCornerShape(24.dp))
                .background(Color(0xFF0C1018))
                .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(24.dp))
                .padding(start = 18.dp, end = 12.dp),
            contentAlignment = Alignment.CenterEnd
        ) {
            BasicTextField(
                value = text,
                onValueChange = { text = it },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 4.dp, vertical = 16.dp),
                textStyle = androidx.compose.ui.text.TextStyle(color = VyRxColors.TextPrimary, fontSize = 14.sp),
                cursorBrush = SolidColor(VyRxColors.PrimaryBright),
                singleLine = false,
                maxLines = 4
            )
            if (text.isEmpty()) {
                Text(
                    "Type ai message...",
                    color = VyRxColors.TextFaint,
                    fontSize = 14.sp,
                    modifier = Modifier.align(Alignment.CenterStart).padding(start = 22.dp)
                )
            }
            if (controller.loading) {
                CircularProgressIndicator(
                    Modifier.align(Alignment.CenterEnd).size(16.dp),
                    color = VyRxColors.PrimaryBright,
                    strokeWidth = 2.dp
                )
            }
        }
        Spacer(Modifier.width(8.dp))
        Box(
            Modifier
                .size(46.dp)
                .clip(CircleShape)
                .background(Color(0xFF101522))
                .border(1.dp, VyRxColors.CardStroke, CircleShape)
                .clickable {
                    if (VoiceInput.isListening) {
                        context.stopService(android.content.Intent(context, com.ronin.ai.services.ForegroundVoiceService::class.java))
                    } else if (VoiceInput.hasPermission(context)) {
                        startVoice()
                    } else {
                        permissionLauncher.launch(android.Manifest.permission.RECORD_AUDIO)
                    }
                },
            contentAlignment = Alignment.Center
        ) {
            Icon(
                Icons.Filled.Mic,
                if (VoiceInput.isListening) "Stop voice input" else "Start voice input",
                tint = if (VoiceInput.isListening) VyRxColors.Green else VyRxColors.TextPrimary,
                modifier = Modifier.size(20.dp)
            )
        }
        Spacer(Modifier.width(8.dp))
        Box(
            Modifier
                .size(46.dp)
                .clip(CircleShape)
                .background(
                    if (text.isNotBlank() && !controller.loading)
                        androidx.compose.ui.graphics.Brush.linearGradient(listOf(VyRxColors.Primary, VyRxColors.PrimaryDim))
                    else SolidColor(Color(0xFF1A2030))
                )
                .clickable(enabled = text.isNotBlank() && !controller.loading) {
                    controller.send(text)
                    text = ""
                },
            contentAlignment = Alignment.Center
        ) {
            Icon(Icons.AutoMirrored.Filled.Send, null, tint = Color.White, modifier = Modifier.size(19.dp))
        }
    }
}

// ---------------------------------------------------------------------------
// Shared chat panel (message list + input), reused by Home and Chat screens
// ---------------------------------------------------------------------------

@Composable
fun ChatPanel(controller: ChatController, onVoiceStart: () -> Unit, modifier: Modifier = Modifier) {
    Column(modifier) {
        LazyColumn(
            Modifier.weight(1f).fillMaxWidth(),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(start = 12.dp, end = 12.dp, bottom = 4.dp)
        ) {
            items(controller.messages, key = { it.id }) { message ->
                VyRxMessageBubble(message, controller)
            }
        }
        controller.error?.let { error ->
            Text(
                error,
                color = VyRxColors.Red,
                fontSize = 11.sp,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                textAlign = TextAlign.Start
            )
        }
        AnimatedVisibility(visible = controller.brainStatus == BrainStatus.STARTING || controller.brainStatus == BrainStatus.CHECKING) {
            Text(
                when (controller.brainStatus) {
                    BrainStatus.STARTING -> "Starting local Brain (Termux)..."
                    BrainStatus.CHECKING -> "Checking local Brain..."
                    else -> ""
                },
                color = VyRxColors.TextDim,
                fontSize = 11.sp,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 2.dp)
            )
        }
        if (controller.brainStatus == BrainStatus.OFFLINE) {
            Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Text("Brain unavailable — Termux could not start the local Brain.", color = VyRxColors.Red, fontSize = 11.sp)
                Spacer(Modifier.width(10.dp))
                Text(
                    "Retry",
                    color = VyRxColors.PrimaryBright,
                    fontSize = 11.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .clickable { controller.retryBrain() }
                        .padding(4.dp)
                )
            }
        }
        ChatInputBar(controller, onVoiceStart)
    }
}
