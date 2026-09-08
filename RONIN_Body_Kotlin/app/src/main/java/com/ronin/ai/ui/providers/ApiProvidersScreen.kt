package com.ronin.ai.ui.providers

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
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Api
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SwapHoriz
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.data.ProviderRepository
import com.ronin.ai.network.ApiClient
import com.ronin.ai.network.ProviderConfig
import com.ronin.ai.network.ProviderEngine
import com.ronin.ai.network.ProviderInfo
import com.ronin.ai.network.ProviderType
import com.ronin.ai.ui.components.VyRxIconButton
import com.ronin.ai.ui.components.VyRxTopBar
import com.ronin.ai.ui.theme.GlassCard
import com.ronin.ai.ui.theme.NeonDot
import com.ronin.ai.ui.theme.SectionLabel
import com.ronin.ai.ui.theme.VyRxColors
import kotlinx.coroutines.launch
import java.util.UUID

private data class ProviderSpec(
    val name: String,
    val title: String,
    val subtitle: String,
    val icon: ImageVector,
    val tint: Color,
    val type: ProviderType
)

private val SPECS = listOf(
    ProviderSpec("groq", "Groq", "Ultra fast • High performance • 8K context", Icons.Filled.Bolt, Color(0xFFEF4444), ProviderType.GROQ),
    ProviderSpec("gemini", "Gemini", "Multimodal • Long context • 1M context", Icons.Filled.Star, VyRxColors.Blue, ProviderType.GEMINI),
    ProviderSpec("openai", "OpenAI", "Multimodal • Creative • 128K context", Icons.Filled.Api, VyRxColors.Green, ProviderType.OPENAI),
    ProviderSpec("custom", "Custom API", "Any model • Any service • Full control", Icons.Filled.Code, VyRxColors.Blue, ProviderType.CUSTOM)
)

