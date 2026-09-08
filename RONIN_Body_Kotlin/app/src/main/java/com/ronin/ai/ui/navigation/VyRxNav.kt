package com.ronin.ai.ui.navigation

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.Search
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
import com.ronin.ai.ui.components.HeartbeatOrb
import com.ronin.ai.ui.theme.VyRxColors
import com.ronin.ai.ui.theme.orbStateFromName

/** Orb gesture modes for the center bottom-bar orb. */
enum class OrbTap { SHORT, LONG }

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun VyRxBottomBar(
    currentRoute: String,
    onNavigate: (String) -> Unit,
    onOrbTap: (OrbTap) -> Unit
) {
    val state by BrainRepository.state.collectAsState()

    Row(
        Modifier
            .fillMaxWidth()
            .background(Color(0xFF0A0D14))
            .padding(top = 6.dp, bottom = 14.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        NavItem(Modifier.weight(1f), Icons.Filled.Home, "Home", "home", currentRoute, onNavigate)
        NavItem(Modifier.weight(1f), Icons.Filled.Search, "Chat", "chat", currentRoute, onNavigate)

        // Center orb — click opens voice chat, long press triggers emergency mode
        Box(
            Modifier
                .size(58.dp)
                .clip(CircleShape)
                .background(Color(0xFF101522))
                .border(1.5.dp, VyRxColors.Primary.copy(alpha = 0.55f), CircleShape)
                .combinedClickable(onClick = { onOrbTap(OrbTap.SHORT) }, onLongClick = { onOrbTap(OrbTap.LONG) })
                .padding(9.dp)
        ) {
            HeartbeatOrb(orbStateFromName(if (state.online) state.state else "idle"), Modifier.size(40.dp), sizeDp = 40)
        }

        NavItem(Modifier.weight(1f), Icons.Filled.Dashboard, "Dashboard", "dashboard", currentRoute, onNavigate)
        NavItem(Modifier.weight(1f), Icons.Filled.Memory, "Memory", "memory", currentRoute, onNavigate)
    }
}

@Composable
private fun NavItem(
    modifier: Modifier,
    icon: ImageVector,
    label: String,
    route: String,
    currentRoute: String,
    onNavigate: (String) -> Unit
) {
    val active = currentRoute == route
    Column(
        modifier
            .clip(CircleShape)
            .clickable { onNavigate(route) }
            .padding(horizontal = 8.dp, vertical = 4.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Icon(icon, null, tint = if (active) VyRxColors.PrimaryBright else VyRxColors.TextFaint, modifier = Modifier.size(20.dp))
        Spacer(Modifier.height(2.dp))
        Text(
            label,
            color = if (active) VyRxColors.TextPrimary else VyRxColors.TextFaint,
            fontSize = 8.5.sp,
            fontWeight = if (active) FontWeight.SemiBold else FontWeight.Medium
        )
    }
}
