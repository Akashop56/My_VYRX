package com.ronin.ai.ui.drawer

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Tools
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.data.BrainRepository
import com.ronin.ai.ui.theme.NeonDot
import com.ronin.ai.ui.theme.SectionLabel
import com.ronin.ai.ui.theme.VyRxColors

data class DrawerItem(val route: String, val icon: ImageVector, val label: String, val sub: String)

val DRAWER_ITEMS = listOf(
    DrawerItem("home", Icons.Filled.Home, "Home", "Dashboard, Status & Quick Actions"),
    DrawerItem("chat", Icons.Filled.Search, "Chat", "Conversations & Ask Anything"),
    DrawerItem("dashboard", Icons.Filled.Dashboard, "Dashboard", "Stats, Logs & Monitoring"),
    DrawerItem("memory", Icons.Filled.Memory, "Memory", "AI Memory & Knowledge Base"),
    DrawerItem("tools", Icons.Filled.Tools, "Tools", "AI Agent Tool Kit"),
    DrawerItem("settings", Icons.Filled.Settings, "Settings", "Customize Your VYRX Experience")
)

@Composable
fun VyRxDrawerContent(
    currentRoute: String,
    onNavigate: (String) -> Unit,
    onToast: (String) -> Unit
) {
    val state by BrainRepository.state.collectAsState()

    Column(Modifier.fillMaxHeight().background(Color(0xFF0A0D14))) {
        // Profile header
        Row(
            Modifier
                .fillMaxWidth()
                .padding(start = 18.dp, end = 16.dp, top = 32.dp, bottom = 16.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Box(
                Modifier
                    .size(58.dp)
                    .clip(CircleShape)
                    .background(VyRxColors.Primary)
                    .border(2.dp, VyRxColors.PrimaryBright.copy(alpha = 0.7f), CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Text("A", color = Color.White, fontSize = 24.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.width(14.dp))
            Column {
                Text("Akash", color = VyRxColors.TextPrimary, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                Text("Think • Create • Achieve", color = VyRxColors.TextDim, fontSize = 11.sp)
            }
        }

        LazyColumn(Modifier.weight(1f)) {
            item { SectionLabel("MAIN MENU") }
            DRAWER_ITEMS.forEach { entry ->
                item {
                    val active = currentRoute == entry.route
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(14.dp))
                            .background(if (active) VyRxColors.Primary.copy(alpha = 0.16f) else Color.Transparent)
                            .border(
                                1.dp,
                                if (active) VyRxColors.Primary.copy(alpha = 0.5f) else Color.Transparent,
                                RoundedCornerShape(14.dp)
                            )
                            .clickable { onNavigate(entry.route) }
                            .padding(start = 10.dp, end = 10.dp, top = 3.dp, bottom = 3.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            Modifier
                                .size(38.dp)
                                .clip(RoundedCornerShape(11.dp))
                                .background(if (active) VyRxColors.Primary.copy(alpha = 0.25f) else VyRxColors.CardBackground)
                                .border(
                                    1.dp,
                                    if (active) VyRxColors.Primary.copy(alpha = 0.5f) else VyRxColors.CardStrokeSoft,
                                    RoundedCornerShape(11.dp)
                                ),
                            contentAlignment = Alignment.Center
                        ) {
                            Icon(entry.icon, null, tint = if (active) VyRxColors.PrimaryBright else VyRxColors.TextDim, modifier = Modifier.size(18.dp))
                        }
                        Spacer(Modifier.width(12.dp))
                        Column(Modifier.weight(1f)) {
                            Text(entry.label, color = if (active) VyRxColors.TextPrimary else VyRxColors.TextDim, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
                            Text(entry.sub, color = VyRxColors.TextFaint, fontSize = 10.sp)
                        }
                        if (active) NeonDot(VyRxColors.PrimaryBright, size = 8)
                    }
                }
            }

            item {
                Spacer(Modifier.height(14.dp))
                SectionLabel("QUICK ACTIONS")
            }
            item {
                QuickActionRow(Icons.Filled.Star, VyRxColors.Amber, "Start Task", "Execute a new task", onToast)
            }
            item {
                QuickActionRow(Icons.Filled.Memory, VyRxColors.Blue, "AI Orb", "Open your orb — think, ask, create", onToast)
            }
            item {
                QuickActionRow(Icons.Filled.Tools, VyRxColors.Green, "Manage Tools", "Configure AI tools", onToast)
            }

            item {
                Spacer(Modifier.height(14.dp))
                // VYRX Pro card
                Box(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 10.dp)
                        .clip(RoundedCornerShape(16.dp))
                        .background(VyRxColors.Primary.copy(alpha = 0.14f))
                        .border(1.dp, VyRxColors.Primary.copy(alpha = 0.5f), RoundedCornerShape(16.dp))
                        .padding(14.dp)
                ) {
                    Column {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Filled.Star, null, tint = VyRxColors.Amber, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(8.dp))
                            Text("VYRX PRO", color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Bold)
                        }
                        Spacer(Modifier.height(4.dp))
                        Text("Unlock advanced AI capabilities", color = VyRxColors.TextDim, fontSize = 10.sp)
                        Spacer(Modifier.height(10.dp))
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            Box(
                                Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(Color.White.copy(alpha = 0.92f))
                                    .padding(vertical = 7.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Text("Upgrade", color = Color(0xFF10141F), fontSize = 10.sp, fontWeight = FontWeight.Bold)
                            }
                            Box(
                                Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(Color.Transparent)
                                    .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(10.dp))
                                    .padding(vertical = 7.dp),
                                contentAlignment = Alignment.Center
                            ) {
                                Text("Learn More", color = VyRxColors.TextDim, fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
                            }
                        }
                    }
                }
            }

            item {
                Spacer(Modifier.height(14.dp))
                // About
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 10.dp, vertical = 6.dp)
                        .clip(RoundedCornerShape(14.dp))
                        .background(VyRxColors.CardBackground.copy(alpha = 0.6f))
                        .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(14.dp))
                        .clickable { onToast("VYRX v1.0.0 • Build 2026.09.01 • Brain-Body Architecture") }
                        .padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Icon(Icons.Filled.Info, null, tint = VyRxColors.TextDim, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(10.dp))
                    Column(Modifier.weight(1f)) {
                        Text("About VYRX", color = VyRxColors.TextPrimary, fontSize = 12.sp, fontWeight = FontWeight.Medium)
                        Text("v1.0.0 • Build 2026.09.01", color = VyRxColors.TextFaint, fontSize = 9.sp)
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        NeonDot(if (state.online) VyRxColors.Green else VyRxColors.Red, size = 7)
                        Spacer(Modifier.width(4.dp))
                        Text("Brain ${if (state.online) "online" else "offline"}", color = VyRxColors.TextFaint, fontSize = 9.sp)
                    }
                }
            }
            item { Spacer(Modifier.height(20.dp)) }
        }
    }
}

@Composable
private fun QuickActionRow(icon: ImageVector, tint: Color, title: String, sub: String, onToast: (String) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 10.dp, vertical = 3.dp)
            .clip(RoundedCornerShape(14.dp))
            .background(VyRxColors.CardBackground.copy(alpha = 0.7f))
            .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(14.dp))
            .clickable { onToast("Use the center orb on the bottom bar — or open Chat.") }
            .padding(start = 12.dp, end = 14.dp, top = 9.dp, bottom = 9.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Icon(icon, null, tint = tint, modifier = Modifier.size(18.dp))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.SemiBold)
            Text(sub, color = VyRxColors.TextFaint, fontSize = 10.sp)
        }
    }
}