@Composable
fun ApiProvidersScreen(
    settingsRepo: com.ronin.ai.data.AppSettingsRepository,
    onOpenDrawer: () -> Unit,
    onNavigate: (String) -> Unit,
    onToast: (String) -> Unit
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    val scope = rememberCoroutineScope()
    val repository = remember(context) { ProviderRepository(context) }

    var engine by remember { mutableStateOf<ProviderEngine?>(null) }
    var providers by remember { mutableStateOf<List<ProviderInfo>?>(null) }
    var addFor by remember { mutableStateOf<ProviderSpec?>(null) }
    var testing by remember { mutableStateOf<String?>(null) }
    var testResult by remember { mutableStateOf<Pair<String, String>?>(null) }

    suspend fun refresh() {
        try {
            val result = ApiClient.providers()
            engine = result.first
            providers = result.second
        } catch (e: Exception) {
            engine = null
            providers = null
        }
    }

    LaunchedEffect(Unit) { refresh() }

    fun updateProviderEnabled(spec: ProviderSpec, enabled: Boolean) {
        scope.launch {
            try {
                ApiClient.updateProvider(spec.name, enabled = enabled)
                refresh()
                onToast("${spec.title} ${if (enabled) "enabled" else "disabled"}")
            } catch (e: Exception) {
                onToast("Brain offline — change saved locally only")
            }
        }
    }

    fun saveKey(spec: ProviderSpec, key: String, model: String, endpoint: String) {
        val existing = repository.load().firstOrNull { it.type == spec.type }
        val config = ProviderConfig(
            id = existing?.id ?: UUID.randomUUID().toString(),
            type = spec.type,
            name = spec.title,
            apiKey = key,
            endpoint = endpoint.ifBlank { null },
            model = model.ifBlank { null },
            enabled = true
        )
        val updated = (repository.load().filterNot { it.type == spec.type } + config)
        repository.save(updated)
        scope.launch {
            try {
                ApiClient.updateProvider(spec.name, enabled = true, model = model.ifBlank { null })
                refresh()
                onToast("${spec.title} API key saved (encrypted) ✓")
            } catch (e: Exception) {
                onToast("Key saved locally; Brain offline for sync")
            }
        }
    }

    fun testConnection(spec: ProviderSpec) {
        val config = repository.load().firstOrNull { it.type == spec.type }
        if (config == null) {
            onToast("Add an API key first to test ${spec.title}.")
            return
        }
        testing = spec.name
        scope.launch {
            val result = ApiClient.testProvider(config)
            testing = null
            result.fold(
                onSuccess = { ms -> testResult = spec.title to "Connected in ${ms} ms ✓" },
                onFailure = { e -> testResult = spec.title to "Failed: ${e.message}" }
            )
        }
    }

    Column(Modifier.fillMaxSize()) {
        VyRxTopBar(
            title = "API PROVIDERS",
            subtitle = "Manage AI Models & API Connections",
            onMenu = onOpenDrawer,
            trailing = {
                Row {
                    VyRxIconButton(Icons.Filled.Search, onClick = { onToast("Search: use the provider list below.") })
                    VyRxIconButton(Icons.Filled.Tune, onClick = {})
                }
            }
        )

        LazyColumn(
            Modifier.fillMaxWidth().weight(1f),
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 4.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            // AI Engine Status banner
            item {
                GlassCard(Modifier.fillMaxWidth(), stroke = VyRxColors.Primary.copy(alpha = 0.45f), glow = VyRxColors.Primary) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            Modifier
                                .size(48.dp)
                                .clip(RoundedCornerShape(14.dp))
                                .background(
                                    Brush.linearGradient(listOf(VyRxColors.Primary.copy(alpha = 0.35f), VyRxColors.Blue.copy(alpha = 0.25f)))
                                )
                                .border(1.dp, VyRxColors.Primary.copy(alpha = 0.5f), RoundedCornerShape(14.dp)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(Icons.Filled.Cloud, null, tint = Color.White, modifier = Modifier.size(24.dp))
                        }
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("AI Engine Status", color = VyRxColors.TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                            Text("Hybrid Multi-Provider • Auto Failover Enabled", color = VyRxColors.TextDim, fontSize = 10.sp)
                            Text(
                                "VYRX will automatically switch to the best available provider.",
                                color = VyRxColors.TextFaint,
                                fontSize = 9.sp
                            )
                        }
                        Spacer(Modifier.width(8.dp))
                        Box(
                            Modifier
                                .clip(RoundedCornerShape(10.dp))
                                .background((if (engine?.online == true) VyRxColors.Green else VyRxColors.Red).copy(alpha = 0.14f))
                                .border(1.dp, (if (engine?.online == true) VyRxColors.Green else VyRxColors.Red).copy(alpha = 0.45f), RoundedCornerShape(10.dp))
                                .padding(horizontal = 10.dp, vertical = 5.dp)
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                NeonDot((if (engine?.online == true) VyRxColors.Green else VyRxColors.Red), size = 7)
                                Spacer(Modifier.width(5.dp))
                                Text(
                                    if (engine?.online == true) "Online" else "Offline",
                                    color = if (engine?.online == true) VyRxColors.Green else VyRxColors.Red,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold
                                )
                            }
                        }
                    }
                }
            }

            // Provider cards
            SPECS.forEach { spec ->
                item(key = "provider_${spec.name}") {
                    val info = providers?.firstOrNull { it.name == spec.name }
                    val configured = repository.load().any { it.type == spec.type } || info?.configured == true
                    val active = info?.active == true
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(18.dp))
                            .background(Color(0xFF0D1220))
                            .border(1.dp, if (active) VyRxColors.Green.copy(alpha = 0.4f) else VyRxColors.CardStrokeSoft, RoundedCornerShape(18.dp))
                            .padding(14.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            Modifier
                                .size(46.dp)
                                .clip(RoundedCornerShape(14.dp))
                                .background(spec.tint.copy(alpha = 0.16f))
                                .border(1.dp, spec.tint.copy(alpha = 0.45f), RoundedCornerShape(14.dp)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(spec.icon, null, tint = spec.tint, modifier = Modifier.size(22.dp))
                        }
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text(spec.title, color = VyRxColors.TextPrimary, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                                Box(
                                    Modifier
                                        .clip(RoundedCornerShape(8.dp))
                                        .background(
                                            when {
                                                active -> VyRxColors.Green.copy(alpha = 0.14f)
                                                configured -> VyRxColors.Blue.copy(alpha = 0.14f)
                                                else -> Color(0xFF151A26)
                                            }
                                        )
                                        .padding(horizontal = 8.dp, vertical = 3.dp)
                                ) {
                                    Text(
                                        when {
                                            active -> "Active"
                                            configured -> "Configured"
                                            else -> "Not Configured"
                                        },
                                        color = when {
                                            active -> VyRxColors.Green
                                            configured -> VyRxColors.Blue
                                            else -> VyRxColors.TextDim
                                        },
                                        fontSize = 9.sp,
                                        fontWeight = FontWeight.SemiBold
                                    )
                                }
                            }
                            Spacer(Modifier.height(4.dp))
                            Text(
                                (info?.model ?: defaultModel(spec)).ifBlank { spec.subtitle.split("•")[0].trim() },
                                color = VyRxColors.PrimaryBright,
                                fontSize = 11.sp
                            )
                            Text(spec.subtitle, color = VyRxColors.TextDim, fontSize = 9.5.sp, maxLines = 1)
                            if (configured) {
                                Spacer(Modifier.height(8.dp))
                                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                    MiniStat(Icons.Filled.Bolt, VyRxColors.Blue, "Latency", info?.latencyMs?.let { "${it}ms" } ?: "—")
                                    MiniStat(Icons.Filled.Api, VyRxColors.Green, "Usage", "${info?.requestsToday ?: 0} today")
                                    MiniStat(Icons.Filled.Star, VyRxColors.Green, "Success", info?.successRate?.let { "${it}%" } ?: "—")
                                }
                            }
                        }
                        Spacer(Modifier.width(8.dp))
                        Column(
                            horizontalAlignment = Alignment.End,
                            verticalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Switch(
                                checked = configured && (info?.enabled ?: true),
                                onCheckedChange = { enabled ->
                                    if (!configured && enabled) {
                                        addFor = spec
                                    } else {
                                        updateProviderEnabled(spec, enabled)
                                    }
                                },
                                colors = SwitchDefaults.colors(
                                    checkedTrackColor = VyRxColors.Primary,
                                    checkedThumbColor = Color.White,
                                    uncheckedTrackColor = Color(0xFF232B3D),
                                    uncheckedThumbColor = Color(0xFF8A93A6)
                                )
                            )
                            if (configured) {
                                OutlinedButton(
                                    onClick = { testConnection(spec) },
                                    enabled = testing != spec.name,
                                    colors = ButtonDefaults.outlinedButtonColors(contentColor = VyRxColors.PrimaryBright),
                                    border = androidx.compose.foundation.BorderStroke(1.dp, VyRxColors.Primary.copy(alpha = 0.5f)),
                                    contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 10.dp, vertical = 4.dp)
                                ) {
                                    if (testing == spec.name) {
                                        CircularProgressIndicator(Modifier.size(12.dp), color = VyRxColors.PrimaryBright, strokeWidth = 1.5.dp)
                                    } else {
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            Icon(Icons.Filled.Key, null, modifier = Modifier.size(12.dp))
                                            Spacer(Modifier.width(5.dp))
                                            Text("Test", fontSize = 10.sp)
                                        }
                                    }
                                }
                            } else {
                                OutlinedButton(
                                    onClick = { addFor = spec },
                                    colors = ButtonDefaults.outlinedButtonColors(contentColor = VyRxColors.PrimaryBright),
                                    border = androidx.compose.foundation.BorderStroke(1.dp, VyRxColors.Primary.copy(alpha = 0.5f)),
                                    contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 10.dp, vertical = 4.dp)
                                ) {
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        Icon(Icons.Filled.Key, null, modifier = Modifier.size(12.dp))
                                        Spacer(Modifier.width(5.dp))
                                        Text(if (spec.type == ProviderType.CUSTOM) "Add API Endpoint" else "Add API Key", fontSize = 10.sp)
                                    }
                                }
                            }
                            Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = VyRxColors.TextFaint, modifier = Modifier.size(16.dp))
                        }
                    }
                }
            }

            // Manage API Keys card
            item {
                GlassCard(Modifier.fillMaxWidth()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            Modifier
                                .size(40.dp)
                                .clip(RoundedCornerShape(12.dp))
                                .background(VyRxColors.Primary.copy(alpha = 0.15f)),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(Icons.Filled.Key, null, tint = VyRxColors.PrimaryBright, modifier = Modifier.size(20.dp))
                        }
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("Manage API Keys", color = VyRxColors.TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold)
                            Text("View, add or remove your API keys securely.", color = VyRxColors.TextDim, fontSize = 10.sp)
                        }
                        Button(
                            onClick = { onNavigate("provider_manager") },
                            colors = ButtonDefaults.buttonColors(containerColor = VyRxColors.Primary, contentColor = Color.White)
                        ) {
                            Text("Manage Keys →", fontSize = 12.sp)
                        }
                    }
                }
            }

            // Security feature tiles
            item {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    SecurityTile(Modifier.weight(1f), Icons.Filled.Lock, "Secure Storage", "Keys encrypted with Android Keystore")
                    SecurityTile(Modifier.weight(1f), Icons.Filled.SwapHoriz, "Auto Failover", "Switches to available provider automatically")
                    SecurityTile(Modifier.weight(1f), Icons.Filled.Api, "Usage Monitoring", "Track usage, latency and success rate")
                    SecurityTile(Modifier.weight(1f), Icons.Filled.Cloud, "Privacy First", "Your keys never leave your device")
                }
            }
        }
    }

    // Add key dialog
    addFor?.let { spec ->
        AddKeyDialog(
            spec = spec,
            onDismiss = { addFor = null },
            onSave = { key, model, endpoint ->
                addFor = null
                saveKey(spec, key, model, endpoint)
            }
        )
    }

    // Test result dialog
    testResult?.let { (title, message) ->
        AlertDialog(
            onDismissRequest = { testResult = null },
            title = { Text("Connection Test — $title", fontWeight = FontWeight.Bold) },
            text = {
                Text(message, color = if (message.endsWith("✓")) VyRxColors.Green else VyRxColors.Red, fontSize = 13.sp)
            },
            confirmButton = { TextButton(onClick = { testResult = null }) { Text("Close") } }
        )
    }
}

