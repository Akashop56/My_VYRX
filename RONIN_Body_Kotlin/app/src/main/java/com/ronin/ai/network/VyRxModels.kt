package com.ronin.ai.network

import org.json.JSONObject

// ---------------------------------------------------------------------------
// AI state (drives the orb)
// ---------------------------------------------------------------------------

data class BrainState(
    val state: String,           // idle | listening | thinking | executing | learning
    val message: String,
    val provider: String?,
    val model: String?,
    val responseMs: Int?,
    val version: String? = null,
    val online: Boolean = false
)

fun JSONObject.parseBrainState(): BrainState = BrainState(
    state = optString("state", "idle"),
    message = optString("message", ""),
    provider = optStringOrNull("provider"),
    model = optStringOrNull("model"),
    responseMs = opt("response_ms").takeIf { it != null && it != JSONObject.NULL }?.let { (it as? Number)?.toInt() },
    version = optStringOrNull("version"),
    online = optBoolean("online", true)
)

// ---------------------------------------------------------------------------
// System health (Brain side, /proc based — no psutil)
// ---------------------------------------------------------------------------

data class HealthInfo(
    val version: String?,
    val uptimeSeconds: Long,
    val cpuPercent: Float,
    val memoryPercent: Float,
    val memoryUsedMb: Float,
    val memoryTotalMb: Float,
    val storageTotalGb: Float,
    val storageFreeGb: Float,
    val storageUsedGb: Float,
    val memoryDbBytes: Long,
    val python: String?
)

fun JSONObject.parseHealthInfo(): HealthInfo = HealthInfo(
    version = optStringOrNull("version"),
    uptimeSeconds = optLong("uptime_seconds", 0L),
    cpuPercent = optDouble("cpu_percent", 0.0).toFloat(),
    memoryPercent = optDouble("memory_percent", 0.0).toFloat(),
    memoryUsedMb = optDouble("memory_used_mb", 0.0).toFloat(),
    memoryTotalMb = optDouble("memory_total_mb", 0.0).toFloat(),
    storageTotalGb = optDouble("storage_total_gb", 0.0).toFloat(),
    storageFreeGb = optDouble("storage_free_gb", 0.0).toFloat(),
    storageUsedGb = optDouble("storage_used_gb", 0.0).toFloat(),
    memoryDbBytes = optLong("memory_db_bytes", 0L),
    python = optStringOrNull("python")
)

// ---------------------------------------------------------------------------
// Action log
// ---------------------------------------------------------------------------

data class ActionLogEntry(val time: String, val level: String, val text: String)

fun JSONObject.parseActionLog(): ActionLogEntry = ActionLogEntry(
    time = optString("time", ""),
    level = optString("level", "info"),
    text = optString("text", "")
)

// ---------------------------------------------------------------------------
// Summary / dashboard
// ---------------------------------------------------------------------------

data class DayStats(
    val tasksCompleted: Int,
    val autoTasks: Int,
    val learned: Int,
    val voiceCommands: Int,
    val appsOpened: Int,
    val webSearches: Int
)

fun JSONObject.parseDayStats(): DayStats = DayStats(
    tasksCompleted = optInt("tasks_completed", 0),
    autoTasks = optInt("auto_tasks", 0),
    learned = optInt("learned", 0),
    voiceCommands = optInt("voice_commands", 0),
    appsOpened = optInt("apps_opened", 0),
    webSearches = optInt("web_searches", 0)
)

data class TaskPoint(val label: String, val tasks: Int)
data class TopTool(val label: String, val count: Int, val percent: Float)
data class AutomationEvent(val label: String, val detail: String, val time: String, val success: Boolean, val auto: Boolean)
data class LearningStatus(val status: String, val progress: Int, val message: String)
data class MemoryStats(val total: Int, val sizeBytes: Long, val healthScore: Int, val retentionDays: Int)

data class SummaryInfo(
    val today: DayStats,
    val yesterday: DayStats,
    val tasksOverTime: List<TaskPoint>,
    val topTools: List<TopTool>,
    val recentAutomations: List<AutomationEvent>,
    val learning: LearningStatus,
    val memory: MemoryStats
) {
    val toolsUsedToday: Int get() = topTools.sumOf { it.count }
    val successRateToday: Int get() {
        val events = recentAutomations
        if (events.isEmpty()) return 0
        return (events.count { it.success } * 100) / events.size
    }
}

