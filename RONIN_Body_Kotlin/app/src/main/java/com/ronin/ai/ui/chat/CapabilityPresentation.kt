package com.ronin.ai.ui.chat

import com.ronin.ai.network.AgentEvent
import org.json.JSONObject
import java.util.Locale

/** Safe, presentation-facing capability vocabulary. */
enum class CapabilityKind(val label: String) {
    REASONING("Reasoning"),
    WEB_RETRIEVAL("Web"),
    LOCAL_KNOWLEDGE_SEARCH("Local Knowledge"),
    DEVICE_INTERACTION("Device")
}

enum class CapabilityLocation(val label: String) {
    REMOTE("Remote"),
    LOCAL("Local")
}

enum class CapabilityHealthState(val label: String) {
    AVAILABLE("Available"),
    DEGRADED("Degraded"),
    UNAVAILABLE("Unavailable"),
    UNKNOWN("Unknown")
}

enum class CapabilityActivityState(val label: String) {
    ACTIVE("Active"),
    SUCCESS("Success"),
    PARTIAL("Partial"),
    FAILED("Failed")
}

enum class RetrievalMode(val label: String) {
    LEXICAL("Lexical"),
    SEMANTIC("Semantic"),
    HYBRID("Hybrid")
}

data class KnowledgeCitation(
    val source: String,
    val lineStart: Int,
    val lineEnd: Int,
    /** Actual backend method; null means the backend did not verify one. */
    val retrieval: RetrievalMode? = null
) {
    val locationLabel: String
        get() = if (lineStart == lineEnd) "line $lineStart" else "lines $lineStart-$lineEnd"
}

enum class RecoverySummary(val label: String) {
    CAPABILITY_CHANGED("Capability changed"),
    RETRYING("Retrying"),
    FALLBACK_AVAILABLE("Fallback available"),
    /** Source compatibility for callers that used the pre-15.1 name. */
    ALTERNATIVE_SELECTED("Capability changed")
}

data class CapabilityRecovery(
    val summary: RecoverySummary,
    val sequence: Int
)

/**
 * One safe UI event. Nullable locality and health mean that the backend did
 * not supply a verified value; they do not mean offline or healthy.
 */
data class CapabilityActivity(
    val capability: CapabilityKind,
    val state: CapabilityActivityState,
    val location: CapabilityLocation? = null,
    val health: CapabilityHealthState? = null,
    val implementation: String? = null,
    val retrieval: RetrievalMode? = null,
    val citations: List<KnowledgeCitation> = emptyList(),
    val recovery: CapabilityRecovery? = null,
    val sequence: Int = 0,
    internal val correlationKey: String? = null
) {
    val displayLabel: String get() = capability.label

    val indicatorLabel: String
        get() = buildString {
            append(capability.label)
            location?.let { append(" • ").append(it.label) }
            append(" • ").append(state.label)
            health?.let { append(" • ").append(it.label) }
        }
}

/**
 * Converts structured stream fields into safe presentation events.
 * Capability, locality, retrieval, and recovery are never reconstructed from
 * a tool name, a requested mode, or strategy prose.
 */
object CapabilityPresentationAdapter {
    private val citationPattern = Regex(
        "^\\s*\\[\\d+\\]\\s+(?:(?:chunk\\s+\\S+\\s+—\\s+))?(.+?)\\s+" +
            "\\(lines\\s+(\\d+)(?:-(\\d+))?,\\s+chars\\s+\\d+-\\d+\\)\\s*$"
    )
    private val controlTypes = setOf(
        "start", "thinking", "thought", "tool_call", "observation",
        "self_correction", "reflexion", "token", "answer_end", "done", "error"
    )

    fun fromThinking(event: AgentEvent.Thinking, sequence: Int): CapabilityActivity? {
        if (event.phase !in setOf("call", "reason", "verify")) return null
        return CapabilityActivity(
            capability = CapabilityKind.REASONING,
            state = CapabilityActivityState.ACTIVE,
            sequence = sequence,
            correlationKey = null
        )
    }

    fun fromToolCall(event: AgentEvent.ToolCall, sequence: Int): CapabilityActivity? {
        val capability = capabilityKind(event.semanticCapability) ?: return null
        return CapabilityActivity(
            capability = capability,
            state = CapabilityActivityState.ACTIVE,
            location = location(event.locality),
            sequence = sequence,
            correlationKey = event.callId ?: "legacy:${event.step}:${event.tool}"
        )
    }

