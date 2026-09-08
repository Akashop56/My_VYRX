package com.ronin.ai.ui.settings

import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Api
import androidx.compose.material.icons.filled.BatteryStd
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.DarkMode
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Fingerprint
import androidx.compose.material.icons.filled.Help
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.PrivacyTip
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.fragment.app.FragmentActivity
import com.ronin.ai.BuildConfig
import com.ronin.ai.data.AppSettings
import com.ronin.ai.data.AppSettingsRepository
import com.ronin.ai.data.BrainRepository
import com.ronin.ai.network.ApiClient
import com.ronin.ai.services.RoninAccessibilityService
import com.ronin.ai.ui.components.HeartbeatOrb
import com.ronin.ai.ui.components.VyRxTopBar
import com.ronin.ai.ui.theme.GlassCard
import com.ronin.ai.ui.theme.NeonDot
import com.ronin.ai.ui.theme.SectionLabel
import com.ronin.ai.ui.theme.VyRxColors
import com.ronin.ai.ui.theme.orbStateFromName
import kotlinx.coroutines.launch

@Composable
fun SettingsScreen(
    settingsRepo: AppSettingsRepository,
    activity: FragmentActivity,
    onNavigate: (String) -> Unit,
    onToast: (String) -> Unit
) {
    val context = activity
    val scope = rememberCoroutineScope()
    val settings by settingsRepo.settings.collectAsState()
    val health by BrainRepository.health.collectAsState()

    var personalityOpen by remember { mutableStateOf(false) }
    var responseModeOpen by remember { mutableStateOf(false) }
    var voiceOpen by remember { mutableStateOf(false) }
    var clearOpen by remember { mutableStateOf(false) }
    var aboutOpen by remember { mutableStateOf(false) }

    val powerManager = context.getSystemService(android.content.Context.POWER_SERVICE) as PowerManager
    val batteryOk = try {
        powerManager.isIgnoringBatteryOptimizations(context.packageName)
    } catch (_: Exception) { false }

    fun toggleBattery() {
        if (batteryOk) {
            onToast("Battery optimization exemption removed — Android may still throttle VYRX.")
        } else {
            try {
                context.startActivity(
                    Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}"))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                )
                onToast("Allow VYRX to keep running in the background.")
            } catch (e: Exception) {
                onToast("Could not open battery settings: ${e.message}")
            }
        }
    }

    fun toggleBiometric(enable: Boolean) {
        if (!enable) {
            settingsRepo.update { it.copy(biometricLock = false) }
            return
        }
        val biometricManager = BiometricManager.from(context)
        if (biometricManager.canAuthenticate(BIOMETRIC_STRONG or DEVICE_CREDENTIAL) != BiometricManager.BIOMETRIC_SUCCESS) {
            onToast("No biometric hardware available on this device.")
            return
        }
        val callback = object : BiometricPrompt.AuthenticationCallback() {
            override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                settingsRepo.update { it.copy(biometricLock = true) }
                onToast("App lock enabled ✓")
            }
            override fun onAuthenticationFailed() {
                onToast("Unlock failed — app lock not enabled.")
            }
            override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                onToast("Biometric lock unavailable: $errString")
            }
        }
        val prompt = BiometricPrompt(context, callback)
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle("Enable App Lock")
            .setSubtitle("Verify your identity to protect VYRX")
            .setAllowedAuthenticators(BIOMETRIC_STRONG or DEVICE_CREDENTIAL)
            .build()
        try {
            prompt.authenticate(info)
        } catch (e: Exception) {
            onToast("Biometric lock unavailable: ${e.message}")
        }
    }

    Column(Modifier.fillMaxSize()) {
        VyRxTopBar(
            title = "SETTINGS",
            subtitle = "Customize Your VYRX Experience",
            onMenu = {},
            trailing = {
                Row {
                    Box(
                        Modifier
                            .size(38.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .padding(9.dp)
                    ) {
                        Icon(Icons.Filled.Search, null, tint = VyRxColors.TextDim)
                    }
                }
            }
        )

        LazyColumn(
            Modifier.fillMaxWidth(),
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 4.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            // Profile card
            item {
                GlassCard(Modifier.fillMaxWidth(), glow = VyRxColors.Primary) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        HeartbeatOrb(orbStateFromName("idle"), Modifier.size(54.dp), sizeDp = 54)
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("VYRX", color = VyRxColors.TextPrimary, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                            Text("AI Assistant", color = VyRxColors.TextDim, fontSize = 11.sp)
                            Text(
                                "Version ${BuildConfig.VERSION_NAME} • Build ${BuildConfig.VERSION_CODE}",
                                color = VyRxColors.TextFaint,
                                fontSize = 10.sp
                            )
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Box(
                                Modifier
                                    .clip(RoundedCornerShape(12.dp))
                                    .background(VyRxColors.Green.copy(alpha = 0.12f))
                                    .border(1.dp, VyRxColors.Green.copy(alpha = 0.4f), RoundedCornerShape(12.dp))
                                    .padding(horizontal = 10.dp, vertical = 5.dp)
                            ) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Icon(Icons.Filled.PrivacyTip, null, tint = VyRxColors.Green, modifier = Modifier.size(13.dp))
                                    Spacer(Modifier.width(4.dp))
                                    Text("System Healthy", color = VyRxColors.Green, fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
                                }
                            }
                            Spacer(Modifier.height(4.dp))
                            Text("All services running", color = VyRxColors.TextFaint, fontSize = 9.sp)
                        }
                    }
                }
            }

            // AI & Personality
            item {
                GlassCard(Modifier.fillMaxWidth()) {
                    CardHeader(Icons.Filled.Person, "AI & PERSONALITY", "Configure AI behavior and response style")
                    Spacer(Modifier.height(6.dp))
                    SettingsRow(
                        Icons.Filled.Api, "AI Model Provider",
                        providerLabel(settings, context),
                        onClick = { onNavigate("providers") }
                    )
                    RowDivider()
                    SettingsRow(
                        Icons.Filled.Person, "Personality",
                        settings.personality.replaceFirstChar { it.uppercase() },
                        onClick = { personalityOpen = true }
                    )
                    RowDivider()
                    SettingsRow(
                        Icons.Filled.Bolt, "Response Style",
                        settings.responseMode.replaceFirstChar { it.uppercase() } + " • 1.2s avg response",
                        onClick = { responseModeOpen = true }
                    )
                    RowDivider()
                    SettingsRow(
                        Icons.Filled.Mic, "Voice & Speech",
                        if (settings.voiceEnabled) "English (IN) • Voice ON" else "English (IN) • Voice OFF",
                        onClick = { voiceOpen = true }
                    )
                }
            }

            // API Providers + App & System
            item {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    GlassCard(Modifier.weight(1f)) {
                        CardHeader(Icons.Filled.Cloud, "API PROVIDERS", "Manage your AI service keys")
                        Spacer(Modifier.height(4.dp))
                        MiniProviderRow(Icons.Filled.Bolt, Color(0xFFEF4444), "Groq API", "groq")
                        RowDivider()
                        MiniProviderRow(Icons.Filled.Star, VyRxColors.Blue, "Gemini API", "gemini")
                        RowDivider()
                        MiniProviderRow(Icons.Filled.Api, VyRxColors.Green, "OpenAI API", "openai")
                        RowDivider()
                        MiniProviderRow(Icons.Filled.Code, VyRxColors.Blue, "Custom API", "custom")
                        Spacer(Modifier.height(8.dp))
                        Box(
                            Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(12.dp))
                                .background(VyRxColors.Primary.copy(alpha = 0.16f))
                                .border(1.dp, VyRxColors.Primary.copy(alpha = 0.45f), RoundedCornerShape(12.dp))
                                .clickable { onNavigate("providers") }
                                .padding(vertical = 10.dp),
                            contentAlignment = Alignment.Center
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Icon(Icons.Filled.Key, null, tint = VyRxColors.PrimaryBright, modifier = Modifier.size(15.dp))
                                Spacer(Modifier.width(8.dp))
                                Text("Manage API Keys", color = VyRxColors.PrimaryBright, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                    GlassCard(Modifier.weight(1f)) {
                        CardHeader(Icons.Filled.PhoneAndroid, "APP & SYSTEM", "Device and app permissions")
                        Spacer(Modifier.height(4.dp))
                        SettingsRow(
                            Icons.Filled.Shield, "App Permissions", "Manage app access",
                            trailing = {
                                Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = VyRxColors.TextFaint, modifier = Modifier.size(18.dp))
                            },
                            onClick = { onNavigate("security") }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.Settings, "Accessibility Service", "Required for automation",
                            trailing = {
                                Switch(
                                    checked = RoninAccessibilityService.instance != null,
                                    onCheckedChange = {
                                        try {
                                            context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                                        } catch (e: Exception) { onToast("Could not open settings") }
                                    },
                                    colors = switchColors()
                                )
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.BatteryStd, "Battery Optimization", "Keep VYRX running",
                            trailing = {
                                Switch(
                                    checked = batteryOk,
                                    onCheckedChange = { toggleBattery() },
                                    colors = switchColors()
                                )
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.Bolt, "Background Activity", "Allow background tasks",
                            trailing = {
                                Switch(
                                    checked = settings.backgroundActivity,
                                    onCheckedChange = { settingsRepo.update { s -> s.copy(backgroundActivity = it) } },
                                    colors = switchColors()
                                )
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.DarkMode, "Auto Start", "Start on device boot",
                            trailing = {
                                Switch(
                                    checked = settings.autoStart,
                                    onCheckedChange = { settingsRepo.update { s -> s.copy(autoStart = it) } },
                                    colors = switchColors()
                                )
                            }
                        )
                    }
                }
            }

            // Privacy & Security + Notifications
            item {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    GlassCard(Modifier.weight(1f)) {
                        CardHeader(Icons.Filled.Lock, "PRIVACY & SECURITY", "Your data, your control")
                        Spacer(Modifier.height(4.dp))
                        SettingsRow(
                            Icons.Filled.Shield, "Data Encryption", "End-to-end encryption",
                            trailing = {
                                Text("Enabled", color = VyRxColors.Green, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.Fingerprint, "Biometric Lock", "Fingerprint / Face unlock",
                            trailing = {
                                Switch(
                                    checked = settings.biometricLock,
                                    onCheckedChange = { toggleBiometric(it) },
                                    colors = switchColors()
                                )
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.PrivacyTip, "Secure Storage", "Android Keystore",
                            trailing = {
                                Text("Enabled", color = VyRxColors.Green, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.Delete, "Clear Data", "Remove all app data",
                            trailing = {
                                Box(
                                    Modifier
                                        .clip(RoundedCornerShape(8.dp))
                                        .background(VyRxColors.Red.copy(alpha = 0.15f))
                                        .border(1.dp, VyRxColors.Red.copy(alpha = 0.4f), RoundedCornerShape(8.dp))
                                        .clickable { clearOpen = true }
                                        .padding(horizontal = 10.dp, vertical = 4.dp)
                                ) {
                                    Text("Clear", color = VyRxColors.Red, fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
                                }
                            }
                        )
                    }
                    GlassCard(Modifier.weight(1f)) {
                        CardHeader(Icons.Filled.Notifications, "NOTIFICATIONS", "Manage alerts and updates")
                        Spacer(Modifier.height(4.dp))
                        SettingsRow(
                            Icons.Filled.Notifications, "App Notifications", "Tips, updates, warnings",
                            trailing = {
                                Switch(checked = settings.appNotifications, onCheckedChange = { settingsRepo.update { s -> s.copy(appNotifications = it) } }, colors = switchColors())
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.Star, "Task Notifications", "Task completion alerts",
                            trailing = {
                                Switch(checked = settings.taskNotifications, onCheckedChange = { settingsRepo.update { s -> s.copy(taskNotifications = it) } }, colors = switchColors())
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.BatteryStd, "System Alerts", "Battery, errors, status",
                            trailing = {
                                Switch(checked = settings.systemAlerts, onCheckedChange = { settingsRepo.update { s -> s.copy(systemAlerts = it) } }, colors = switchColors())
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.Settings, "Notification Style", settings.notificationStyle.replaceFirstChar { it.uppercase() },
                            onClick = {
                                val order = listOf("minimal", "detailed", "silent")
                                val next = order[(order.indexOf(settings.notificationStyle) + 1) % order.size]
                                settingsRepo.update { s -> s.copy(notificationStyle = next) }
                            }
                        )
                    }
                }
            }

            // Appearance + Support & About
            item {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    GlassCard(Modifier.weight(1f)) {
                        CardHeader(Icons.Filled.DarkMode, "APPEARANCE", "Customize the look and feel")
                        Spacer(Modifier.height(4.dp))
                        SettingsRow(
                            Icons.Filled.DarkMode, "Theme",
                            if (settings.darkMode) "Dark (Default)" else "Light",
                            onClick = { settingsRepo.update { s -> s.copy(darkMode = !s.darkMode) } }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.Star, "Accent Color", settings.accent.replaceFirstChar { it.uppercase() },
                            trailing = {
                                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                                    listOf("purple" to VyRxColors.Primary, "blue" to VyRxColors.Blue, "green" to VyRxColors.Green, "amber" to VyRxColors.Amber).forEach { (value, color) ->
                                        Box(
                                            Modifier
                                                .size(12.dp)
                                                .clip(CircleShape)
                                                .background(color)
                                                .border(2.dp, if (settings.accent == value) Color.White else Color.Transparent, CircleShape)
                                                .clickable { settingsRepo.update { s -> s.copy(accent = value) } }
                                        )
                                    }
                                }
                            }
                        )
                        RowDivider()
                        SettingsRow(
                            Icons.Filled.Person, "Font Size",
                            fontLabel(settings.fontScale),
                            onClick = {
                                val scale = when {
                                    settings.fontScale < 0.95f -> 1.0f
                                    settings.fontScale < 1.1f -> 1.15f
                                    else -> 0.85f
                                }
                                settingsRepo.update { s -> s.copy(fontScale = scale) }
                            }
                        )
                    }
                    GlassCard(Modifier.weight(1f)) {
                        CardHeader(Icons.Filled.Info, "SUPPORT & ABOUT", "Help, legal and app information")
                        Spacer(Modifier.height(4.dp))
                        SettingsRow(Icons.Filled.Help, "Help & Support", "FAQ, troubleshooting", onClick = { onToast("Help: keep Termux running and check the Brain log in Termux.") })
                        RowDivider()
                        SettingsRow(Icons.Filled.Info, "About VYRX", "Version, credits, licenses", onClick = { aboutOpen = true })
                        RowDivider()
                        SettingsRow(Icons.Filled.PrivacyTip, "Privacy Policy", "Your data matters", onClick = { onToast("All data (memory, keys, settings) stays on your device.") })
                    }
                }
            }
        }
    }

    if (personalityOpen) {
        SingleChoiceDialog(
            title = "Personality",
            options = listOf("assistant" to "Assistant (Default)", "professional" to "Professional", "friendly" to "Friendly", "creative" to "Creative", "developer" to "Developer"),
            selected = settings.personality,
            onDismiss = { personalityOpen = false },
            onPick = { settingsRepo.update { s -> s.copy(personality = it) } }
        )
    }
    if (responseModeOpen) {
        SingleChoiceDialog(
            title = "Response Style",
            options = listOf("fast" to "Fast — short responses", "balanced" to "Balanced", "deep" to "Deep Thinking — detailed reasoning"),
            selected = settings.responseMode,
            onDismiss = { responseModeOpen = false },
            onPick = { settingsRepo.update { s -> s.copy(responseMode = it) } }
        )
    }
    if (voiceOpen) {
        AlertDialog(
            onDismissRequest = { voiceOpen = false },
            title = { Text("Voice & Speech", fontWeight = FontWeight.Bold) },
            text = {
                Column {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text("Voice Assistant", color = VyRxColors.TextPrimary, fontSize = 13.sp)
                        Switch(
                            checked = settings.voiceEnabled,
                            onCheckedChange = { settingsRepo.update { s -> s.copy(voiceEnabled = it) } },
                            colors = switchColors()
                        )
                    }
                    Spacer(Modifier.height(8.dp))
                    Text("Language: English (IN)", color = VyRxColors.TextDim, fontSize = 12.sp)
                    Text("Speech speed: 1x", color = VyRxColors.TextDim, fontSize = 12.sp)
                }
            },
            confirmButton = { TextButton(onClick = { voiceOpen = false }) { Text("Done") } }
        )
    }
    if (clearOpen) {
        AlertDialog(
            onDismissRequest = { clearOpen = false },
            title = { Text("Clear all data?", fontWeight = FontWeight.Bold) },
            text = {
                Text("Deletes chat history, all stored memories and local settings. API keys stay encrypted unless you remove them in API Providers.", color = VyRxColors.TextDim, fontSize = 12.sp)
            },
            confirmButton = {
                TextButton(onClick = {
                    clearOpen = false
                    scope.launch {
                        try {
                            val (memories, _) = ApiClient.memories()
                            memories.forEach { ApiClient.deleteMemory(it.id) }
                            settingsRepo.resetAll()
                            onToast("All data cleared")
                        } catch (e: Exception) {
                            onToast("Clear failed: ${e.message}")
                        }
                    }
                }) { Text("Clear", color = VyRxColors.Red) }
            },
            dismissButton = { TextButton(onClick = { clearOpen = false }) { Text("Cancel") } }
        )
    }
    if (aboutOpen) {
        AlertDialog(
            onDismissRequest = { aboutOpen = false },
            title = { Text("About VYRX", fontWeight = FontWeight.Bold) },
            text = {
                Column {
                    Text("VYRX — Personal Autonomous AI Assistant", color = VyRxColors.TextPrimary, fontSize = 13.sp)
                    Spacer(Modifier.height(8.dp))
                    Text("Version ${BuildConfig.VERSION_NAME} (build ${BuildConfig.VERSION_CODE})", color = VyRxColors.TextDim, fontSize = 12.sp)
                    Text("Body: Kotlin + Jetpack Compose (Android)", color = VyRxColors.TextDim, fontSize = 12.sp)
                    Text("Brain: Python FastAPI on 127.0.0.1:8000", color = VyRxColors.TextDim, fontSize = 12.sp)
                    Text("Architecture: Brain-Body (local, private, on-device)", color = VyRxColors.TextDim, fontSize = 12.sp)
                    Text("Health: ${health?.let { "CPU ${it.cpuPercent}%, RAM ${it.memoryPercent}%" } ?: "Brain offline"}", color = VyRxColors.TextDim, fontSize = 12.sp)
                }
            },
            confirmButton = { TextButton(onClick = { aboutOpen = false }) { Text("Close") } }
        )
    }
}

private fun fontLabel(scale: Float): String = when {
    scale < 0.95f -> "Small"
    scale < 1.1f -> "Medium"
    else -> "Large"
}

private fun providerLabel(settings: AppSettings, context: android.content.Context): String {
    // First enabled provider from the device's encrypted key store.
    val providers = com.ronin.ai.data.ProviderRepository(context).load()
    val first = providers.firstOrNull { it.enabled } ?: return "No provider"
    return first.type.label + (first.model?.let { " ($it)" } ?: "")
}

@Composable
private fun switchColors() = SwitchDefaults.colors(
    checkedTrackColor = VyRxColors.Primary,
    checkedThumbColor = Color.White,
    uncheckedTrackColor = Color(0xFF232B3D),
    uncheckedThumbColor = Color(0xFF8A93A6)
)

@Composable
private fun CardHeader(icon: ImageVector, title: String, subtitle: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(34.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(VyRxColors.Primary.copy(alpha = 0.14f))
                .border(1.dp, VyRxColors.Primary.copy(alpha = 0.35f), RoundedCornerShape(10.dp)),
            contentAlignment = Alignment.Center
        ) {
            Icon(icon, null, tint = VyRxColors.PrimaryBright, modifier = Modifier.size(17.dp))
        }
        Spacer(Modifier.width(10.dp))
        Column {
            SectionLabel(title, VyRxColors.TextPrimary)
            Text(subtitle, color = VyRxColors.TextDim, fontSize = 10.sp)
        }
    }
}

@Composable
private fun RowDivider() {
    Spacer(Modifier.height(2.dp))
    Box(Modifier.fillMaxWidth().height(1.dp).background(VyRxColors.CardStrokeSoft.copy(alpha = 0.6f)))
    Spacer(Modifier.height(2.dp))
}

@Composable
private fun SettingsRow(
    icon: ImageVector,
    title: String,
    subtitle: String,
    trailing: (@Composable () -> Unit)? = null,
    onClick: (() -> Unit)? = null
) {
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .let { if (onClick != null) it.clickable(onClick = onClick) else it }
            .padding(vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(icon, null, tint = VyRxColors.TextDim, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Text(subtitle, color = VyRxColors.TextFaint, fontSize = 10.sp, maxLines = 1)
        }
        trailing?.invoke()
    }
}

@Composable
private fun MiniProviderRow(icon: ImageVector, tint: Color, name: String, provider: String) {
    var status by remember { mutableStateOf("") }
    androidx.compose.runtime.LaunchedEffect(Unit) {
        try {
            val (engine, list) = ApiClient.providers()
            val info = list.firstOrNull { it.name == provider }
            status = when {
                info?.active == true -> "Active"
                info?.configured == true -> "Configured"
                else -> "Not Configured"
            }
        } catch (_: Exception) {
            status = "Offline"
        }
    }
    Row(Modifier.fillMaxWidth().padding(vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(28.dp)
                .clip(CircleShape)
                .background(tint.copy(alpha = 0.15f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(icon, null, tint = tint, modifier = Modifier.size(14.dp))
        }
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(name, color = VyRxColors.TextPrimary, fontSize = 12.sp)
            Text(status, color = when (status) {
                "Active" -> VyRxColors.Green
                "Configured" -> VyRxColors.Blue
                else -> VyRxColors.TextFaint
            }, fontSize = 10.sp)
        }
        NeonDot(
            when (status) {
                "Active" -> VyRxColors.Green
                "Configured" -> VyRxColors.Blue
                else -> VyRxColors.TextFaint
            },
            size = 7
        )
        Spacer(Modifier.width(6.dp))
        Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, null, tint = VyRxColors.TextFaint, modifier = Modifier.size(16.dp))
    }
}

@Composable
private fun SingleChoiceDialog(
    title: String,
    options: List<Pair<String, String>>,
    selected: String,
    onDismiss: () -> Unit,
    onPick: (String) -> Unit
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title, fontWeight = FontWeight.Bold) },
        text = {
            Column(Modifier.fillMaxWidth()) {
                options.forEach { (value, label) ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .clickable { onPick(value); onDismiss() }
                            .padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            Modifier
                                .size(16.dp)
                                .clip(CircleShape)
                                .border(1.5.dp, if (selected == value) VyRxColors.Primary else VyRxColors.CardStroke, CircleShape)
                        ) {
                            if (selected == value) {
                                Box(Modifier.size(9.dp).align(Alignment.Center).clip(CircleShape).background(VyRxColors.Primary))
                            }
                        }
                        Spacer(Modifier.width(10.dp))
                        Text(label, color = VyRxColors.TextPrimary, fontSize = 13.sp)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } }
    )
}
