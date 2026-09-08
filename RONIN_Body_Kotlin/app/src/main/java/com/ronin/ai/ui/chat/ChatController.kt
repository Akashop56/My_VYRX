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
 *
 * Autonomous-OS core: when the Brain dispatches an [AgentAction], this
 * controller executes it on-device and POSTs the observation back to
 * /agent/result automatically — the ReAct loop continues with zero taps
 * until the Brain returns final spoken text.
 */
class ChatController(
    context: Context,
    private val providersProvider: () -> List<ProviderConfig> = { emptyList() },
    private val settingsProvider: () -> AppSettings = { AppSettings() }
) {
    private val appContext = context.applicationContext
    private val connection = BrainConnectionManager(appContext)
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    /** Stable agent session so Brain continuations resolve to this chat. */
    private val sessionId: String = UUID.randomUUID().toString()

    val messages = mutableStateListOf<ChatMessage>()
    var loading by mutableStateOf(false); private set
    var error by mutableStateOf<String?>(null); private set
    var proposal by mutableStateOf<UpdateProposal?>(null); private set
    var brainStatus by mutableStateOf(BrainStatus.CHECKING); private set
    var listening by mutableStateOf(false); private set

    /** TTS-ready speech of the last completed turn (plain text, no tool tags). */
    var lastSpeech by mutableStateOf<String?>(null); private set

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
                messages.add(ChatMessage(text = "VYRX online. How can I help, Boss?", mine = false))
            }
        }
    }

    fun retryBrain() = ensureBrainStarted()

    private suspend fun executeAsk(prompt: String, fromVoice: Boolean): AskResponse {
        val s = settingsProvider()
        return ApiClient.ask(
            message = prompt,
            sessionId = sessionId,
            providers = providersProvider(),
            inputMode = if (fromVoice) "voice" else "text",
            toolsEnabled = s.toolsEnabled,
            personality = s.personality,
            responseMode = s.responseMode
        )
    }

    /** Legacy offline command path (synchronous, kept for backward compatibility). */
    private fun runLegacyCommand(r: AskResponse) {
        r.command?.let { command ->
            val execution = CommandExecutor.execute(appContext, command)
            val executed = execution.getOrDefault(false)
            if (!executed) {
                messages.add(ChatMessage(text = execution.exceptionOrNull()?.localizedMessage ?: "Android command could not run; check required permissions or Accessibility.", mine = false))
            }
        }
    }

    /**
     * Autonomous loop: execute dispatched device actions and feed observations
     * back until the Brain speaks its final answer. Returns the final response.
     */
    private suspend fun runAgentLoop(first: AskResponse): AskResponse {
        var r = first
        var guard = 0
        while (r.needsToolResult && r.action != null && guard < MAX_AGENT_ROUNDTRIPS) {
            guard++
            val action = r.action!!
            val interim = ChatMessage(text = "⚙️ Acting: ${action.tool}…", mine = false)
            messages.add(interim)
            val outcome = runCatching { CommandExecutor.executeAction(appContext, action) }
                .getOrElse { e -> com.ronin.ai.network.AgentResult.fail("Execution failed: ${e.localizedMessage ?: "unknown error"}") }
            messages.remove(interim)
            r = ApiClient.submitToolResult(
                sessionId = sessionId,
                tool = action.tool,
                result = outcome.text,
                success = outcome.success,
                toolCallId = action.toolCallId
            )
            // A continuation may also carry a legacy command (offline fallback).
            runLegacyCommand(r)
        }
        return r
    }

    fun send(text: String, fromVoice: Boolean = false) {
        val prompt = text.trim()
        if (prompt.isEmpty() || loading) return
        if (brainStatus != BrainStatus.ONLINE) {
            error = "Brain is not available yet — retrying."
            ensureBrainStarted()
            return
        }
        messages.add(ChatMessage(text = prompt, mine = true))
        loading = true
        error = null
        scope.launch {
            try {
                val first = executeAsk(prompt, fromVoice)
                runLegacyCommand(first)
                val r = runAgentLoop(first)
                if (r.response.isNotBlank()) {
                    messages.add(ChatMessage(text = r.response, mine = false))
                    lastSpeech = r.response
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
                val first = executeAsk(lastUser.text, fromVoice = false)
                first.command?.let { command -> runCatching { CommandExecutor.execute(appContext, command) } }
                val r = runAgentLoop(first)
                if (r.response.isNotBlank()) {
                    messages.add(ChatMessage(text = r.response, mine = false))
                    lastSpeech = r.response
                }
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
                messages.add(ChatMessage(text = "Memory saved.", mine = false))
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
                messages.add(ChatMessage(text = r.message, mine = false))
            } catch (e: Exception) {
                error = e.message
            } finally {
                proposal = null
            }
        }
    }

    private companion object {
        /** Max Body->Brain round-trips per user message (mirrors Brain's step budget). */
        const val MAX_AGENT_ROUNDTRIPS = 8
    }
}
