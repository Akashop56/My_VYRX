package com.ronin.ai.ui.home

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Book
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.data.BrainRepository
import com.ronin.ai.network.HealthInfo
import com.ronin.ai.ui.chat.ChatController
import com.ronin.ai.ui.chat.ChatPanel
import com.ronin.ai.ui.components.AiOrb
import com.ronin.ai.ui.components.LinearBar
import com.ronin.ai.ui.components.RingProgress
import com.ronin.ai.ui.components.VyRxIconButton
import com.ronin.ai.ui.components.VyRxTopBar
import com.ronin.ai.ui.components.Waveform
import com.ronin.ai.ui.theme.GlassCard
import com.ronin.ai.ui.theme.NeonDot
import com.ronin.ai.ui.theme.OrbState
import com.ronin.ai.ui.theme.SectionLabel
import com.ronin.ai.ui.theme.VyRxColors
import com.ronin.ai.ui.theme.orbColor
import com.ronin.ai.ui.theme.orbStateFromName
import com.ronin.ai.ui.theme.orbStateLabel
import kotlin.math.roundToLong

@Composable
fun HomeScreen(
    controller: ChatController,
    onOpenDrawer: () -> Unit,
    onNavigate: (String) -> Unit,
    onOrbTap: () -> Unit,
    onOrbLongPress: () -> Unit,
    onVoiceStart: () -> Unit
) {
    val state by BrainRepository.state.collectAsState()
    val logs by BrainRepository.logs.collectAsState()
    val health by BrainRepository.health.collectAsState()
    val summary by BrainRepository.summary.collectAsState()
    val cpuHistory by BrainRepository.cpuHistory.collectAsState()
    val orbState = orbStateFromName(state.state)
    var logExpanded by remember { mutableStateOf(true) }

    Column(Modifier.fillMaxWidth()) {
        VyRxTopBar(
            title = "VYRX",
            subtitle = "A I   A S S I S T A N T",
            onMenu = onOpenDrawer,
            trailing = { VyRxIconButton(Icons.Filled.Tune, onClick = { onNavigate("settings") }) }
        )

        // Hero: Active Model | ORB + AI STATE | Today's Summary
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            ActiveModelCard(
                model = state.model ?: state.provider,
                responseMs = state.responseMs,
                online = state.online,
                Modifier.weight(1f)
            )
            Spacer(Modifier.width(8.dp))
            Column(Modifier.width(148.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                AiOrb(orbState, Modifier.size(112.dp))
                Spacer(Modifier.height(4.dp))
                SectionLabel("AI STATE")
                Text(
                    orbStateLabel(orbState),
                    color = orbColor(orbState),
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold
                )
                Text(
                    if (state.online) state.message else "Brain offline",
                    color = VyRxColors.TextDim,
                    fontSize = 10.sp,
                    maxLines = 1
                )
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                    repeat(6) { i ->
                        NeonDot(orbColor(orbState), size = if (i < 4) 6 else 4)
                    }
                }
            }
            Spacer(Modifier.width(8.dp))
            TodaySummaryCard(
                tasks = summary?.today?.tasksCompleted ?: 0,
                auto = summary?.today?.autoTasks ?: 0,
                learned = summary?.today?.learned ?: 0,
                Modifier.weight(1f)
            )
        }

        // Action log (real-time stream)
        ActionLogPanel(logs = logs, expanded = logExpanded, onToggle = { logExpanded = !logExpanded })

        // Chat (shared panel)
        ChatPanel(controller, onVoiceStart, Modifier.weight(1f))

        // Health card
        HealthCard(health, state, cpuHistory, onNavigate)
    }
}

// ---------------------------------------------------------------------------

@Composable
private fun ActiveModelCard(model: String?, responseMs: Int?, online: Boolean, modifier: Modifier = Modifier) {
    GlassCard(modifier, contentPadding = 12.dp) {
        SectionLabel("ACTIVE MODEL")
        Spacer(Modifier.height(4.dp))
        Text(
            if (!online) "Brain Offline" else model ?: "No provider",
            color = if (online) VyRxColors.PrimaryBright else VyRxColors.Red,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            maxLines = 2
        )
        Spacer(Modifier.height(10.dp))
        Box(Modifier.fillMaxWidth().height(1.dp).background(VyRxColors.CardStroke))
        Spacer(Modifier.height(10.dp))
        SectionLabel("RESPONSE SPEED")
        Spacer(Modifier.height(4.dp))
        Text(
            if (responseMs != null) "${(responseMs / 1000f).roundToLong().coerceAtLeast(1)}s" else "—",
            color = VyRxColors.TextPrimary,
            fontSize = 15.sp,
            fontWeight = FontWeight.Bold
        )
    }
}

@Composable
private fun SummaryRow(icon: androidx.compose.ui.graphics.vector.ImageVector, tint: Color, value: Int, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(26.dp)
                .clip(CircleShape)
                .background(tint.copy(alpha = 0.14f))
                .border(1.dp, tint.copy(alpha = 0.4f), CircleShape),
            contentAlignment = Alignment.Center
        ) {
            Icon(icon, null, tint = tint, modifier = Modifier.size(14.dp))
        }
        Spacer(Modifier.width(8.dp))
        Column {
            Text("$value", color = VyRxColors.TextPrimary, fontSize = 15.sp, fontWeight = FontWeight.Bold)
            Text(label, color = VyRxColors.TextDim, fontSize = 9.sp, maxLines = 1)
        }
    }
}

