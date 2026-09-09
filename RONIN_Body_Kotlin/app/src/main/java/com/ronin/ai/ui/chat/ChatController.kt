package com.ronin.ai.ui.chat

import android.content.Context
import com.ronin.ai.data.AppSettings
import com.ronin.ai.data.BrainRepository
import com.ronin.ai.network.ActionLogEntry
import com.ronin.ai.network.AgentAction
import com.ronin.ai.network.AgentEvent
import com.ronin.ai.network.AgentResult
import com.ronin.ai.network.ApiClient
import com.ronin.ai.network.AskResponse
import com.ronin.ai.network.BrainConnectionManager
import com.ronin.ai.network.BrainState
import com.ronin.ai.network.ProviderConfig
import com.ronin.ai.network.UpdateProposal
import com.ronin.ai.utils.CommandExecutor
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.time.LocalTime
import java.time.format.DateTimeFormatter
import java.util.UUID
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue

/**
 * One line of the agent's internal monologue, rendered by the "Thought
 * Process" terminal block. [level] drives the neon colour coding.
 */
data class ThoughtLine(
    val id: Int,
    val time: String,
    val level: String,
    val text: String,
    val detail: String? = null,
    val tool: String? = null,
    val ms: Int = 0
) {
    companion object {
        const val LEVEL_PLAN = "plan"
        const val LEVEL_THINK = "think"
        const val LEVEL_CALL = "call"
        const val LEVEL_OK = "ok"
        const val LEVEL_FAIL = "fail"
        const val LEVEL_FIX = "fix"
        const val LEVEL_NOTE = "note"
        const val LEVEL_ANSWER = "answer"
    }
}

/** Where the autonomous loop currently is — drives the orb and the terminal chip. */
enum class AgentPhase {
    IDLE, PLANNING, REASONING, ACTING, OBSERVING, CORRECTING, TYPING, DONE;

    val busy: Boolean get() = this != IDLE && this != DONE

    fun label(): String = when (this) {
        IDLE -> "Ready"
        PLANNING -> "Analyzing prompt"
        REASONING -> "Reasoning"
        ACTING -> "Executing tool"
        OBSERVING -> "Reading result"
        CORRECTING -> "Self-correcting"
        TYPING -> "Typing answer"
        DONE -> "Complete"
    }
}

data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val text: String,
    val mine: Boolean,
    val time: String = LocalTime.now().format(DateTimeFormatter.ofPattern("h:mm a")),
    /** True while the answer is still streaming in (typewriter + live terminal). */
    val streaming: Boolean = false,
    /** The turn's CoT / action log, kept on the bubble so it stays reviewable. */
    val thoughts: List<ThoughtLine> = emptyList(),
    val phase: AgentPhase = AgentPhase.IDLE,
    val steps: Int = 0,
    val elapsedMs: Int = 0,
    val failed: Boolean = false
)

enum class BrainStatus { CHECKING, STARTING, ONLINE, OFFLINE }