    fun fromObservation(
        event: AgentEvent.Observation,
        previous: CapabilityActivity?,
        sequence: Int
    ): CapabilityActivity? {
        val capability = previous?.capability ?: capabilityKind(event.semanticCapability) ?: return null
        val base = previous ?: CapabilityActivity(
            capability = capability,
            state = CapabilityActivityState.ACTIVE,
            location = location(event.locality),
            sequence = sequence,
            correlationKey = event.callId ?: "legacy:${event.step}:${event.tool}"
        )
        val actualRetrieval = retrievalMode(event.retrievalMethod)
        val citations = if (capability == CapabilityKind.LOCAL_KNOWLEDGE_SEARCH) {
            parseKnowledgeCitations(event.result, actualRetrieval)
        } else {
            emptyList()
        }
        return base.copy(
            state = activityState(event),
            location = location(event.locality) ?: base.location,
            retrieval = actualRetrieval ?: base.retrieval,
            citations = citations,
            sequence = sequence
        )
    }

    fun completeReasoning(
        previous: CapabilityActivity?,
        success: Boolean,
        sequence: Int
    ): CapabilityActivity? {
        if (previous?.capability != CapabilityKind.REASONING || previous.recovery != null) return null
        return previous.copy(
            state = if (success) CapabilityActivityState.SUCCESS else CapabilityActivityState.FAILED,
            sequence = sequence
        )
    }

    fun fromSelfCorrection(
        event: AgentEvent.SelfCorrection,
        sequence: Int,
        previous: CapabilityActivity? = null
    ): CapabilityActivity? {
        val summary = when (event.transitionType) {
            "capability_replacement" -> {
                // A replacement is real only when both candidate identities
                // and an authoritative successful outcome are supplied. The
                // word alternative in strategy is not evidence.
                if (event.previousCandidate.isNullOrBlank() ||
                    event.selectedCandidate.isNullOrBlank() ||
                    event.outcome !in setOf("success", "partial_success")) {
                    return null
                }
                RecoverySummary.CAPABILITY_CHANGED
            }
            "fallback_proposal" -> RecoverySummary.FALLBACK_AVAILABLE
            "retry" -> RecoverySummary.RETRYING
            else -> return null
        }
        val capability = capabilityKind(event.semanticCapability) ?: previous?.capability ?: return null
        return CapabilityActivity(
            capability = capability,
            state = CapabilityActivityState.ACTIVE,
            location = location(event.locality) ?: previous?.location,
            implementation = previous?.implementation,
            recovery = CapabilityRecovery(summary, sequence),
            sequence = sequence,
            correlationKey = event.callId?.let { "recovery:$it" } ?: previous?.correlationKey
        )
    }

    fun parseKnowledgeCitations(
        result: String?,
        retrieval: RetrievalMode? = null
    ): List<KnowledgeCitation> {
        if (result.isNullOrBlank()) return emptyList()
        return result.lineSequence().mapNotNull { line ->
            val match = citationPattern.matchEntire(line) ?: return@mapNotNull null
            val source = safeSource(match.groupValues[1]) ?: return@mapNotNull null
            val start = match.groupValues[2].toIntOrNull()
                ?.takeIf { it > 0 } ?: return@mapNotNull null
            val end = match.groupValues[3].toIntOrNull() ?: start
            if (end < start) return@mapNotNull null
            KnowledgeCitation(source, start, end, retrieval)
        }.toList()
    }

    /**
     * Sanitize only an explicitly marked control envelope. Ordinary JSON,
     * code, partial JSON, and user text remain ordinary answer content even if
     * they contain keys named action, tool, or capability.
     */
    fun sanitizeAnswer(text: String): String {
        val parsed = internalControlObject(text) ?: return text
        return parsed.optString("response", "").trim()
    }

    fun isInternalMetadata(text: String): Boolean = internalControlObject(text) != null

