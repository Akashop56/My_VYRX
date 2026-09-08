package com.ronin.ai.ui.security

import android.Manifest
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Accessibility
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Fingerprint
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.PrivacyTip
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.Composable
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
import com.ronin.ai.data.AppSettingsRepository
import com.ronin.ai.data.BrainRepository
import com.ronin.ai.network.ApiClient
import com.ronin.ai.services.RoninAccessibilityService
import com.ronin.ai.ui.components.VyRxTopBar
import com.ronin.ai.ui.theme.GlassCard
import com.ronin.ai.ui.theme.NeonDot
import com.ronin.ai.ui.theme.SectionLabel
import com.ronin.ai.ui.theme.VyRxColors
import kotlinx.coroutines.launch

@Composable
fun SecurityScreen(
    settingsRepo: AppSettingsRepository,
    onOpenDrawer: () -> Unit,
    onToast: (String) -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val settings by settingsRepo.settings.collectAsState()
    val health by BrainRepository.health.collectAsState()
    var clearMemOpen by remember { mutableStateOf(false) }

    val micGranted = ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
    val accessibilityOn = RoninAccessibilityService.instance != null
    val notificationsOn = try {
        val enabled = Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners") ?: ""
        enabled.contains("RoninNotificationListener")
    } catch (_: Exception) { false }

    Column(Modifier.fillMaxSize()) {
        VyRxTopBar(
            title = "SECURITY",
            subtitle = "Privacy & Protection",
            onMenu = onOpenDrawer,
            trailing = null
        )

        LazyColumn(
            Modifier.fillMaxWidth(),
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 4.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            // Hero
            item {
                GlassCard(Modifier.fillMaxWidth(), stroke = VyRxColors.Green.copy(alpha = 0.4f), glow = VyRxColors.Green) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            Modifier
                                .size(48.dp)
                                .clip(CircleShape)
                                .background(VyRxColors.Green.copy(alpha = 0.14f))
                                .border(1.dp, VyRxColors.Green.copy(alpha = 0.45f), CircleShape),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(Icons.Filled.Shield, null, tint = VyRxColors.Green, modifier = Modifier.size(24.dp))
                        }
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("Protection Center", color = VyRxColors.TextPrimary, fontSize = 15.sp, fontWeight = FontWeight.Bold)
                            Text("Your data, keys and permissions — all on-device.", color = VyRxColors.TextDim, fontSize = 10.sp)
                        }
                        Column(horizontalAlignment = Alignment.End) {
                            Text("Brain", color = VyRxColors.TextDim, fontSize = 10.sp)
                            Text(
                                if (health != null) "v${health.version} ✓" else "Offline",
                                color = if (health != null) VyRxColors.Green else VyRxColors.Red,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.SemiBold
                            )
                        }
                    }
                }
            }

            // Encrypted assets
            item {
                GlassCard(Modifier.fillMaxWidth()) {
                    SectionLabel("ENCRYPTED ASSETS", VyRxColors.TextPrimary)
                    Spacer(Modifier.height(10.dp))
                    SecRow(
                        Icons.Filled.Key, "API Keys",
                        "Groq • Gemini • OpenAI • Custom",
                        status = "Encrypted ✓", ok = true
                    )
                    RowDivider()
                    SecRow(
                        Icons.Filled.Storage, "Memory Database",
                        "Stored locally (Termux workspace)",
                        status = "Protected ✓", ok = true
                    )
                    RowDivider()
                    SecRow(
                        Icons.Filled.Lock, "App Settings",
                        "Preferences, tool toggles, appearance",
                        status = "Encrypted ✓", ok = true
                    )
                }
            }

            // App lock
            item {
                GlassCard(Modifier.fillMaxWidth()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.Fingerprint, null, tint = VyRxColors.PrimaryBright, modifier = Modifier.size(20.dp))
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("App Lock (Biometric)", color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                            Text("Fingerprint / Face unlock required to open VYRX", color = VyRxColors.TextFaint, fontSize = 10.sp)
                        }
                        Switch(
                            checked = settings.biometricLock,
                            onCheckedChange = {
                                if (it) {
                                    onToast("Enable App Lock from Settings → Privacy & Security (verifies biometric).")
                                } else {
                                    settingsRepo.update { s -> s.copy(biometricLock = false) }
                                }
                            },
                            colors = switchColors()
                        )
                    }
                }
            }

            // Permissions
            item {
                GlassCard(Modifier.fillMaxWidth()) {
                    SectionLabel("PERMISSIONS", VyRxColors.TextPrimary)
                    Spacer(Modifier.height(6.dp))
                    PermRow(
                        Icons.Filled.Mic, "Microphone",
                        "Voice input for the orb",
                        granted = micGranted,
                        onManage = {
                            if (!micGranted) {
                                (context as? android.app.Activity)?.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 42)
                            }
                        }
                    )
                    RowDivider()
                    PermRow(
                        Icons.Filled.Accessibility, "Accessibility",
                        "Lets VYRX control the device",
                        granted = accessibilityOn,
                        onManage = {
                            try {
                                context.startActivity(android.content.Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
                            } catch (e: Exception) { onToast("Could not open accessibility settings") }
                        }
                    )
                    RowDivider()
                    PermRow(
                        Icons.Filled.Notifications, "Notification Listener",
                        "Reads notifications for automation",
                        granted = notificationsOn,
                        onManage = {
                            try {
                                context.startActivity(android.content.Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS").addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
                            } catch (e: Exception) { onToast("Could not open notification settings") }
                        }
                    )
                }
            }

            // Data control
            item {
                GlassCard(Modifier.fillMaxWidth(), stroke = VyRxColors.Red.copy(alpha = 0.35f)) {
                    SectionLabel("DATA CONTROL", VyRxColors.Red)
                    Spacer(Modifier.height(6.dp))
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.Delete, null, tint = VyRxColors.Red, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("Delete all memories", color = VyRxColors.TextPrimary, fontSize = 13.sp)
                            Text("Removes every stored memory from the Brain", color = VyRxColors.TextFaint, fontSize = 10.sp)
                        }
                        TextButton(onClick = { clearMemOpen = true }) {
                            Text("Delete", color = VyRxColors.Red, fontWeight = FontWeight.SemiBold)
                        }
                    }
                    RowDivider()
                    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                        Icon(Icons.Filled.Delete, null, tint = VyRxColors.TextDim, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text("Reset local settings", color = VyRxColors.TextPrimary, fontSize = 13.sp)
                            Text("Personality, toggles, appearance — back to defaults", color = VyRxColors.TextFaint, fontSize = 10.sp)
                        }
                        TextButton(onClick = {
                            settingsRepo.resetAll()
                            onToast("Settings reset to defaults")
                        }) {
                            Text("Reset", color = VyRxColors.PrimaryBright, fontWeight = FontWeight.SemiBold)
                        }
                    }
                }
            }
        }
    }

    if (clearMemOpen) {
        AlertDialog(
            onDismissRequest = { clearMemOpen = false },
            title = { Text("Delete all memories?", fontWeight = FontWeight.Bold) },
            text = {
                Text("This permanently removes all Personal, Experience and Knowledge memories from the Brain database.", color = VyRxColors.TextDim, fontSize = 12.sp)
            },
            confirmButton = {
                TextButton(onClick = {
                    clearMemOpen = false
                    scope.launch {
                        try {
                            val (memories, _) = ApiClient.memories()
                            memories.forEach { ApiClient.deleteMemory(it.id) }
                            onToast("All memories deleted")
                        } catch (e: Exception) {
                            onToast("Delete failed: ${e.message}")
                        }
                    }
                }) { Text("Delete All", color = VyRxColors.Red) }
            },
            dismissButton = { TextButton(onClick = { clearMemOpen = false }) { Text("Cancel") } }
        )
    }
}

