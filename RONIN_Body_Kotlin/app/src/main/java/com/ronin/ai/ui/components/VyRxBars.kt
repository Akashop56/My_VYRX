package com.ronin.ai.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.outlined.Chat
import androidx.compose.material.icons.outlined.Dashboard
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Psychology
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.ui.theme.OrbState
import com.ronin.ai.ui.theme.VyRxColors

// ---------------------------------------------------------------------------
// Top bar: hamburger | centered title+subtitle | trailing actions
// ---------------------------------------------------------------------------

@Composable
fun VyRxTopBar(
    title: String,
    subtitle: String? = null,
    onMenu: () -> Unit,
    trailing: (@Composable () -> Unit)? = null,
    modifier: Modifier = Modifier
) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        VyRxIconButton(Icons.Filled.Menu, onMenu, tint = VyRxColors.TextDim)
        Spacer(Modifier.weight(1f))
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text(
                text = title,
                color = VyRxColors.TextPrimary,
                fontSize = 20.sp,
                fontWeight = FontWeight.Bold,
                letterSpacing = 1.5.sp
            )
            if (subtitle != null) {
                Text(text = subtitle, color = VyRxColors.TextDim, fontSize = 11.sp)
            }
        }
        Spacer(Modifier.weight(1f))
        trailing?.invoke() ?: VyRxIconButton(Icons.Filled.Tune, {}, tint = VyRxColors.TextDim)
    }
}

@Composable
fun VyRxIconButton(icon: ImageVector, onClick: () -> Unit, tint: Color = VyRxColors.TextDim, modifier: Modifier = Modifier) {
    Box(
        modifier
            .size(38.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(Color.Transparent)
            .clickable(onClick = onClick),
        contentAlignment = Alignment.Center
    ) {
        Icon(icon, null, tint = tint, modifier = Modifier.size(20.dp))
    }
}

// ---------------------------------------------------------------------------
// Bottom navigation: Home | Chat | [ORB] | Dashboard | Memory
// ---------------------------------------------------------------------------

private data class NavItem(val route: String, val label: String, val icon: ImageVector, val outlinedIcon: ImageVector)

private val NAV_ITEMS = listOf(
    NavItem("home", "Home", Icons.Filled.Home, Icons.Outlined.Home),
    NavItem("chat", "Chat", Icons.Outlined.Chat, Icons.Outlined.Chat),
    NavItem("dashboard", "Dashboard", Icons.Filled.Dashboard, Icons.Outlined.Dashboard),
    NavItem("memory", "Memory", Icons.Filled.Psychology, Icons.Outlined.Psychology)
)

@Composable
fun VyRxBottomBar(
    currentRoute: String,
    onNavigate: (String) -> Unit,
    orbState: OrbState,
    onOrbTap: () -> Unit,
    onOrbLongPress: () -> Unit,
    modifier: Modifier = Modifier
) {
    Surface(
        modifier = modifier
            .fillMaxWidth()
            .navigationBarsPadding(),
        color = VyRxColors.Surface.copy(alpha = 0.92f),
        shadowElevation = 18.dp
    ) {
        Box(Modifier.matchParentSize()) {
            // hairline on top
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(1.dp)
                    .background(VyRxColors.CardStroke)
            )
        }
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 8.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            val left = NAV_ITEMS.take(2)
            val right = NAV_ITEMS.drop(2)
            left.forEach { item -> NavTab(item, currentRoute == item.route, onNavigate) }
            Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
                CenterOrbButton(orbState, onOrbTap, onOrbLongPress, Modifier.size(64.dp))
            }
            right.forEach { item -> NavTab(item, currentRoute == item.route, onNavigate) }
        }
    }
}

@Composable
private fun NavTab(item: NavItem, active: Boolean, onNavigate: (String) -> Unit) {
    val color = if (active) VyRxColors.PrimaryBright else VyRxColors.TextDim
    Column(
        Modifier
            .clip(RoundedCornerShape(14.dp))
            .clickable { onNavigate(item.route) }
            .padding(horizontal = 12.dp, vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Icon(
            if (active) item.icon else item.outlinedIcon,
            null,
            tint = color,
            modifier = Modifier.size(21.dp)
        )
        Spacer(Modifier.height(3.dp))
        Text(
            item.label,
            color = if (active) VyRxColors.PrimaryBright else VyRxColors.TextFaint,
            fontSize = 10.sp,
            fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal
        )
    }
}
