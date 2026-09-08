package com.ronin.ai.network

import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

object ApiClient {
    private val client = OkHttpClient.Builder().connectTimeout(8, TimeUnit.SECONDS).readTimeout(45, TimeUnit.SECONDS).build()
    private val sseClient = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(0, TimeUnit.SECONDS).build()
    private const val BASE = "http://127.0.0.1:8000"
    private val json = "application/json; charset=utf-8".toMediaType()

    // ------------------------------------------------------------------
    // Original RONIN endpoints
    // ------------------------------------------------------------------

    suspend fun ask(
        message: String,
        sessionId: String = "default",
        providers: List<ProviderConfig> = emptyList(),
        inputMode: String = "text",
        toolsEnabled: Map<String, Boolean> = emptyMap(),
        personality: String? = null,
        responseMode: String? = null
    ): AskResponse = withContext(Dispatchers.IO) {
        val configuredProviders = JSONArray()
        providers.filter { it.enabled }.forEach { configuredProviders.put(
            JSONObject().put("provider", it.type.wireName)
                .put("api_key", it.apiKey)
                .putOpt("endpoint", it.endpoint)
                .putOpt("model", it.model)
        ) }
        val toolsJson = JSONObject()
        toolsEnabled.forEach { (k, v) -> toolsJson.put(k, v) }
        val body = JSONObject()
            .put("message", message)
            .put("session_id", sessionId)
            .put("providers", configuredProviders)
            .put("input_mode", inputMode)
            .put("tools_enabled", toolsJson)
            .putOpt("personality", personality)
            .putOpt("response_mode", responseMode)
            .toString().toRequestBody(json)
        execute("$BASE/ask_ronin", body).let(::parseAsk)
    }

    suspend fun approve(approved: Boolean, proposal: UpdateProposal): ApprovalResponse = withContext(Dispatchers.IO) {
        val p = JSONObject().put("proposal_id", proposal.proposal_id).put("file_path", proposal.file_path)
            .put("module_name", proposal.module_name).put("new_code", proposal.new_code).put("summary", proposal.summary)
        val raw = execute("$BASE/approve_update", JSONObject().put("approved", approved).put("proposal", p).toString().toRequestBody(json))
        val o = JSONObject(raw)
        ApprovalResponse(o.getBoolean("accepted"), o.getBoolean("success"), o.getString("message"))
    }

    /**
     * Hidden background callback: Body -> Brain execution observation.
     * Called automatically after executing an AgentAction; the Brain replies
     * with either the next pending action or the final spoken answer.
     */
    suspend fun submitToolResult(
        sessionId: String,
        tool: String,
        result: String,
        success: Boolean,
        toolCallId: String? = null
    ): AskResponse = withContext(Dispatchers.IO) {
        val body = JSONObject()
            .put("session_id", sessionId)
            .put("tool", tool)
            .put("result", result.take(20000))
            .put("success", success)
            .putOpt("tool_call_id", toolCallId)
            .toString().toRequestBody(json)
        execute("$BASE/agent/result", body).let(::parseAsk)
    }

    suspend fun health(): Boolean = withContext(Dispatchers.IO) {
        try {
            client.newCall(Request.Builder().url("$BASE/health").get().build()).execute().use { response ->
                response.isSuccessful && JSONObject(response.body?.string().orEmpty()).optString("status") == "ok"
            }
        } catch (_: IOException) { false }
    }

    // ------------------------------------------------------------------
    // VYRX API surface
    // ------------------------------------------------------------------

    suspend fun state(): BrainState = withContext(Dispatchers.IO) {
        executeGet("$BASE/api/state").let { JSONObject(it).parseBrainState() }
    }

    suspend fun healthDetail(): HealthInfo = withContext(Dispatchers.IO) {
        executeGet("$BASE/api/health").let { JSONObject(it).parseHealthInfo() }
    }

    suspend fun actionLogs(limit: Int = 50): List<ActionLogEntry> = withContext(Dispatchers.IO) {
        val arr = JSONArray(executeGet("$BASE/api/action_logs?limit=$limit").let { JSONObject(it).optString("logs", "[]") })
        List(arr.length()) { arr.getJSONObject(it).parseActionLog() }
    }

    suspend fun summary(): SummaryInfo = withContext(Dispatchers.IO) {
        executeGet("$BASE/api/summary").let { JSONObject(it).parseSummary() }
    }

