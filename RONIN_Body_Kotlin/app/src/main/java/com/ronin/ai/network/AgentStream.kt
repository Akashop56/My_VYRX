package com.ronin.ai.network

import org.json.JSONObject

/**
 * One frame of the Brain's live agent stream.
 *
 * The Brain runs the whole ReAct loop inside a single `text/event-stream`
 * response and pushes these frames as they happen (see
 * `docs/REALTIME_STREAMING.md`). Each frame is `event: <type>` +
 * `data: <json>` where the JSON always carries `type` + a monotonic `seq`.
 *
 * [Legacy] is the compatibility path: a Brain that predates SSE answers
 * `/ask_ronin` with one monolithic JSON body, which we surface as a single
 * event so the UI keeps working (just without the live terminal).
 */
sealed class AgentEvent {

    /** Stream opened and the request was accepted by the planner. */
    data class Start(val requestId: String, val message: String, val state: BrainState?) : AgentEvent()

    /** A reasoning phase began: plan | route | recall | prompt | call | reason | verify | answer. */
    data class Thinking(val step: Int, val phase: String, val text: String) : AgentEvent()

    /** Live chain-of-thought text. [text] (when final) is authoritative: replace, don't append. */
    data class Thought(
        val step: Int,
        val streamId: String,
        val delta: String,
        val isFinal: Boolean,
        val text: String?
    ) : AgentEvent()

    /** The agent decided to run a tool. [action] is non-null for device tools the Body must execute. */
    data class ToolCall(
        val step: Int,
        val tool: String,
        val label: String,
        val args: JSONObject,
        val device: Boolean,
        val thought: String?,
        val action: AgentAction?
    ) : AgentEvent()

    /** The tool came back. [result] is a display-sized digest, not the raw payload. */
    data class Observation(val step: Int, val tool: String, val ok: Boolean, val ms: Int, val result: String) : AgentEvent()

    /** A failure the agent is healing from (Brain's `self_correction` / `reflexion` frame). */
    data class SelfCorrection(val step: Int, val tool: String?, val reason: String, val strategy: String, val attempt: Int) : AgentEvent()

    /** A chunk of the final answer — append to the active bubble for the typing effect. */
    data class Token(val index: Int, val text: String) : AgentEvent()

    /** Server finished composing; the Body may now drain its own reveal buffer. */
    data class AnswerEnd(val chars: Int) : AgentEvent()

    /** The Brain's own action-log line for this turn. */
    data class LogEntry(val entry: ActionLogEntry) : AgentEvent()

    /** Orb state pushed alongside the turn (faster than the global /api/events SSE). */
    data class BrainStateUpdate(val state: BrainState) : AgentEvent()

    /** Terminal frame: the complete answer, route and step count. */
    data class Done(val ask: AskResponse, val elapsedMs: Int) : AgentEvent()

    /** In-band failure. The stream may still end with [Done] afterwards. */
    data class Failed(val code: String, val message: String, val fatal: Boolean) : AgentEvent()

    /** Pre-SSE Brain: one JSON answer instead of a stream. */
    data class Legacy(val ask: AskResponse) : AgentEvent()
}

/**
 * SSE frame decoder. Kept separate from the transport so it is unit-testable and
 * usable by any client (OkHttp Flow, a raw socket, a test harness).
 */
object AgentEventCodec {

    /** Returns null for frames we do not understand yet (forward compatible). */
    fun decode(eventType: String?, raw: String): AgentEvent? {
        val data = runCatching { JSONObject(raw) }.getOrNull() ?: return null
        val type = data.optString("type", eventType ?: "").ifBlank { return null }
        return when (type) {
            "start" -> AgentEvent.Start(
                requestId = data.optString("request_id", ""),
                message = data.optString("message", ""),
                state = data.optJSONObject("state")?.let { runCatching { it.parseBrainState() }.getOrNull() }
            )

            "thinking" -> AgentEvent.Thinking(
                step = data.optInt("step", 0),
                phase = data.optString("phase", "plan"),
                text = data.optString("text", "")
            )

            "thought" -> AgentEvent.Thought(
                step = data.optInt("step", 0),
                streamId = data.optString("stream", "t0"),
                delta = data.optString("delta", ""),
                isFinal = data.optBoolean("final", false),
                text = data.optStringOrNull("text")
            )

            "tool_call" -> AgentEvent.ToolCall(
                step = data.optInt("step", 0),
                tool = data.optString("tool", "?"),
                label = data.optString("label", data.optString("tool", "?")),
                args = data.optJSONObject("args") ?: JSONObject(),
                device = data.optBoolean("device", false),
                thought = data.optStringOrNull("thought"),
                action = data.optJSONObject("action")?.let { action ->
                    AgentAction(
                        action.getString("tool"),
                        action.optJSONObject("args") ?: JSONObject(),
                        action.optString("tool_call_id").takeIf { it.isNotBlank() },
                        action.optString("thought").takeIf { it.isNotBlank() }
                    )
                }
            )

            "observation" -> AgentEvent.Observation(
                step = data.optInt("step", 0),
                tool = data.optString("tool", "?"),
                ok = data.optBoolean("ok", true),
                ms = data.optInt("ms", 0),
                result = data.optString("result", "")
            )

            // `reflexion` is the semantic alias the Brain may emit instead.
            "self_correction", "reflexion" -> AgentEvent.SelfCorrection(
                step = data.optInt("step", 0),
                tool = data.optStringOrNull("tool"),
                reason = data.optString("reason", data.optString("reflexion", "")),
                strategy = data.optString("strategy", ""),
                attempt = data.optInt("attempt", 0)
            )

            "token" -> AgentEvent.Token(index = data.optInt("i", 0), text = data.optString("text", ""))

            "answer_end" -> AgentEvent.AnswerEnd(chars = data.optInt("chars", 0))

            "log" -> AgentEvent.LogEntry(entry = data.parseActionLog())

            "state" -> AgentEvent.BrainStateUpdate(state = data.parseBrainState())

            "done" -> AgentEvent.Done(
                ask = ApiClient.parseAsk(data),
                elapsedMs = data.optInt("elapsed_ms", 0)
            )

            "error" -> AgentEvent.Failed(
                code = data.optString("code", "error"),
                message = data.optString("message", ""),
                fatal = data.optBoolean("fatal", false)
            )

            else -> null
        }
    }
}
