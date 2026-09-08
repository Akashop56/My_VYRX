package com.ronin.ai.ui.tools

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.Settings
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
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Apps
import androidx.compose.material.icons.filled.AutoStories
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Insights
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Public
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Smartphone
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.Icon
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.ronin.ai.data.AppSettings
import com.ronin.ai.data.AppSettingsRepository
import com.ronin.ai.data.BrainRepository
import com.ronin.ai.network.ApiClient
import com.ronin.ai.network.ToolInfo
import com.ronin.ai.network.ToolsUsage
import com.ronin.ai.services.RoninAccessibilityService
import com.ronin.ai.services.RoninNotificationListener
import com.ronin.ai.ui.components.HeartbeatOrb
import com.ronin.ai.ui.components.VyRxIconButton
import com.ronin.ai.ui.components.VyRxTopBar
import com.ronin.ai.ui.theme.GlassCard
import com.ronin.ai.ui.theme.NeonDot
import com.ronin.ai.ui.theme.SectionLabel
import com.ronin.ai.ui.theme.VyRxColors
import com.ronin.ai.ui.theme.orbStateFromName
import kotlinx.coroutines.launch

private val TABS = listOf("all" to "All Tools", "device" to "Device", "internet" to "Internet", "automation" to "Automation", "development" to "Development")

private fun toolIcon(id: String): ImageVector = when (id) {
    "app_control" -> Icons.Filled.Apps
    "web_search" -> Icons.Filled.Public
    "code_executor" -> Icons.Filled.Code
    "file_manager" -> Icons.Filled.Folder
    "task_automation" -> Icons.Filled.Schedule
    "notification_manager" -> Icons.Filled.Notifications
    "telegram" -> Icons.AutoMirrored.Filled.KeyboardArrowRight
    "email" -> Icons.Filled.Email
    "system_monitor" -> Icons.Filled.Insights
    "custom_api" -> Icons.Filled.Cloud
    "note_creator" -> Icons.Filled.Description
    "image_analyzer" -> Icons.Filled.Search
    else -> Icons.Filled.Bolt
}

private fun toolTint(id: String): Color = when (id) {
    "app_control" -> VyRxColors.PrimaryBright
    "web_search" -> VyRxColors.Blue
    "code_executor" -> VyRxColors.Green
    "file_manager" -> VyRxColors.Amber
    "task_automation" -> Color(0xFFF472B6)
    "notification_manager" -> VyRxColors.Blue
    "telegram" -> VyRxColors.Blue
    "email" -> VyRxColors.PrimaryBright
    "system_monitor" -> VyRxColors.Green
    "custom_api" -> VyRxColors.Amber
    "note_creator" -> VyRxColors.Amber
    "image_analyzer" -> VyRxColors.PrimaryBright
    else -> VyRxColors.TextDim
}

data class ToolSection(val title: String, val subtitle: String, val ids: List<String>)

private val SECTIONS = listOf(
    ToolSection("CORE TOOLS", "Essential tools for daily tasks",
        listOf("app_control", "web_search", "code_executor", "file_manager", "task_automation", "notification_manager", "note_creator")),
    ToolSection("COMMUNICATION TOOLS", "Stay connected everywhere", listOf("telegram", "email")),
    ToolSection("ADVANCED TOOLS", "Power features for complex tasks",
        listOf("system_monitor", "custom_api", "image_analyzer"))
)