    suspend fun memories(category: String? = null, search: String? = null, sort: String = "newest"): Pair<List<MemoryItem>, MemoryStats> =
        withContext(Dispatchers.IO) {
            val query = buildString {
                append("$BASE/api/memory?sort=$sort")
                if (category != null) append("&category=$category")
                if (!search.isNullOrBlank()) append("&search=${Uri.encode(search)}")
            }
            val o = JSONObject(executeGet(query))
            val items = mutableListOf<MemoryItem>()
            o.optJSONArray("memories")?.let { arr -> for (i in 0 until arr.length()) items.add(arr.getJSONObject(i).parseMemoryItem()) }
            val statsJson = o.optJSONObject("memory_stats") ?: o.optJSONObject("stats") ?: JSONObject()
            val stats = MemoryStats(
                statsJson.optInt("total", 0),
                statsJson.optLong("size_bytes", 0L),
                statsJson.optInt("health_score", 0),
                statsJson.optInt("retention_days", 0)
            )
            items to stats
        }

    suspend fun addMemory(category: String, title: String, content: String, importance: Int = 3, source: String = "user"): MemoryItem =
        withContext(Dispatchers.IO) {
            val body = JSONObject().put("category", category).put("title", title).put("content", content)
                .put("importance", importance).put("source", source).toString().toRequestBody(json)
            execute("$BASE/api/memory", body).let { JSONObject(it).parseMemoryItem() }
        }

    suspend fun updateMemory(id: Long, category: String? = null, title: String? = null, content: String? = null,
                             importance: Int? = null, pinned: Boolean? = null): MemoryItem = withContext(Dispatchers.IO) {
        val body = JSONObject().putOpt("category", category).putOpt("title", title).putOpt("content", content)
            .putOpt("importance", importance).putOpt("pinned", pinned).toString().toRequestBody(json)
        execute("$BASE/api/memory/$id", body).let { JSONObject(it).parseMemoryItem() }
    }

    suspend fun deleteMemory(id: Long) {
        withContext(Dispatchers.IO) {
            client.newCall(Request.Builder().url("$BASE/api/memory/$id").delete().build()).execute().use { }
        }
    }

    suspend fun providers(): Pair<ProviderEngine, List<ProviderInfo>> = withContext(Dispatchers.IO) {
        val o = JSONObject(executeGet("$BASE/api/providers"))
        val engine = o.optJSONObject("engine") ?: JSONObject()
        val list = mutableListOf<ProviderInfo>()
        o.optJSONArray("providers")?.let { arr -> for (i in 0 until arr.length()) list.add(arr.getJSONObject(i).parseProviderInfo()) }
        ProviderEngine(engine.optBoolean("online", false), engine.optStringOrNull("active"), engine.optDouble("health", 0.0).toFloat()) to list
    }

    suspend fun updateProvider(name: String, enabled: Boolean?, model: String? = null): ProviderInfo = withContext(Dispatchers.IO) {
        val body = JSONObject().put("name", name).putOpt("enabled", enabled).putOpt("model", model).toString().toRequestBody(json)
        execute("$BASE/api/providers", body).let { JSONObject(it).parseProviderInfo() }
    }

    suspend fun tools(): Pair<List<ToolInfo>, ToolsUsage> = withContext(Dispatchers.IO) {
        val o = JSONObject(executeGet("$BASE/api/tools"))
        val list = mutableListOf<ToolInfo>()
        o.optJSONArray("tools")?.let { arr -> for (i in 0 until arr.length()) list.add(arr.getJSONObject(i).parseToolInfo()) }
        val usage = o.optJSONObject("usage_today")
        list to ToolsUsage(usage?.optInt("tools_used", 0) ?: 0, usage?.optInt("success_rate", 0) ?: 0)
    }

    suspend fun reportDeviceStatus(accessibility: Boolean, notifications: Boolean, microphone: Boolean, bodyVersion: String) {
        withContext(Dispatchers.IO) {
            val body = JSONObject().put("accessibility", accessibility).put("notifications", notifications)
                .put("microphone", microphone).put("body_version", bodyVersion).toString().toRequestBody(json)
            try { execute("$BASE/api/device_status", body) } catch (_: IOException) { /* best effort */ }
        }
    }

    suspend fun feedback(rating: String, comment: String? = null) {
        withContext(Dispatchers.IO) {
            val body = JSONObject().put("rating", rating).putOpt("comment", comment).toString().toRequestBody(json)
            try { execute("$BASE/api/feedback", body) } catch (_: IOException) { /* best effort */ }
        }
    }

    // ------------------------------------------------------------------
    // Real provider connection test (run on-device, key never leaves except to that provider)
    // ------------------------------------------------------------------

    suspend fun testProvider(config: ProviderConfig): Result<Int> = withContext(Dispatchers.IO) {
        val start = System.currentTimeMillis()
        try {
            val url = when (config.type) {
                ProviderType.GROQ -> "https://api.groq.com/openai/v1/models"
                ProviderType.OPENAI -> "https://api.openai.com/v1/models"
                ProviderType.OPENROUTER -> "https://openrouter.ai/api/v1/models"
                ProviderType.GEMINI -> "https://generativelanguage.googleapis.com/v1beta/models?key=${Uri.encode(config.apiKey)}"
                ProviderType.CUSTOM -> config.endpoint.orEmpty()
            }
            if (url.isBlank()) return@withContext Result.failure<Int>(IOException("Endpoint is not configured"))
            val builder = Request.Builder().url(url)
            if (config.type != ProviderType.GEMINI) {
                builder.header("Authorization", "Bearer ${config.apiKey}")
            }
            val request = builder.get().build()
            client.newCall(request).execute().use { response ->
                if (!response.isSuccessful) return@withContext Result.failure<Int>(IOException("HTTP ${response.code}"))
                Result.success((System.currentTimeMillis() - start).toInt())
            }
        } catch (e: Exception) {
            Result.failure<Int>(e)
        }
    }