private fun defaultModel(spec: ProviderSpec): String = when (spec.type) {
    ProviderType.GROQ -> "Mixtral 8x7B (Recommended)"
    ProviderType.GEMINI -> "Gemini 1.5 Pro"
    ProviderType.OPENAI -> "GPT-4o"
    ProviderType.OPENROUTER -> "OpenRouter"
    ProviderType.CUSTOM -> "Your Own Endpoint"
}

@Composable
private fun MiniStat(icon: ImageVector, tint: Color, label: String, value: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, tint = tint, modifier = Modifier.size(12.dp))
        Spacer(Modifier.width(4.dp))
        Text("$label $value", color = VyRxColors.TextDim, fontSize = 9.sp, maxLines = 1)
    }
}

@Composable
private fun SecurityTile(modifier: Modifier, icon: ImageVector, title: String, subtitle: String) {
    Box(
        modifier
            .clip(RoundedCornerShape(16.dp))
            .background(Color(0xFF0D1220))
            .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(16.dp))
            .padding(12.dp)
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(icon, null, tint = VyRxColors.Blue, modifier = Modifier.size(18.dp))
            Spacer(Modifier.height(6.dp))
            Text(title, color = VyRxColors.TextPrimary, fontSize = 10.sp, fontWeight = FontWeight.SemiBold, textAlign = TextAlign.Center)
            Spacer(Modifier.height(4.dp))
            Text(subtitle, color = VyRxColors.TextDim, fontSize = 8.sp, textAlign = TextAlign.Center)
        }
    }
}