@Composable
fun ToolsScreen(
    settingsRepo: AppSettingsRepository,
    onOpenDrawer: () -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val settings by settingsRepo.settings.collectAsState()
    val state by BrainRepository.state.collectAsState()

    var tab by remember { mutableStateOf("all") }
    var query by remember { mutableStateOf("") }
    var tools by remember { mutableStateOf<List<ToolInfo>?>(null) }
    var usage by remember { mutableStateOf<ToolsUsage?>(null) }
    var brainOnline by remember { mutableStateOf(false) }

    suspend fun refreshTools() {
        try {
            val result = ApiClient.tools()
            tools = result.first
            usage = result.second
            brainOnline = true
        } catch (e: Exception) {
            brainOnline = false
        }
    }

    fun reportDeviceStatus() {
        val accessibility = RoninAccessibilityService.instance != null
        val notifications = isNotificationListenerEnabled(context)
        val microphone = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        val version = try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "1.0"
        } catch (_: Exception) { "1.0" }
        scope.launch { ApiClient.reportDeviceStatus(accessibility, notifications, microphone, version) }
    }

    LaunchedEffect(Unit) {
        reportDeviceStatus()
        refreshTools()
    }
    LaunchedEffect(state.online) {
        if (state.online) refreshTools()
    }

    val byId: Map<String, ToolInfo> = tools.orEmpty().associateBy { it.id }

    Column(Modifier.fillMaxSize()) {
        VyRxTopBar(
            title = "TOOLS",
            subtitle = "AI Agent Tool Kit",
            onMenu = onOpenDrawer,
            trailing = {
                Row {
                    VyRxIconButton(Icons.Filled.Search, onClick = { query = if (query.isEmpty()) " " else "" }) // " " opens the search field
                    VyRxIconButton(Icons.Filled.Tune, onClick = {})
                }
            }
        )
        Text(
            "Give VYRX the right tools, and it can do almost anything on your device.",
            color = VyRxColors.TextDim,
            fontSize = 11.sp,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)
        )

        // Tabs
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(Color(0xFF0B0F17))
                .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(16.dp))
                .padding(4.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp)
        ) {
            TABS.forEach { (value, label) ->
                val active = tab == value
                Box(
                    Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(12.dp))
                        .background(if (active) VyRxColors.Primary.copy(alpha = 0.22f) else Color.Transparent)
                        .border(1.dp, if (active) VyRxColors.Primary.copy(alpha = 0.55f) else Color.Transparent, RoundedCornerShape(12.dp))
                        .clickable { tab = value }
                        .padding(vertical = 8.dp),
                    contentAlignment = Alignment.Center
                ) {
                    Text(label, color = if (active) VyRxColors.PrimaryBright else VyRxColors.TextDim, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }

        LazyColumn(
            Modifier.fillMaxWidth().weight(1f),
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 4.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            // Status card
            item {
                GlassCard(Modifier.fillMaxWidth()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        HeartbeatOrb(orbStateFromName(if (brainOnline) "executing" else "idle"), Modifier.size(52.dp), sizeDp = 52)
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("Tools Status", color = VyRxColors.TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                            Text(
                                if (brainOnline) "${tools?.count { it.active } ?: 0} / ${tools?.size ?: 12} Tools Available" else "Brain offline",
                                color = VyRxColors.TextDim,
                                fontSize = 11.sp
                            )
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                NeonDot(if (brainOnline) VyRxColors.Green else VyRxColors.Red, size = 7)
                                Spacer(Modifier.width(5.dp))
                                Text(
                                    if (brainOnline) "All Systems Online" else "Systems Offline",
                                    color = if (brainOnline) VyRxColors.Green else VyRxColors.Red,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold
                                )
                            }
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            SectionLabel("Tool Usage (Today)", VyRxColors.TextDim)
                            Spacer(Modifier.height(6.dp))
                            Text("${usage?.toolsUsed ?: 0} tools used", color = VyRxColors.TextPrimary, fontSize = 11.sp)
                            Text("${usage?.successRate ?: 0}% success rate", color = VyRxColors.Green, fontSize = 11.sp)
                        }
                    }
                }
            }

            if (query.isNotEmpty()) {
                item {
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(14.dp))
                            .background(Color(0xFF0C1018))
                            .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(14.dp))
                            .padding(start = 16.dp, end = 12.dp),
                        contentAlignment = Alignment.CenterStart
                    ) {
                        androidx.compose.foundation.text.BasicTextField(
                            value = query,
                            onValueChange = { query = it },
                            modifier = Modifier.fillMaxWidth().padding(vertical = 12.dp),
                            textStyle = androidx.compose.ui.text.TextStyle(color = VyRxColors.TextPrimary, fontSize = 13.sp),
                            cursorBrush = androidx.compose.ui.graphics.SolidColor(VyRxColors.PrimaryBright),
                            singleLine = true,
                            decorationBox = { inner ->
                                Box {
                                    if (query.isEmpty()) {
                                        Text("Search tools...", color = VyRxColors.TextFaint, fontSize = 13.sp)
                                    }
                                    inner()
                                }
                            }
                        )
                    }
                }
            }

            SECTIONS.forEach { section ->
                val visible = section.ids
                    .filter { tab == "all" || byId[it]?.category == tab }
                    .filter {
                        val t = byId[it]
                        query.isBlank() ||
                            t?.name?.contains(query, true) == true ||
                            t?.description?.contains(query, true) == true || it.contains(query, true)
                    }
                if (visible.isEmpty()) return@forEach
                item(key = "header_${section.title}") {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(
                                when (section.title) {
                                    "CORE TOOLS" -> Icons.Filled.Apps
                                    "COMMUNICATION TOOLS" -> Icons.Filled.Public
                                    else -> Icons.Filled.Tune
                                },
                                null,
                                tint = VyRxColors.PrimaryBright,
                                modifier = Modifier.size(15.dp)
                            )
                            Spacer(Modifier.width(6.dp))
                            SectionLabel(section.title, VyRxColors.TextPrimary)
                        }
                        Text(section.subtitle, color = VyRxColors.TextFaint, fontSize = 10.sp)
                    }
                }
                visible.chunked(2).forEach { row ->
                    item(key = "row_${row.joinToString(",")}"){
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                            row.forEach { id ->
                                val tool = byId[id] ?: return@forEach
                                ToolCard(
                                    tool = tool,
                                    enabled = settings.toolsEnabled[id] ?: true,
                                    onToggle = { enabled ->
                                        settingsRepo.setToolEnabled(id, enabled)
                                        scope.launch { refreshTools() }
                                    },
                                    modifier = Modifier.weight(1f)
                                )
                            }
                            if (row.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun ToolCard(tool: ToolInfo, enabled: Boolean, onToggle: (Boolean) -> Unit, modifier: Modifier = Modifier) {
    val tint = toolTint(tool.id)
    val active = tool.active && enabled
    Row(
        modifier
            .clip(RoundedCornerShape(18.dp))
            .background(Color(0xFF0D1220))
            .border(1.dp, if (active) tint.copy(alpha = 0.35f) else VyRxColors.CardStrokeSoft, RoundedCornerShape(18.dp))
            .clickable { /* detail: toggled below; reserved for tool settings */ }
            .padding(14.dp),
        verticalAlignment = Alignment.Top
    ) {
        Box(
            Modifier
                .size(42.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(tint.copy(alpha = 0.15f))
                .border(1.dp, tint.copy(alpha = 0.4f), RoundedCornerShape(12.dp)),
            contentAlignment = Alignment.Center
        ) {
            Icon(toolIcon(tool.id), null, tint = tint, modifier = Modifier.size(20.dp))
        }
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(tool.name, color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
                Spacer(Modifier.weight(1f))
                Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = VyRxColors.TextFaint, modifier = Modifier.size(16.dp))
            }
            Spacer(Modifier.height(3.dp))
            Text(tool.description, color = VyRxColors.TextDim, fontSize = 10.sp, maxLines = 2)
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .clip(RoundedCornerShape(8.dp))
                        .background(if (active) VyRxColors.Green.copy(alpha = 0.12f) else Color(0xFF151A26))
                        .padding(horizontal = 8.dp, vertical = 3.dp)
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        NeonDot(if (active) VyRxColors.Green else VyRxColors.TextFaint, size = 6)
                        Spacer(Modifier.width(4.dp))
                        Text(if (active) "Active" else if (tool.active) "Disabled" else "Inactive", color = if (active) VyRxColors.Green else VyRxColors.TextDim, fontSize = 10.sp)
                    }
                }
                Spacer(Modifier.weight(1f))
                Switch(
                    checked = enabled,
                    onCheckedChange = onToggle,
                    colors = SwitchDefaults.colors(
                        checkedTrackColor = VyRxColors.Primary,
                        checkedThumbColor = Color.White,
                        uncheckedTrackColor = Color(0xFF232B3D),
                        uncheckedThumbColor = Color(0xFF8A93A6)
                    )
                )
            }
        }
    }
}

private fun isNotificationListenerEnabled(context: Context): Boolean {
    return try {
        val enabled = Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners") ?: ""
        enabled.contains(RoninNotificationListener::class.java.name.substringAfterLast("."))
    } catch (_: Exception) {
        false
    }
}
