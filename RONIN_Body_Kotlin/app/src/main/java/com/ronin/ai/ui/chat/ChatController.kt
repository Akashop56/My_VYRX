package com.ronin.ai.ui.chat

import android.content.Context
import com.ronin.ai.data.AppSettings
import com.ronin.ai.network.AskResponse
import com.ronin.ai.network.ApiClient
import com.ronin.ai.network.BrainConnectionManager
import com.ronin.ai.network.ProviderConfig
import com.ronin.ai.network.UpdateProposal
import com.ronin.ai.utils.CommandExecutor
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.util.UUID
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val text: String,
    val mine: Boolean,
    val time: String = LocalTime.now().format(DateTimeFormatter.ofPattern("h:mm a"))
)

enum class BrainStatus { CHECKING, STARTING, ONLINE, OFFLINE }

/**
 * Owns the conversation lifecycle (brain startup, sending, command execution,
 * self-coding proposals, memory save, regenerate, feedback) and is shared by
 * the Home screen and the full Chat screen.
 */
class ChatController(
    context: Context,
    private val providersProvider: () -> List<ProviderConfig> = { emptyList() },
    private val settingsProvider: () -> AppSettings = { AppSettings() }
) {
    private val appContext = context.applicationContext
    private val connection = BrainConnectionManager(appContext)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    val messages = mutableStateListOf<ChatMessage>()
    var loading by mutableStateOf(false); private set
    var error by mutableStateOf<String?>(null); private set
    var proposal by mutableStateOf<UpdateProposal?>(null); private set
    var brainStatus by mutableStateOf(BrainStatus.CHECKING); private set
    var listening by mutableStateOf(false); private set

    /** Text to prefill in the chat input (e.g. "Emergency: " from the orb long-press). */
    var prefill by mutableStateOf("")

    init {
        ensureBrainStarted()
    }

    fun dispose() {
        scope.cancel()
    }

    private fun ensureBrainStarted() {
        scope.launch {
            brainStatus = BrainStatus.CHECKING
            brainStatus = if (connection.isAvailable()) {
                BrainStatus.ONLINE
            } else {
                brainStatus = BrainStatus.STARTING
                if (connection.requestStartupAndCheck()) BrainStatus.ONLINE else BrainStatus.OFFLINE
            }
            if (messages.isEmpty()) {
                messages.add(ChatMessage("VYRX online. How can I help, Boss?", mine = false))
            }
        }
    }

    fun retryBrain() = ensureBrainStarted()

    private suspend fun executeAsk(prompt: String, fromVoice: Boolean): AskResponse {
        val s = settingsProvider()
        return ApiClient.ask(
            message = prompt,
            providers = providersProvider(),
            inputMode = if (fromVoice) "voice" else "text",
            toolsEnabled = s.toolsEnabled,
            personality = s.personality,
            responseMode = s.responseMode
        )
    }

    fun send(text: String, fromVoice: Boolean = false) {
        val prompt = text.trim()
        if (prompt.isEmpty() || loading) return
        if (brainStatus != BrainStatus.ONLINE) {
            error = "Brain is not available yet — retrying."
            ensureBrainStarted()
            return
        }
        messages.add(ChatMessage(prompt, mine = true))
        loading = true
        error = null
        scope.launch {
            try {
                val r = executeAsk(prompt, fromVoice)
                messages.add(ChatMessage(r.response, mine = false))
                r.command?.let { command ->
                    val executed = CommandExecutor.execute(appContext, command).getOrDefault(false)
                    if (!executed) {
                        messages.add(ChatMessage("Android command was prepared but could not run; enable Accessibility or required permissions.", mine = false))
                    }
                }
                proposal = r.update_proposal
                if (r.error != null && r.error!!.isNotBlank()) error = r.error
            } catch (e: Exception) {
                brainStatus = BrainStatus.OFFLINE
                error = e.message ?: "Connection to VYRX Brain failed."
            } finally {
                loading = false
            }
        }
    }

    /** Regenerate the last AI answer. */
    fun regenerate() {
        val lastUser = messages.lastOrNull { it.mine } ?: return
        if (loading) return
        scope.launch {
            val lastIndex = messages.indexOfLast { !it.mine }
            if (lastIndex >= 0) messages.removeAt(lastIndex)
            loading = true
            error = null
            try {
                val r = executeAsk(lastUser.text, fromVoice = false)
                messages.add(ChatMessage(r.response, mine = false))
                r.command?.let { command -> runCatching { CommandExecutor.execute(appContext, command) } }
            } catch (e: Exception) {
                error = e.message ?: "Regeneration failed."
            } finally {
                loading = false
            }
        }
    }

    fun saveAsMemory(message: ChatMessage) {
        scope.launch {
            runCatching {
                ApiClient.addMemory(
                    category = "personal",
                    title = message.text.take(60).ifBlank { "Saved note" },
                    content = message.text,
                    importance = 3,
                    source = "chat"
                )
            }.onSuccess {
                messages.add(ChatMessage("Memory saved.", mine = false))
            }.onFailure { error = it.message }
        }
    }

    fun explain(message: ChatMessage) {
        send("Explain in simple terms: ${message.text.take(300)}", fromVoice = false)
    }

    fun feedback(up: Boolean, message: ChatMessage) {
        scope.launch {
            runCatching { ApiClient.feedback(if (up) "up" else "down", message.text.take(120)) }
        }
    }

    fun approveProposal(approved: Boolean) {
        val p = proposal ?: return
        scope.launch {
            try {
                val r = ApiClient.approve(approved, p)
                messages.add(ChatMessage(r.message, mine = false))
            } catch (e: Exception) {
                error = e.message
            } finally {
                proposal = null
            }
        }
    }
}