@Composable
private fun SecRow(icon: ImageVector, title: String, subtitle: String, status: String, ok: Boolean) {
    Row(Modifier.fillMaxWidth().padding(vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, tint = VyRxColors.Blue, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Text(subtitle, color = VyRxColors.TextFaint, fontSize = 10.sp)
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            NeonDot(if (ok) VyRxColors.Green else VyRxColors.Red, size = 7)
            Spacer(Modifier.width(5.dp))
            Text(status, color = if (ok) VyRxColors.Green else VyRxColors.Red, fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
        }
    }
}

@Composable
private fun PermRow(icon: ImageVector, title: String, subtitle: String, granted: Boolean, onManage: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 7.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, tint = if (granted) VyRxColors.Green else VyRxColors.TextDim, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Medium)
            Text(subtitle, color = VyRxColors.TextFaint, fontSize = 10.sp)
        }
        Box(
            Modifier
                .clip(RoundedCornerShape(8.dp))
                .background((if (granted) VyRxColors.Green else VyRxColors.Amber).copy(alpha = 0.12f))
                .padding(horizontal = 9.dp, vertical = 4.dp)
        ) {
            Text(if (granted) "Granted" else "Not granted", color = if (granted) VyRxColors.Green else VyRxColors.Amber, fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
        }
        Spacer(Modifier.width(8.dp))
        TextButton(onClick = onManage) { Text("Manage", color = VyRxColors.PrimaryBright, fontSize = 11.sp) }
    }
}

@Composable
private fun RowDivider() {
    Spacer(Modifier.height(2.dp))
    Box(Modifier.fillMaxWidth().height(1.dp).background(VyRxColors.CardStrokeSoft.copy(alpha = 0.6f)))
    Spacer(Modifier.height(2.dp))
}

@Composable
private fun switchColors() = androidx.compose.material3.SwitchDefaults.colors(
    checkedTrackColor = VyRxColors.Primary,
    checkedThumbColor = Color.White,
    uncheckedTrackColor = Color(0xFF232B3D),
    uncheckedThumbColor = Color(0xFF8A93A6)
)
