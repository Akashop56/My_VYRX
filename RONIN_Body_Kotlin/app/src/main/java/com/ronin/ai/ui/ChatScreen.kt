package com.ronin.ai.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.ronin.ai.ui.chat.ChatController
import com.ronin.ai.ui.chat.ChatPanel
import com.ronin.ai.ui.components.VyRxTopBar

/**
 * Full-screen conversation. Reuses the shared [ChatController] (brain startup,
 * sending, command execution, self-coding approval) and the shared chat panel.
 */
@Composable
fun ChatScreen(
    controller: ChatController,
    onOpenDrawer: () -> Unit,
    onVoiceStart: () -> Unit
) {
    Column(Modifier.fillMaxSize()) {
        VyRxTopBar(
            title = "CHAT",
            subtitle = "Conversation with VYRX",
            onMenu = onOpenDrawer,
            modifier = Modifier.fillMaxWidth()
        )
        ChatPanel(controller, onVoiceStart, Modifier.weight(1f))
    }
}