    // ------------------------------------------------------------------
    // SSE live stream (state + action logs)
    // ------------------------------------------------------------------

    private var currentSse: EventSource? = null
    private val sseStarted = AtomicBoolean(false)

    fun startEventStream(
        onOpen: () -> Unit,
        onState: (BrainState) -> Unit,
        onLogs: (List<ActionLogEntry>) -> Unit,
        onLog: (ActionLogEntry) -> Unit,
        onFailure: (Throwable) -> Unit
    ) {
        if (!sseStarted.compareAndSet(false, true)) stopEventStream()
        val request = Request.Builder().url("$BASE/api/events").header("Accept", "text/event-stream").build()
        val listener = object : EventSourceListener() {
            override fun onOpen(eventSource: EventSource, response: Response) = onOpen()

            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                try {
                    val obj = JSONObject(data)
                    when (obj.optString("type")) {
                        "hello" -> {
                            obj.optJSONObject("state")?.let { onState(it.parseBrainState()) }
                            val arr = obj.optJSONArray("logs")
                            if (arr != null) onLogs(List(arr.length()) { arr.getJSONObject(it).parseActionLog() })
                        }
                        "state" -> obj.optJSONObject("state")?.let { onState(it.parseBrainState()) }
                        "log" -> obj.optJSONObject("log")?.let { onLog(it.parseActionLog()) }
                    }
                } catch (_: Exception) { /* malformed event: ignore */ }
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                sseStarted.set(false)
                onFailure(t ?: IOException("SSE connection failed"))
            }

            override fun onClosed(eventSource: EventSource) {
                sseStarted.set(false)
                onFailure(IOException("SSE stream closed"))
            }
        }
        currentSse = EventSources.createFactory(sseClient).newEventSource(request, listener)
    }

    fun stopEventStream() {
        currentSse?.cancel()
        currentSse = null
        sseStarted.set(false)
    }

    // ------------------------------------------------------------------
    // HTTP plumbing
    // ------------------------------------------------------------------

    private fun execute(url: String, body: RequestBody): String =
        client.newCall(Request.Builder().url(url).post(body).build()).execute().use { r ->
            val t = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("HTTP ${r.code}: $t")
            if (t.isBlank()) throw IOException("Empty response")
            t
        }

    private fun executeGet(url: String): String =
        client.newCall(Request.Builder().url(url).get().build()).execute().use { r ->
            val t = r.body?.string().orEmpty()
            if (!r.isSuccessful) throw IOException("HTTP ${r.code}: $t")
            if (t.isBlank()) throw IOException("Empty response")
            t
        }

    private fun parseAsk(raw: String): AskResponse {
        val o = JSONObject(raw)
        val c = o.optJSONObject("command")?.let {
            AndroidCommand(
                it.getString("action"),
                it.optString("text").takeIf { value -> value.isNotBlank() },
                it.optString("package_name").takeIf { value -> value.isNotBlank() },
                it.opt("x")?.takeIf { v -> v != JSONObject.NULL }?.let { v -> (v as? Number)?.toFloat() },
                it.opt("y")?.takeIf { v -> v != JSONObject.NULL }?.let { v -> (v as? Number)?.toFloat() },
                it.optString("node_id").takeIf { value -> value.isNotBlank() },
                it.optString("direction").takeIf { value -> value.isNotBlank() }
            )
        }
        val p = o.optJSONObject("update_proposal")?.let {
            UpdateProposal(it.getString("proposal_id"), it.getString("file_path"), it.getString("module_name"), it.getString("new_code"), it.getString("summary"))
        }
        val a = o.optJSONObject("action")?.let {
            AgentAction(
                it.getString("tool"),
                it.optJSONObject("args") ?: JSONObject(),
                it.optString("tool_call_id").takeIf { value -> value.isNotBlank() },
                it.optString("thought").takeIf { value -> value.isNotBlank() }
            )
        }
        return AskResponse(
            o.optString("response", ""),
            o.optString("route", "llm"),
            c, p,
            o.optString("error").takeIf { value -> value.isNotBlank() },
            a,
            o.optBoolean("needs_tool_result", false),
            o.optString("thought").takeIf { value -> value.isNotBlank() },
            o.optInt("steps", 0)
        )
    }
}