fun JSONObject.parseSummary(): SummaryInfo {
    val over = mutableListOf<TaskPoint>()
    optJSONArray("tasks_over_time")?.let { arr ->
        for (i in 0 until arr.length()) over.add(
            TaskPoint(arr.getJSONObject(i).optString("label", ""), arr.getJSONObject(i).optInt("tasks", 0))
        )
    }
    val top = mutableListOf<TopTool>()
    optJSONArray("top_tools")?.let { arr ->
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            top.add(TopTool(o.optString("label", o.optString("tool", "")), o.optInt("count", 0), o.optDouble("percent", 0.0).toFloat()))
        }
    }
    val auto = mutableListOf<AutomationEvent>()
    optJSONArray("recent_automations")?.let { arr ->
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            auto.add(AutomationEvent(o.optString("label", ""), o.optString("detail", ""), o.optString("time", ""), o.optBoolean("success", true), o.optBoolean("auto", false)))
        }
    }
    val learning = optJSONObject("learning") ?: JSONObject()
    val memory = optJSONObject("memory") ?: JSONObject()
    return SummaryInfo(
        today = (optJSONObject("today") ?: JSONObject()).parseDayStats(),
        yesterday = (optJSONObject("yesterday") ?: JSONObject()).parseDayStats(),
        tasksOverTime = over,
        topTools = top,
        recentAutomations = auto,
        learning = LearningStatus(learning.optString("status", "idle"), learning.optInt("progress", 0), learning.optString("message", "")),
        memory = MemoryStats(memory.optInt("total", 0), memory.optLong("size_bytes", 0L), memory.optInt("health_score", 0), memory.optInt("retention_days", 0))
    )
}

// ---------------------------------------------------------------------------
// Memory module (Personal / Experience / Knowledge)
// ---------------------------------------------------------------------------

data class MemoryItem(
    val id: Long,
    val category: String,
    val title: String,
    val content: String,
    val importance: Int,
    val pinned: Boolean,
    val source: String,
    val confidence: Float,
    val createdAt: String,
    val updatedAt: String,
    val lastUsed: String?,
    val useCount: Int
)

fun JSONObject.parseMemoryItem(): MemoryItem = MemoryItem(
    id = optLong("id", 0L),
    category = optString("category", "personal"),
    title = optString("title", ""),
    content = optString("content", ""),
    importance = optInt("importance", 3),
    pinned = when (val p = opt("pinned")) {
        is Boolean -> p
        is Number -> p.toDouble() != 0.0
        is String -> p.equals("true", true) || p == "1"
        else -> false
    },
    source = optString("source", "user"),
    confidence = optDouble("confidence", 0.9).toFloat(),
    createdAt = optString("created_at", ""),
    updatedAt = optString("updated_at", ""),
    lastUsed = optStringOrNull("last_used"),
    useCount = optInt("use_count", 0)
)

// ---------------------------------------------------------------------------
// API providers
// ---------------------------------------------------------------------------

data class ProviderInfo(
    val name: String,
    val label: String,
    val configured: Boolean,
    val enabled: Boolean,
    val active: Boolean,
    val model: String?,
    val latencyMs: Int?,
    val avgLatencyMs: Int?,
    val requestsToday: Int,
    val errorsToday: Int,
    val successRate: Float?
)

fun JSONObject.parseProviderInfo(): ProviderInfo = ProviderInfo(
    name = optString("name", ""),
    label = optString("label", optString("name", "")),
    configured = optBoolean("configured", false),
    enabled = optBoolean("enabled", true),
    active = optBoolean("active", false),
    model = optStringOrNull("model"),
    latencyMs = optIntOrNull("latency_ms"),
    avgLatencyMs = optIntOrNull("avg_latency_ms"),
    requestsToday = optInt("requests_today", 0),
    errorsToday = optInt("errors_today", 0),
    successRate = opt("success_rate").takeIf { it != null && it != JSONObject.NULL }?.let { (it as? Number)?.toFloat() }
)

data class ProviderEngine(val online: Boolean, val active: String?, val health: Float)

// ---------------------------------------------------------------------------
// Tools module
// ---------------------------------------------------------------------------

data class ToolInfo(
    val id: String,
    val name: String,
    val category: String,
    val description: String,
    val active: Boolean,
    val reason: String
)

fun JSONObject.parseToolInfo(): ToolInfo = ToolInfo(
    id = optString("id", ""),
    name = optString("name", ""),
    category = optString("category", "device"),
    description = optString("description", ""),
    active = optBoolean("active", false),
    reason = optString("reason", "")
)

data class ToolsUsage(val toolsUsed: Int, val successRate: Int)

// ---------------------------------------------------------------------------
// org.json helpers
// ---------------------------------------------------------------------------

fun JSONObject.optStringOrNull(key: String): String? = when (val v = opt(key)) {
    null, JSONObject.NULL -> null
    is String -> v.ifBlank { null }
    else -> v.toString()
}

fun JSONObject.optIntOrNull(key: String): Int? =
    opt(key).takeIf { it != null && it != JSONObject.NULL }?.let { (it as? Number)?.toInt() }
