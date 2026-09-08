package com.ronin.ai.data

import com.ronin.ai.network.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*

/**
 * Singleton live view of the Brain:
 * - state (orb), action logs (SSE stream with polling fallback),
 *   health (drives waveform history), summary (dashboard).
 */
object BrainRepository {

    private val _state = MutableStateFlow(BrainState("idle", "Brain offline", null, null, null, online = false))
    val state: StateFlow<BrainState> = _state.asStateFlow()

    private val _logs = MutableStateFlow<List<ActionLogEntry>>(emptyList())
    val logs: StateFlow<List<ActionLogEntry>> = _logs.asStateFlow()

    private val _health = MutableStateFlow<HealthInfo?>(null)
    val health: StateFlow<HealthInfo?> = _health.asStateFlow()

    private val _summary = MutableStateFlow<SummaryInfo?>(null)
    val summary: StateFlow<SummaryInfo?> = _summary.asStateFlow()

    /** Last CPU samples (from periodic health polls) for the dashboard waveform. */
    private val _cpuHistory = MutableStateFlow<List<Float>>(emptyList())
    val cpuHistory: StateFlow<List<Float>> = _cpuHistory.asStateFlow()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var connected = false
    private var sseAlive = false

    fun connect() {
        if (connected) return
        connected = true
        scope.launch { refreshSnapshot() }
        scope.launch { runSseLoop() }
        scope.launch { runPollLoop() }
    }

    fun disconnect() {
        connected = false
        ApiClient.stopEventStream()
        sseAlive = false
    }

    private suspend fun refreshSnapshot() {
        try {
            _state.value = ApiClient.state().copy(online = true)
            _logs.value = ApiClient.actionLogs(60)
        } catch (_: Exception) {
            _state.value = _state.value.copy(online = false, message = "Brain offline")
        }
        try {
            _health.value = ApiClient.healthDetail()
        } catch (_: Exception) { /* handled by poll loop */ }
        try {
            _summary.value = ApiClient.summary()
        } catch (_: Exception) { /* handled by poll loop */ }
    }

    private fun runSseLoop() {
        scope.launch {
            while (isActive) {
                sseAlive = false
                ApiClient.startEventStream(
                    onOpen = { sseAlive = true },
                    onState = { s -> _state.value = s.copy(online = true) },
                    onLogs = { list -> _logs.value = list },
                    onLog = { entry -> _logs.value = (_logs.value + entry).takeLast(200) },
                    onFailure = { /* reconnect handled by the delay below */ }
                )
                delay(15_000) // reconnect cadence after failure/closed
            }
        }
    }

    private fun runPollLoop() {
        scope.launch {
            var tick = 0
            while (isActive) {
                tick++
                val online = try {
                    val h = withTimeoutOrNull(4_000) { ApiClient.healthDetail() }
                    if (h != null) {
                        _health.value = h
                        _cpuHistory.value = (_cpuHistory.value + h.cpuPercent).takeLast(32)
                        true
                    } else false
                } catch (_: Exception) { false }

                if (!online) {
                    if (_state.value.online) _state.value = _state.value.copy(online = false, message = "Brain offline")
                } else {
                    if (!_state.value.online) _state.value = _state.value.copy(online = true)
                    if (!sseAlive) {
                        // SSE down -> fall back to polling state + logs
                        try {
                            _state.value = ApiClient.state().copy(online = true)
                        } catch (_: Exception) { /* keep last */ }
                        try {
                            val fresh = ApiClient.actionLogs(60)
                            if (fresh.isNotEmpty()) _logs.value = fresh
                        } catch (_: Exception) { /* keep last */ }
                    }
                }

                if (tick % 10 == 0) { // ~30s
                    runCatching { _summary.value = ApiClient.summary() }
                }
                delay(3_000)
            }
        }
    }

    fun refreshSummary() {
        scope.launch { runCatching { _summary.value = ApiClient.summary() } }
    }

    fun refreshHealth() {
        scope.launch {
            runCatching {
                val h = ApiClient.healthDetail()
                _health.value = h
                _cpuHistory.value = (_cpuHistory.value + h.cpuPercent).takeLast(32)
            }
        }
    }
}