@Composable
private fun TodaySummaryCard(tasks: Int, auto: Int, learned: Int, modifier: Modifier = Modifier) {
    GlassCard(modifier, contentPadding = 12.dp) {
        SectionLabel("TODAY'S SUMMARY")
        Spacer(Modifier.height(8.dp))
        SummaryRow(Icons.Filled.CheckCircle, VyRxColors.Green, tasks, "Tasks Completed")
        Spacer(Modifier.height(8.dp))
        SummaryRow(Icons.Filled.Bolt, VyRxColors.Blue, auto, "Auto Tasks")
        Spacer(Modifier.height(8.dp))
        SummaryRow(Icons.Filled.Book, VyRxColors.Amber, learned, "Things Learned")
    }
}

@Composable
private fun ActionLogPanel(logs: List<com.ronin.ai.network.ActionLogEntry>, expanded: Boolean, onToggle: () -> Unit) {
    GlassCard(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
        stroke = VyRxColors.LogGreen.copy(alpha = 0.22f),
        contentPadding = 12.dp
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            SectionLabel("ACTION LOG", VyRxColors.LogGreen)
            Box(
                Modifier
                    .clip(CircleShape)
                    .clickable(onClick = onToggle)
                    .padding(6.dp),
                contentAlignment = Alignment.Center
            ) {
                Row(horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                    repeat(3) { Box(Modifier.size(3.dp).clip(CircleShape).background(VyRxColors.LogGreen)) }
                }
            }
        }
        if (expanded) {
            Spacer(Modifier.height(8.dp))
            val visible = logs.takeLast(6).reversed()
            if (visible.isEmpty()) {
                Text("Waiting for activity...", color = VyRxColors.TextFaint, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
            }
            visible.forEach { entry ->
                Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.Top) {
                    Text("[${entry.time}]", color = VyRxColors.LogTime, fontSize = 11.sp, fontFamily = FontFamily.Monospace)
                    Spacer(Modifier.width(6.dp))
                    Icon(
                        when (entry.level) {
                            "success" -> Icons.Filled.CheckCircle
                            "error" -> Icons.Filled.Warning
                            "tool" -> Icons.Filled.Bolt
                            else -> Icons.Filled.Info
                        },
                        null,
                        tint = when (entry.level) {
                            "error" -> VyRxColors.Red
                            "success" -> VyRxColors.LogGreen
                            "tool" -> VyRxColors.Blue
                            else -> VyRxColors.LogGreen
                        },
                        modifier = Modifier.size(12.dp).padding(top = 1.dp)
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(entry.text, color = VyRxColors.LogGreen, fontSize = 11.sp, fontFamily = FontFamily.Monospace, maxLines = 2)
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------

@Composable
private fun HealthTile(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(
        modifier
            .clip(RoundedCornerShape(14.dp))
            .background(Color(0xFF0B0F17))
            .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(14.dp))
            .padding(10.dp),
        contentAlignment = Alignment.Center
    ) {
        content()
    }
}

@Composable
private fun HealthCard(health: HealthInfo?, state: com.ronin.ai.network.BrainState, cpuHistory: List<Float>, onNavigate: (String) -> Unit) {
    GlassCard(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
        contentPadding = 12.dp
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
            SectionLabel("VYRX HEALTH", VyRxColors.PrimaryBright)
            Text(
                "VIEW ALL",
                color = VyRxColors.Blue,
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 0.8.sp,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .clickable { onNavigate("dashboard") }
                    .padding(4.dp)
            )
        }
        Spacer(Modifier.height(10.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            HealthTile(Modifier.weight(1f)) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    SectionLabel("Memory", VyRxColors.TextDim)
                    Spacer(Modifier.height(6.dp))
                    RingProgress(
                        (health?.memoryPercent ?: 0f) / 100f,
                        VyRxColors.Green,
                        Modifier.size(46.dp),
                        label = "RAM"
                    )
                }
            }
            HealthTile(Modifier.weight(1f)) {
                Column {
                    SectionLabel("Storage")
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "${fmt1(health?.storageUsedGb ?: 0f)} GB",
                        color = VyRxColors.TextPrimary,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Text("/ ${fmt1(health?.storageTotalGb ?: 0f)} GB", color = VyRxColors.TextFaint, fontSize = 10.sp)
                    Spacer(Modifier.height(6.dp))
                    LinearBar(
                        value = if ((health?.storageTotalGb ?: 0f) > 0) (health?.storageUsedGb ?: 0f) / (health?.storageTotalGb ?: 1f) else 0f,
                        color = VyRxColors.Green,
                        Modifier.fillMaxWidth()
                    )
                }
            }
            HealthTile(Modifier.weight(1f)) {
                Column {
                    SectionLabel("CPU Load")
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "${(health?.cpuPercent ?: 0f).toInt()}%",
                        color = VyRxColors.TextPrimary,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.Bold
                    )
                    Spacer(Modifier.height(4.dp))
                    Waveform(cpuHistory, VyRxColors.Green, Modifier.fillMaxWidth().height(26.dp))
                }
            }
            HealthTile(Modifier.weight(1f)) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    SectionLabel("API Status")
                    Spacer(Modifier.height(6.dp))
                    Text(
                        (state.provider ?: "No API").replaceFirstChar { it.uppercase() } + " API",
                        color = VyRxColors.TextPrimary,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1
                    )
                    Spacer(Modifier.height(4.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        NeonDot(if (state.online && state.provider != null) VyRxColors.Green else VyRxColors.Red, size = 7)
                        Spacer(Modifier.width(4.dp))
                        Text(
                            if (state.online && state.provider != null) "Active" else "Offline",
                            color = if (state.online && state.provider != null) VyRxColors.Green else VyRxColors.Red,
                            fontSize = 10.sp
                        )
                    }
                }
            }
        }
    }
}

private fun fmt1(v: Float): String = "${"%.1f".format(v)}"