    /** A terminal detail derived from the structured capability field. */
    fun safeToolDetail(event: AgentEvent.ToolCall): String? {
        return when (capabilityKind(event.semanticCapability)) {
            CapabilityKind.LOCAL_KNOWLEDGE_SEARCH -> "Local knowledge search"
            CapabilityKind.WEB_RETRIEVAL -> "Web retrieval"
            CapabilityKind.DEVICE_INTERACTION -> "Device action"
            CapabilityKind.REASONING -> "Reasoning capability"
            null -> null
        }
    }

    /** A safe terminal observation summary; source excerpts stay citation-only. */
    fun safeObservationText(event: AgentEvent.Observation): String {
        if (capabilityKind(event.semanticCapability) == CapabilityKind.LOCAL_KNOWLEDGE_SEARCH) {
            return if (event.ok) "Local knowledge result received" else "Local knowledge search failed"
        }
        val sanitized = sanitizeAnswer(event.result).trim()
        if (sanitized.isBlank() && event.result.isNotBlank()) return "Tool result received"
        return sanitized.replace(Regex("\\s+"), " ").take(200)
    }

    private fun activityState(event: AgentEvent.Observation): CapabilityActivityState = when {
        !event.ok -> CapabilityActivityState.FAILED
        event.outcome == "partial_success" -> CapabilityActivityState.PARTIAL
        else -> CapabilityActivityState.SUCCESS
    }

    private fun capabilityKind(raw: String?): CapabilityKind? = when (raw?.trim()?.lowercase(Locale.US)) {
        "reasoning" -> CapabilityKind.REASONING
        "web_retrieval" -> CapabilityKind.WEB_RETRIEVAL
        "local_knowledge_search" -> CapabilityKind.LOCAL_KNOWLEDGE_SEARCH
        "device_interaction" -> CapabilityKind.DEVICE_INTERACTION
        else -> null
    }

    private fun location(raw: String?): CapabilityLocation? = when (raw?.trim()?.lowercase(Locale.US)) {
        "local" -> CapabilityLocation.LOCAL
        "remote" -> CapabilityLocation.REMOTE
        else -> null
    }

    private fun retrievalMode(raw: String?): RetrievalMode? = when (raw?.trim()?.lowercase(Locale.US)) {
        "lexical_fts5", "lexical" -> RetrievalMode.LEXICAL
        "semantic_cosine", "semantic" -> RetrievalMode.SEMANTIC
        "hybrid_rrf", "hybrid" -> RetrievalMode.HYBRID
        else -> null
    }

    private fun internalControlObject(text: String): JSONObject? {
        val trimmed = text.trim()
        if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null
        val parsed = runCatching { JSONObject(trimmed) }.getOrNull() ?: return null
        val type = parsed.optString("type").trim().lowercase(Locale.US)
        if (type in controlTypes) return parsed
        // Legacy action responses have an explicit route plus an action object;
        // a normal user JSON object with an action/tool/capability key does not.
        val route = parsed.optString("route").trim().lowercase(Locale.US)
        if (route in setOf("agent_action", "android_command") && parsed.opt("action") is JSONObject) {
            return parsed
        }
        return null
    }

    private fun safeSource(raw: String): String? {
        if (raw != raw.trim()) return null
        val source = raw.replace('\\', '/')
        if (source.isEmpty() || source.length > 240) return null
        if (source.startsWith("/") || source.startsWith("~") ||
            source.contains("://") || source.contains(':')) return null
        if (source.any { it.isISOControl() }) return null
        if (source.split('/').any { it == ".." || it == "." || it.isEmpty() }) return null
        val lowered = source.lowercase(Locale.US)
        if (listOf(
                ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm",
                ".sqlite-wal", ".sqlite-shm", ".sqlite3-wal", ".sqlite3-shm"
            ).any { lowered.endsWith(it) }
        ) return null
        val basename = lowered.substringAfterLast('/')
        if (basename.all { it.isDigit() } || basename in setOf(
                "sqlite_sequence", "knowledge_chunks", "knowledge_chunks_fts",
                "knowledge_files", "knowledge_embedding_metadata"
            )
        ) return null
        if (Regex("^(chunk|chunk_id|rowid|file_id|source_id)[_:-]?\\d+$").matches(basename)) return null
        if (listOf(
                "chunk:", "chunk_id:", "rowid:", "sqlite_",
                "knowledge_chunks:", "knowledge_files:",
                "knowledge_embedding_metadata:"
            ).any { lowered.startsWith(it) }
        ) return null
        return source
    }
}
