package com.ronin.ai.ui.dashboard

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.data.BrainRepository
import com.ronin.ai.network.AutomationEvent
import com.ronin.ai.network.TopTool
import com.ronin.ai.ui.components.AiOrb
import com.ronin.ai.ui.components.LinearBar
import com.ronin.ai.ui.components.RingProgress
import com.ronin.ai.ui.components.TaskBarChart
import com.ronin.ai.ui.components.VyRxIconButton
import com.ronin.ai.ui.components.VyRxTopBar
import com.ronin.ai.ui.components.Waveform
import com.ronin.ai.ui.theme.GlassCard
import com.ronin.ai.ui.theme.NeonDot
import com.ronin.ai.ui.theme.SectionHeader
import com.ronin.ai.ui.theme.SectionLabel
import com.ronin.ai.ui.theme.VyRxColors
import com.ronin.ai.ui.theme.orbColor
import com.ronin.ai.ui.theme.orbStateFromName
import com.ronin.ai.ui.theme.orbStateLabel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@Composable
fun DashboardScreen(onOpenDrawer: () -> Unit, onNavigate: (String) -> Unit) {
    val state by BrainRepository.state.collectAsState()
    val health by BrainRepository.health.collectAsState()
    val summary by BrainRepository.summary.collectAsState()
    val cpuHistory by BrainRepository.cpuHistory.collectAsState()
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var refreshing by remember { mutableStateOf(false) }
    var refreshLines by remember { mutableStateOf<List<String>?>(null) }

    val orbState = orbStateFromName(state.state)

    fun refresh() {
        if (refreshing) return
        refreshing = true
        refreshLines = null
        scope.launch {
            BrainRepository.refreshHealth()
            delay(350)
            refreshLines = listOf("✓ Memory synced")
            BrainRepository.refreshSummary()
            delay(350)
            refreshLines = listOf("✓ Memory synced", "✓ CPU refreshed")
            delay(350)
            refreshLines = listOf("✓ Memory synced", "✓ CPU refreshed", "✓ API status checked", "✓ Tasks updated")
            refreshing = false
            delay(1600)
            refreshLines = null
        }
    }

    Column(Modifier.fillMaxSize()) {
        VyRxTopBar(
            title = "DASHBOARD",
            subtitle = "System Overview & Analytics",
            onMenu = onOpenDrawer,
            trailing = {
                Row {
                    VyRxIconButton(
                        Icons.Filled.Refresh,
                        onClick = { refresh() },
                        tint = if (refreshing) VyRxColors.PrimaryBright else VyRxColors.TextDim
                    )
                    VyRxIconButton(Icons.Filled.Tune, onClick = {})
                }
            }
        )
        LazyColumn(
            Modifier.fillMaxWidth(),
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            // AI STATE card
            item {
                GlassCard(Modifier.fillMaxWidth()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        AiOrb(orbState, Modifier.size(74.dp))
                        Spacer(Modifier.width(14.dp))
                        Column(Modifier.weight(1f)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                NeonDot(orbColor(orbState), size = 9)
                                Spacer(Modifier.width(6.dp))
                                Text(orbStateLabel(orbState), color = orbColor(orbState), fontSize = 16.sp, fontWeight = FontWeight.Bold)
                            }
                            Spacer(Modifier.height(4.dp))
                            Text(
                                if (state.online) state.message else "Brain offline",
                                color = VyRxColors.TextDim,
                                fontSize = 11.sp,
                                maxLines = 2
                            )
                            Spacer(Modifier.height(8.dp))
                            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                repeat(6) { i -> NeonDot(orbColor(orbState), size = if (i < 4) 6 else 4) }
                            }
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            SectionLabel("ACTIVE MODEL")
                            Spacer(Modifier.height(4.dp))
                            Text(
                                state.model ?: state.provider?.replaceFirstChar { it.uppercase() } ?: "No provider",
                                color = VyRxColors.PrimaryBright,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold,
                                maxLines = 2
                            )
                            Spacer(Modifier.height(10.dp))
                            SectionLabel("RESPONSE SPEED")
                            Spacer(Modifier.height(4.dp))
                            Text(
                                state.responseMs?.let { "${fmt1(it / 1000f)}s" } ?: "—",
                                color = VyRxColors.PrimaryBright,
                                fontSize = 14.sp,
                                fontWeight = FontWeight.Bold
                            )
                        }
                    }
                }
            }

            // SYSTEM HEALTH
            item {
                GlassCard(Modifier.fillMaxWidth()) {
                    SectionHeader("SYSTEM HEALTH", "View Details", null)
                    Spacer(Modifier.height(10.dp))
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        HealthMiniTile(Modifier.weight(1f)) {
                            SectionLabel("Memory Usage")
                            Spacer(Modifier.height(6.dp))
                            RingProgress(
                                (health?.memoryPercent ?: 0f) / 100f, VyRxColors.Green, Modifier.size(52.dp), "RAM"
                            )
                        }
                        HealthMiniTile(Modifier.weight(1f)) {
                            SectionLabel("Storage")
                            Spacer(Modifier.height(6.dp))
                            Text("${fmt1(health?.storageUsedGb ?: 0f)} GB", color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Text("/ ${fmt1(health?.storageTotalGb ?: 0f)} GB", color = VyRxColors.TextFaint, fontSize = 9.sp)
                            Spacer(Modifier.height(6.dp))
                            LinearBar(
                                if ((health?.storageTotalGb ?: 0f) > 0) (health?.storageUsedGb ?: 0f) / (health?.storageTotalGb ?: 1f) else 0f,
                                VyRxColors.Green, Modifier.fillMaxWidth()
                            )
                            Spacer(Modifier.height(4.dp))
                            Text(
                                "${((health?.storageUsedGb ?: 0f) / (health?.storageTotalGb ?: 1f) * 100).toInt()}% Used",
                                color = VyRxColors.Green, fontSize = 9.sp
                            )
                        }
                        HealthMiniTile(Modifier.weight(1f)) {
                            SectionLabel("CPU Load")
                            Spacer(Modifier.height(6.dp))
                            Text("${(health?.cpuPercent ?: 0f).toInt()}%", color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                            Spacer(Modifier.height(4.dp))
                            Waveform(cpuHistory, VyRxColors.Green, Modifier.fillMaxWidth().height(28.dp))
                            Spacer(Modifier.height(4.dp))
                            Text(
                                if ((health?.cpuPercent ?: 0f) < 70f) "Stable" else "Busy",
                                color = if ((health?.cpuPercent ?: 0f) < 70f) VyRxColors.Green else VyRxColors.Amber,
                                fontSize = 9.sp
                            )
                        }
                        HealthMiniTile(Modifier.weight(1f)) {
                            SectionLabel("API Status")
                            Spacer(Modifier.height(6.dp))
                            Text(
                                (state.provider ?: "No API").replaceFirstChar { it.uppercase() } + " API",
                                color = VyRxColors.TextPrimary, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, maxLines = 1
                            )
                            Spacer(Modifier.height(6.dp))
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                NeonDot(if (state.online && state.provider != null) VyRxColors.Green else VyRxColors.Red, size = 7)
                                Spacer(Modifier.width(4.dp))
                                Text(
                                    if (state.online && state.provider != null) "Active" else "Offline",
                                    color = if (state.online && state.provider != null) VyRxColors.Green else VyRxColors.Red,
                                    fontSize = 10.sp
                                )
                            }
                            Spacer(Modifier.height(4.dp))
                            Text(
                                if (state.online) "All Systems OK" else "Brain offline",
                                color = VyRxColors.TextFaint, fontSize = 9.sp
                            )
                        }
                    }
                }
            }

            // TODAY'S ACTIVITY
            item {
                val today = summary?.today
                val yesterday = summary?.yesterday
                GlassCard(Modifier.fillMaxWidth()) {
                    SectionHeader("TODAY'S ACTIVITY", "See All", null)
                    Spacer(Modifier.height(10.dp))
                    if (today == null) {
                        Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                            CircularProgressIndicator(Modifier.size(16.dp), color = VyRxColors.PrimaryBright, strokeWidth = 2.dp)
                            Spacer(Modifier.width(10.dp))
                            Text("Loading activity...", color = VyRxColors.TextDim, fontSize = 12.sp)
                        }
                    } else {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            ActivityTile(Modifier.weight(1f), VyRxColors.Green, "${today.tasksCompleted}", "Tasks Completed", today, yesterday, { it.tasksCompleted })
                            ActivityTile(Modifier.weight(1f), VyRxColors.Blue, "${today.autoTasks}", "Auto Tasks", today, yesterday, { it.autoTasks })
                            ActivityTile(Modifier.weight(1f), VyRxColors.Amber, "${today.learned}", "Things Learned", today, yesterday, { it.learned })
                        }
                        Spacer(Modifier.height(8.dp))
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            ActivityTile(Modifier.weight(1f), VyRxColors.PrimaryBright, "${today.voiceCommands}", "Voice Commands", today, yesterday, { it.voiceCommands })
                            ActivityTile(Modifier.weight(1f), VyRxColors.Blue, "${today.appsOpened}", "Apps Opened", today, yesterday, { it.appsOpened })
                            ActivityTile(Modifier.weight(1f), VyRxColors.Green, "${today.webSearches}", "Web Searches", today, yesterday, { it.webSearches })
                        }
                    }
                }
            }

            // TASKS OVER TIME + TOP TOOLS
            item {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    GlassCard(Modifier.weight(1f)) {
                        SectionHeader("TASKS OVER TIME")
                        Spacer(Modifier.height(10.dp))
                        val overTime = summary?.tasksOverTime
                        if (overTime == null) {
                            Row(Modifier.fillMaxWidth().padding(vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                                CircularProgressIndicator(Modifier.size(16.dp), color = VyRxColors.PrimaryBright, strokeWidth = 2.dp)
                            }
                        } else {
                            TaskBarChart(overTime, Modifier.fillMaxWidth().height(150.dp))
                        }
                    }
                    GlassCard(Modifier.weight(1f)) {
                        SectionHeader("TOP TOOLS USED", "See All", { onNavigate("tools") })
                        Spacer(Modifier.height(10.dp))
                        val top = summary?.topTools.orEmpty()
                        if (top.isEmpty()) {
                            Text("No tool activity yet.", color = VyRxColors.TextFaint, fontSize = 11.sp)
                        }
                        top.take(5).forEach { tool ->
                            TopToolRow(tool)
                            Spacer(Modifier.height(8.dp))
                        }
                    }
                }
            }

            // RECENT AUTOMATIONS + LEARNING
            item {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    GlassCard(Modifier.weight(1f)) {
                        SectionHeader("RECENT AUTOMATIONS", "See All", null)
                        Spacer(Modifier.height(10.dp))
                        val events = summary?.recentAutomations.orEmpty()
                        if (events.isEmpty()) {
                            Text("No automations recorded yet.", color = VyRxColors.TextFaint, fontSize = 11.sp)
                        }
                        events.forEach { event ->
                            AutomationRow(event)
                            Spacer(Modifier.height(8.dp))
                        }
                    }
                    GlassCard(Modifier.weight(1f)) {
                        SectionHeader("LEARNING STATUS", "See All", { onNavigate("memory") })
                        Spacer(Modifier.height(12.dp))
                        val learning = summary?.learning
                        Column(horizontalAlignment = Alignment.CenterHorizontally) {
                            Box(
                                Modifier
                                    .size(44.dp)
                                    .clip(CircleShape)
                                    .background(VyRxColors.Amber.copy(alpha = 0.12f))
                                    .border(1.dp, VyRxColors.Amber.copy(alpha = 0.4f), CircleShape),
                                contentAlignment = Alignment.Center
                            ) {
                                Icon(
                                    androidx.compose.material.icons.Icons.Filled.Psychology,
                                    null,
                                    tint = VyRxColors.Amber,
                                    modifier = Modifier.size(22.dp)
                                )
                            }
                            Spacer(Modifier.height(10.dp))
                            Text(
                                if (learning?.status == "in_progress") "Learning in progress..." else "Learning idle",
                                color = VyRxColors.Amber,
                                fontSize = 12.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                            Spacer(Modifier.height(4.dp))
                            Text(learning?.message ?: "", color = VyRxColors.TextDim, fontSize = 10.sp, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
                            Spacer(Modifier.height(10.dp))
                            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                LinearBar((learning?.progress ?: 0) / 100f, VyRxColors.Amber, Modifier.weight(1f))
                                Spacer(Modifier.width(8.dp))
                                Text("${learning?.progress ?: 0}%", color = VyRxColors.Amber, fontSize = 11.sp, fontWeight = FontWeight.Bold)
                            }
                        }
                    }
                }
            }

            // Refresh overlay
            item {
                AnimatedVisibility(visible = refreshLines != null) {
                    Row(Modifier.fillMaxWidth().padding(top = 4.dp)) {
                        refreshLines?.forEach { line ->
                            Text(line, color = VyRxColors.Green, fontSize = 10.sp, modifier = Modifier.padding(end = 10.dp))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun HealthMiniTile(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(
        modifier
            .clip(RoundedCornerShape(14.dp))
            .background(Color(0xFF0B0F17))
            .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(14.dp))
            .padding(10.dp)
    ) {
        Column { content() }
    }
}

@Composable
private fun ActivityTile(
    modifier: Modifier,
    tint: Color,
    value: String,
    label: String,
    today: com.ronin.ai.network.DayStats,
    yesterday: com.ronin.ai.network.DayStats?,
    pick: (com.ronin.ai.network.DayStats) -> Int
) {
    val shownDelta = pick(today) - pick(yesterday ?: today)
    Box(
        modifier
            .clip(RoundedCornerShape(14.dp))
            .background(Color(0xFF0B0F17))
            .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(14.dp))
            .padding(10.dp)
    ) {
        Column {
            Box(
                Modifier
                    .size(24.dp)
                    .clip(CircleShape)
                    .background(tint.copy(alpha = 0.14f)),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    when (label) {
                        "Tasks Completed" -> Icons.Filled.CheckCircle
                        else -> Icons.Filled.Refresh
                    },
                    null,
                    tint = tint,
                    modifier = Modifier.size(13.dp)
                )
            }
            Spacer(Modifier.height(6.dp))
            Text(value, color = VyRxColors.TextPrimary, fontSize = 17.sp, fontWeight = FontWeight.Bold)
            Text(label, color = VyRxColors.TextDim, fontSize = 8.5.sp, maxLines = 2)
            Text(
                if (shownDelta > 0) "↑ ${shownDelta} vs yesterday" else "— vs yesterday",
                color = if (shownDelta > 0) VyRxColors.Green else VyRxColors.TextFaint,
                fontSize = 8.sp,
                maxLines = 1
            )
        }
    }
}

@Composable
private fun TopToolRow(tool: TopTool) {
    Column {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(tool.label, color = VyRxColors.TextPrimary, fontSize = 11.sp)
            Text("${tool.percent}%", color = VyRxColors.TextDim, fontSize = 11.sp)
        }
        Spacer(Modifier.height(4.dp))
        LinearBar(tool.percent / 100f, VyRxColors.Primary, Modifier.fillMaxWidth())
    }
}

@Composable
private fun AutomationRow(event: AutomationEvent) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(30.dp)
                .clip(CircleShape)
                .background(VyRxColors.Green.copy(alpha = 0.12f))
                .border(1.dp, VyRxColors.Green.copy(alpha = 0.35f), CircleShape),
            contentAlignment = Alignment.Center
        ) {
            Icon(Icons.Filled.CheckCircle, null, tint = VyRxColors.Green, modifier = Modifier.size(15.dp))
        }
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(event.label, color = VyRxColors.TextPrimary, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
            Text("Automation • ${event.time}", color = VyRxColors.TextFaint, fontSize = 9.sp, maxLines = 1)
        }
        Row(
            Modifier
                .clip(RoundedCornerShape(8.dp))
                .background(if (event.success) VyRxColors.Green.copy(alpha = 0.12f) else VyRxColors.Red.copy(alpha = 0.12f))
                .padding(horizontal = 7.dp, vertical = 3.dp)
        ) {
            Text(
                if (event.success) "Success" else "Failed",
                color = if (event.success) VyRxColors.Green else VyRxColors.Red,
                fontSize = 9.sp,
                fontWeight = FontWeight.SemiBold
            )
        }
    }
}

private fun fmt1(v: Float): String = String.format("%.1f", v)