@Composable
private fun AddKeyDialog(spec: ProviderSpec, onDismiss: () -> Unit, onSave: (String, String, String) -> Unit) {
    var key by remember { mutableStateOf("") }
    var model by remember { mutableStateOf("") }
    var endpoint by remember { mutableStateOf("") }
    val isCustom = spec.type == ProviderType.CUSTOM
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("${spec.title} Setup", fontWeight = FontWeight.Bold) },
        text = {
            Column(Modifier.fillMaxWidth()) {
                if (isCustom) {
                    SectionLabel("ENDPOINT URL")
                    Spacer(Modifier.height(4.dp))
                    BasicTextField(
                        value = endpoint,
                        onValueChange = { endpoint = it },
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .background(Color(0xFF0C1018))
                            .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(10.dp))
                            .padding(10.dp),
                        textStyle = androidx.compose.ui.text.TextStyle(color = VyRxColors.TextPrimary, fontSize = 13.sp),
                        cursorBrush = SolidColor(VyRxColors.PrimaryBright),
                        singleLine = true
                    )
                    Spacer(Modifier.height(12.dp))
                    SectionLabel("MODEL NAME")
                    Spacer(Modifier.height(4.dp))
                    BasicTextField(
                        value = model,
                        onValueChange = { model = it },
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .background(Color(0xFF0C1018))
                            .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(10.dp))
                            .padding(10.dp),
                        textStyle = androidx.compose.ui.text.TextStyle(color = VyRxColors.TextPrimary, fontSize = 13.sp),
                        cursorBrush = SolidColor(VyRxColors.PrimaryBright),
                        singleLine = true
                    )
                    Spacer(Modifier.height(12.dp))
                } else {
                    SectionLabel("MODEL (OPTIONAL)")
                    Spacer(Modifier.height(4.dp))
                    BasicTextField(
                        value = model,
                        onValueChange = { model = it },
                        modifier = Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .background(Color(0xFF0C1018))
                            .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(10.dp))
                            .padding(10.dp),
                        textStyle = androidx.compose.ui.text.TextStyle(color = VyRxColors.TextPrimary, fontSize = 13.sp),
                        cursorBrush = SolidColor(VyRxColors.PrimaryBright),
                        singleLine = true
                    )
                    Spacer(Modifier.height(12.dp))
                }
                SectionLabel("API KEY")
                Spacer(Modifier.height(4.dp))
                BasicTextField(
                    value = key,
                    onValueChange = { key = it },
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .background(Color(0xFF0C1018))
                        .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(10.dp))
                        .padding(10.dp),
                    textStyle = androidx.compose.ui.text.TextStyle(color = VyRxColors.TextPrimary, fontSize = 13.sp),
                    cursorBrush = SolidColor(VyRxColors.PrimaryBright),
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation()
                )
                Spacer(Modifier.height(10.dp))
                Text(
                    "🔐 Stored encrypted in Android Keystore (EncryptedSharedPreferences). Never in plain text.",
                    color = VyRxColors.TextFaint,
                    fontSize = 10.sp
                )
            }
        },
        confirmButton = {
            Button(
                enabled = key.isNotBlank() && (!isCustom || endpoint.isNotBlank()),
                onClick = { onSave(key, model, endpoint) },
                colors = ButtonDefaults.buttonColors(containerColor = VyRxColors.Primary)
            ) { Text("Save") }
        },
        dismissButton = { OutlinedButton(onClick = onDismiss) { Text("Cancel") } }
    )
}
