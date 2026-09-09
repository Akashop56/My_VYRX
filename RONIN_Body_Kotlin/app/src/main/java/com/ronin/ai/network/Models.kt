package com.ronin.ai.network

import org.json.JSONObject

data class AskRequest(val message: String, val session_id: String = "default")

/** Legacy offline command contract (kept for backward compatibility). */
data class AndroidCommand(
    val action: String,
    val text: String? = null,
    val package_name: String? = null,
    val x: Float? = null,
    val y: Float? = null,
    val node_id: String? = null,
    val direction: String? = null
)

data class UpdateProposal(val proposal_id: String, val file_path: String, val module_name: String, val new_code: String, val summary: String)

/**
 * One pending device-side tool call dispatched by the Python Brain.
 * The Body MUST execute it and POST the observation back to /agent/result
 * without requiring any user tap (see ChatController agent loop).
 */
data class AgentAction(
    val tool: String,
    val args: JSONObject = JSONObject(),
    val toolCallId: String? = null,
    val thought: String? = null
) {
    fun argString(key: String): String? = args.optString(key, "").takeIf { it.isNotBlank() }
    fun argDouble(key: String): Double? = args.opt(key)?.takeIf { it != JSONObject.NULL }?.let { (it as? Number)?.toDouble() }
    fun argInt(key: String, default: Int): Int = args.opt(key)?.takeIf { it != JSONObject.NULL }?.let { (it as? Number)?.toInt() } ?: default
}

/** Structured outcome of executing an AgentAction on the device. */
data class AgentResult(val success: Boolean, val text: String) {
    companion object {
        fun ok(text: String) = AgentResult(true, text)
        fun fail(text: String) = AgentResult(false, text)
    }
}

data class AskResponse(
    val response: String,
    val route: String,
    val command: AndroidCommand? = null,
    val update_proposal: UpdateProposal? = null,
    val error: String? = null,
    // --- agentic loop fields (additive) ---
    val action: AgentAction? = null,
    val needsToolResult: Boolean = false,
    val thought: String? = null,
    val steps: Int = 0,
    /** True when the answer rode the live SSE stream instead of this JSON body. */
    val streamed: Boolean = false
)
data class ApprovalRequest(val approved: Boolean, val proposal: UpdateProposal)
data class ApprovalResponse(val accepted: Boolean, val success: Boolean, val message: String)
enum class ProviderType(val wireName: String, val label: String) {
 OPENAI("openai", "OpenAI"), GEMINI("gemini", "Gemini"), GROQ("groq", "Groq"), OPENROUTER("openrouter", "OpenRouter"), CUSTOM("custom", "Custom API endpoint")
}
data class ProviderConfig(val id: String, val type: ProviderType, val name: String, val apiKey: String, val endpoint: String? = null, val model: String? = null, val enabled: Boolean = true)