/**
 * Owns the conversation lifecycle (brain startup, sending, command execution,
 * self-coding proposals, memory save, regenerate, feedback) and is shared by
 * the Home screen and the full Chat screen.
 *
 * Autonomous-OS core: when the Brain dispatches an [AgentAction], this
 * controller executes it on-device and POSTs the observation back to
 * /agent/result automatically — the ReAct loop continues with zero taps.
 *
 * Real-time transport (Step 1): a turn is one SSE stream
 * ([ApiClient.askStream]) instead of a blocking request, so the loop's
 * internal monologue (thinking / tool_call / observation / self_correction)
 * reaches the UI *while* it happens and the final answer arrives as `token`
 * chunks that this controller reveals through a typewriter ticker.
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

    // ------------------------------------------------------------------
    // Live turn state (Consumed by the chat UI)
    // ------------------------------------------------------------------

    /** Phase of the loop the user is watching (null when idle). */
    var agentPhase by mutableStateOf(AgentPhase.IDLE); private set

    /** Short human label for the current step, e.g. "Executing open_app". */
    var phaseLabel by mutableStateOf("Ready"); private set

    /** Wall-clock duration of the running turn, ticked by the reveal ticker. */
    var turnElapsedMs by mutableStateOf(0); private set

    /** Number of CoT frames received for the running turn. */
    var turnStepCount by mutableStateOf(0); private set

    /** Collapsed state of the "Thought Process" terminal for the active turn. */
    var turnExpanded by mutableStateOf(true); private set

    /** Live log lines of the running turn (the terminal block reads this). */
    val turnLines = mutableStateListOf<ThoughtLine>()

    /**
     * Bumped on every reveal/line change. Compose screens use it as a key to
     * follow the typing without reading the whole buffer each frame.
     */
    var streamSeq by mutableStateOf(0); private set

    init {
        ensureBrainStarted()
    }

    fun dispose() {
        ticker?.cancel()
        ticker = null
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

    // ------------------------------------------------------------------
    // Sending
    // ------------------------------------------------------------------

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
                addLine(
                    ThoughtLine.LEVEL_FAIL,
                    execution.exceptionOrNull()?.localizedMessage
                        ?: "Android command could not run; check required permissions or Accessibility."
                )
            }
        }
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
        // Claim the input immediately: the send button and regenerate both gate on it.
        loading = true
        error = null
        scope.launch { runTurn(prompt, fromVoice) }
    }

    /**
     * One autonomous turn: open the Brain's SSE stream, mirror every frame into
     * the live terminal block, execute dispatched device actions, and reveal the
     * answer with a typing effect.
     */
    private suspend fun runTurn(prompt: String, fromVoice: Boolean) {
        loading = true
        error = null
        beginTurn()
        val s = settingsProvider()
        try {
            ApiClient.askStream(
                message = prompt,
                sessionId = sessionId,
                providers = providersProvider(),
                inputMode = if (fromVoice) "voice" else "text",
                toolsEnabled = s.toolsEnabled,
                personality = s.personality,
                responseMode = s.responseMode
            ).collect { event -> handleEvent(event) }
        } catch (e: Exception) {
            // No usable stream (Brain restarted mid-turn, proxy killed the socket,
            // or a Brain too old for SSE): fall back to the monolithic call.
            val partial = answerTarget.isNotEmpty()
            if (!partial) {
                runCatching { executeAsk(prompt, fromVoice) }
                    .onSuccess { ask -> finishWithAsk(ask, typedIn = false) }
                    .onFailure { fail ->
                        brainStatus = BrainStatus.OFFLINE
                        error = fail.message ?: e.message ?: "Connection to VYRX Brain failed."
                        addLine(ThoughtLine.LEVEL_FAIL, error.orEmpty())
                    }
            } else {
                error = e.message ?: "Stream interrupted."
                addLine(ThoughtLine.LEVEL_FAIL, "Stream interrupted — showing what arrived.")
            }
        } finally {
            endTurn()
        }
    }

    private suspend fun handleEvent(event: AgentEvent) {
        when (event) {
            is AgentEvent.Start -> {
                turnStepCount = 0
                phaseLabel = "Connected · ${event.requestId}"
            }

            is AgentEvent.Thinking -> onThinking(event)
            is AgentEvent.Thought -> onThought(event)
            is AgentEvent.ToolCall -> onToolCall(event)
            is AgentEvent.Observation -> onObservation(event)
            is AgentEvent.SelfCorrection -> onSelfCorrection(event)

            is AgentEvent.Token -> {
                agentPhase = AgentPhase.TYPING
                phaseLabel = "Typing answer…"
                if (!typingStarted) {
                    typingStarted = true
                    // Get out of the way so Boss can read; the header stays tappable.
                    if (!userPinnedTerminal) turnExpanded = false
                }
                answerTarget.append(event.text)
            }

            is AgentEvent.AnswerEnd -> {
                agentPhase = AgentPhase.DONE
                phaseLabel = "Answer complete"
            }

            is AgentEvent.LogEntry -> onBrainLog(event.entry)
            is AgentEvent.BrainStateUpdate -> onBrainState(event.state)

            is AgentEvent.Done -> {
                // The `done` frame repeats the full answer: use it only if no
                // token ever arrived, so the typewriter never restarts or jumps.
                if (answerTarget.isEmpty() && event.ask.response.isNotBlank()) {
                    answerTarget.append(event.ask.response)
                }
                finishWithAsk(event.ask, typedIn = true, elapsedMs = event.elapsedMs)
            }

            is AgentEvent.Failed -> {
                agentPhase = AgentPhase.CORRECTING
                error = event.message.ifBlank { "Brain stream error: ${event.code}" }
                addLine(ThoughtLine.LEVEL_FAIL, "${event.code}: ${event.message}")
            }

            is AgentEvent.Legacy -> {
                // Pre-SSE Brain: replay the loop synchronously, then type the answer.
                agentPhase = AgentPhase.REASONING
                addLine(ThoughtLine.LEVEL_NOTE, "Brain answered without streaming — running the loop classically.")
                val settled = runCatching { runAgentLoop(event.ask) }.getOrElse { event.ask }
                answerTarget.setLength(0).append(settled.response)
                finishWithAsk(settled, typedIn = true)
            }
        }
    }

    private fun onThinking(event: AgentEvent.Thinking) {
        turnStepCount = maxOf(turnStepCount, event.step)
        agentPhase = when (event.phase) {
            "answer" -> AgentPhase.TYPING
            "reason", "verify", "call" -> AgentPhase.REASONING
            "plan", "route", "recall", "prompt" -> AgentPhase.PLANNING
            else -> AgentPhase.REASONING
        }
        phaseLabel = event.text
        addLine(
            if (event.phase == "answer") ThoughtLine.LEVEL_ANSWER else ThoughtLine.LEVEL_PLAN,
            event.text
        )
    }

    private fun onThought(event: AgentEvent.Thought) {
        agentPhase = AgentPhase.REASONING
        phaseLabel = "Reasoning…"
        if (event.isFinal) {
            val authoritative = event.text
            if (authoritative != null) {
                // Authoritative text wins over whatever partial deltas arrived.
                thoughtDelta.setLength(0)
                val id = liveThoughtId
                if (id == null) liveThoughtId = addLine(ThoughtLine.LEVEL_THINK, oneLine(authoritative, 600))
                else updateLine(id) { it.copy(text = oneLine(authoritative, 600)) }
            }
            return
        }
        if (event.delta.isNotEmpty()) thoughtDelta.append(event.delta)
    }

    private suspend fun onToolCall(event: AgentEvent.ToolCall) {
        agentPhase = AgentPhase.ACTING
        phaseLabel = event.label
        turnStepCount = maxOf(turnStepCount, event.step + 1)
        addLine(
            ThoughtLine.LEVEL_CALL,
            event.label,
            detail = previewArgs(event.args),
            tool = event.tool
        )
        if (!event.device || event.action == null) return
        // Device tool: execute on the Body and hand the observation back. The
        // Brain is parked on it inside the same stream, so no new turn starts.
        val action = event.action
        val outcome = runCatching { CommandExecutor.executeAction(appContext, action) }
            .getOrElse { e -> AgentResult.fail("Execution failed: ${e.localizedMessage ?: "unknown error"}") }
        submitObservation(action, outcome)
    }

    private suspend fun submitObservation(action: AgentAction, outcome: AgentResult) {
        agentPhase = AgentPhase.OBSERVING
        runCatching {
            ApiClient.submitToolResult(
                sessionId = sessionId,
                tool = action.tool,
                result = outcome.text,
                success = outcome.success,
                toolCallId = action.toolCallId
            )
        }.onFailure { e ->
            error = e.message ?: "Could not report the device result to the Brain."
            addLine(ThoughtLine.LEVEL_FAIL, "Observation callback failed: ${error.orEmpty()}")
        }
    }

    private fun onObservation(event: AgentEvent.Observation) {
        agentPhase = if (event.ok) AgentPhase.REASONING else AgentPhase.CORRECTING
        phaseLabel = if (event.ok) "${event.tool} → ${event.ms} ms" else "${event.tool} failed"
        addLine(
            if (event.ok) ThoughtLine.LEVEL_OK else ThoughtLine.LEVEL_FAIL,
            "${if (event.ok) "result ok" else "result failed"} · ${oneLine(event.result, 200)}",
            tool = event.tool,
            ms = event.ms
        )
    }

    private fun onSelfCorrection(event: AgentEvent.SelfCorrection) {
        agentPhase = AgentPhase.CORRECTING
        phaseLabel = "Self-correcting: ${event.tool ?: "provider"}"
        addLine(
            ThoughtLine.LEVEL_FIX,
            event.reason.ifBlank { "recovering from a failed step" } +
                (if (event.strategy.isBlank()) "" else " → ${event.strategy}"),
            tool = event.tool
        )
    }

    private fun onBrainLog(entry: ActionLogEntry) {
        // The Brain's own action-log line for this turn: mirror it into the
        // dashboard stream (deduped) without duplicating our structured frames.
        BrainRepository.pushLog(entry)
    }

    private fun onBrainState(state: BrainState) {
        BrainRepository.pushState(state)
    }

    // ------------------------------------------------------------------
    // Pre-stream compatibility: the classic request/response agent loop
    // ------------------------------------------------------------------

    /**
     * Autonomous loop for a Brain that answers monolithically: execute dispatched
     * device actions and feed observations back until the Brain speaks.
     */
    private suspend fun runAgentLoop(first: AskResponse): AskResponse {
        var r = first
        var guard = 0
        while (r.needsToolResult && r.action != null && guard < MAX_AGENT_ROUNDTRIPS) {
            guard++
            val action = r.action!!
            addLine(ThoughtLine.LEVEL_CALL, "Executing ${action.tool} on the Body", tool = action.tool)
            val outcome = runCatching { CommandExecutor.executeAction(appContext, action) }
                .getOrElse { e -> AgentResult.fail("Execution failed: ${e.localizedMessage ?: "unknown error"}") }
            addLine(
                if (outcome.success) ThoughtLine.LEVEL_OK else ThoughtLine.LEVEL_FAIL,
                oneLine(outcome.text, 200),
                tool = action.tool
            )
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

    private fun finishWithAsk(r: AskResponse, typedIn: Boolean, elapsedMs: Int = 0) {
        runLegacyCommand(r)
        if (r.update_proposal != null) proposal = r.update_proposal
        if (!r.error.isNullOrBlank()) error = r.error
        if (r.response.isNotBlank()) {
            if (!typedIn) answerTarget.setLength(0).append(r.response)
            lastSpeech = r.response
        }
        if (elapsedMs > 0) turnElapsedMs = elapsedMs
    }

    // ------------------------------------------------------------------
    // Typewriter ticker — decouples network arrival from reveal speed
    // ------------------------------------------------------------------

    private val answerTarget = StringBuilder()
    private val thoughtDelta = StringBuilder()
    private var revealed = 0
    private var typingStarted = false
    private var userPinnedTerminal = false
    private var activeId: String? = null
    private var lineSeq = 0
    private var liveThoughtId: Int? = null
    private var ticker: Job? = null
    private var turnStartedAt = 0L

    private fun beginTurn() {
        turnLines.clear()
        answerTarget.setLength(0)
        thoughtDelta.setLength(0)
        revealed = 0
        typingStarted = false
        userPinnedTerminal = false
        turnStepCount = 0
        turnElapsedMs = 0
        turnExpanded = true
        liveThoughtId = null
        agentPhase = AgentPhase.PLANNING
        phaseLabel = "Starting…"
        turnStartedAt = System.currentTimeMillis()
        val message = ChatMessage(text = "", mine = false, streaming = true, phase = AgentPhase.PLANNING)
        activeId = message.id
        messages.add(message)
        ticker?.cancel()
        ticker = scope.launch {
            while (isActive) {
                delay(TICK_MS)
                revealTick()
            }
        }
    }

    /** Reveal a slice of the pending answer and flush buffered chain-of-thought text. */
    private fun revealTick() {
        var changed = false

        if (thoughtDelta.isNotEmpty()) {
            val text = thoughtDelta.toString()
            thoughtDelta.setLength(0)
            val id = liveThoughtId
            if (id != null) updateLine(id) { line -> line.copy(text = oneLine(line.text + text, 600)) }
            else liveThoughtId = addLine(ThoughtLine.LEVEL_THINK, oneLine(text, 600))
            changed = true
        }

        val remaining = answerTarget.length - revealed
        if (remaining > 0) {
            // Reveal faster when far behind, slower near the end: reads like typing
            // even when the network delivered the text in bursts.
            val step = maxOf(2, minOf(14, (remaining + 5) / 6))
            revealed = (revealed + step).coerceAtMost(answerTarget.length)
            updateActiveMessage(answerTarget.substring(0, revealed))
            changed = true
        }

        val elapsed = (System.currentTimeMillis() - turnStartedAt).toInt()
        if (elapsed / 100 != turnElapsedMs / 100) {
            turnElapsedMs = elapsed
            changed = true
        }
        if (changed) streamSeq++
    }

    private suspend fun endTurn() {
        // Let the effect finish (bounded) so the bubble never jumps to the full text.
        val deadline = System.currentTimeMillis() + TYPING_GRACE_MS
        while (revealed < answerTarget.length && System.currentTimeMillis() < deadline) {
            delay(TICK_MS)
        }
        ticker?.cancel()
        ticker = null
        if (revealed < answerTarget.length) revealed = answerTarget.length
        commitActiveMessage()
        agentPhase = AgentPhase.IDLE
        phaseLabel = "Ready"
        loading = false
    }

    private fun updateActiveMessage(text: String) {
        val id = activeId ?: return
        val index = messages.indexOfFirst { it.id == id }
        if (index < 0) return
        val current = messages[index]
        messages[index] = current.copy(text = text, phase = agentPhase)
    }

    private fun commitActiveMessage() {
        val id = activeId ?: return
        val index = messages.indexOfFirst { it.id == id }
        activeId = null
        if (index < 0) return
        val current = messages[index]
        val text = answerTarget.toString().ifBlank { current.text }
        val failed = !error.isNullOrBlank() && text.isBlank()
        messages[index] = current.copy(
            text = text,
            streaming = false,
            thoughts = turnLines.toList(),
            phase = AgentPhase.DONE,
            steps = turnStepCount,
            elapsedMs = turnElapsedMs,
            failed = failed
        )
        if (text.isBlank() && !failed) {
            // Nothing to show (e.g. a stream that only dispatched actions).
            messages.removeAt(index)
        }
    }

    // ------------------------------------------------------------------
    // Terminal log lines
    // ------------------------------------------------------------------

    private fun addLine(level: String, text: String, detail: String? = null, tool: String? = null, ms: Int = 0): Int {
        val line = ThoughtLine(
            id = ++lineSeq,
            time = LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm:ss")),
            level = level,
            text = oneLine(text),
            detail = detail?.takeIf { it.isNotBlank() },
            tool = tool,
            ms = ms
        )
        turnLines.add(line)
        streamSeq++
        // Any structured event ends the current free-form reasoning line, so the
        // next delta opens a fresh one instead of being glued to a tool call.
        if (level != ThoughtLine.LEVEL_THINK) liveThoughtId = null
        while (turnLines.size > MAX_TERMINAL_LINES) turnLines.removeAt(0)
        return line.id
    }

    private fun updateLine(id: Int, transform: (ThoughtLine) -> ThoughtLine) {
        val index = turnLines.indexOfFirst { it.id == id }
        if (index < 0) return
        turnLines[index] = transform(turnLines[index])
    }

    /** Tap on the terminal header: keep it open/closed how Boss wants. */
    fun toggleTerminal() {
        userPinnedTerminal = true
        turnExpanded = !turnExpanded
    }

    // ------------------------------------------------------------------
    // Secondary actions
    // ------------------------------------------------------------------

    /** Regenerate the last AI answer. */
    fun regenerate() {
        val lastUser = messages.lastOrNull { it.mine } ?: return
        if (loading) return
        scope.launch {
            val lastIndex = messages.indexOfLast { !it.mine }
            if (lastIndex >= 0) messages.removeAt(lastIndex)
            runTurn(lastUser.text, fromVoice = false)
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

        /** Reveal cadence: ~31 fps, smooth without recomposing every frame. */
        const val TICK_MS = 32L

        /** How long we let the typewriter catch up after the stream ends. */
        const val TYPING_GRACE_MS = 4_000L

        /** Terminal block memory for a single turn. */
        const val MAX_TERMINAL_LINES = 140

        fun oneLine(text: String, limit: Int = 420): String {
            val flat = " ".join(text.split("\n", "\r").flatMap { it.split(" ") }.filter { it.isNotBlank() })
            return if (flat.length <= limit) flat else flat.take(limit - 1) + "…"
        }

        fun previewArgs(args: org.json.JSONObject): String {
            if (args.length() == 0) return ""
            val builder = StringBuilder()
            val keys = args.keys()
            while (keys.hasNext() && builder.length < 140) {
                val key = keys.next()
                if (builder.isNotEmpty()) builder.append(", ")
                builder.append(key).append('=').append(args.opt(key)?.toString().orEmpty().take(40))
            }
            return oneLine(builder.toString(), 160)
        }
    }
}
