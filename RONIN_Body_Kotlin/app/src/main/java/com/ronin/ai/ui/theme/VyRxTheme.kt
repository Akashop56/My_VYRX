package com.ronin.ai.ui.theme

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.Typography
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

object VyRxColors {
    val Background = Color(0xFF05060A)
    val Surface = Color(0xFF0E1118)
    val SurfaceGlass = Color(0x29141A26)
    val CardStroke = Color(0xFF1E2635)
    val CardStrokeSoft = Color(0xFF161D2A)
    val CardBackground = Color(0xFF10151F)
    val Primary = Color(0xFF8B5CF6)
    val PrimaryBright = Color(0xFFA78BFA)
    val PrimaryDim = Color(0xFF4C3A85)
    val Blue = Color(0xFF38BDF8)
    val BlueDeep = Color(0xFF3B82F6)
    val Green = Color(0xFF4ADE80)
    val Amber = Color(0xFFFBBF24)
    val Red = Color(0xFFF87171)
    val TextPrimary = Color(0xFFE8EAF0)
    val TextDim = Color(0xFF8A93A6)
    val TextFaint = Color(0xFF5A6478)
    val LogGreen = Color(0xFF34D399)
    val LogTime = Color(0xFF3E9C6E)

    fun accentColor(accent: String): Color = when (accent) {
        "blue" -> Blue
        "green" -> Green
        "amber" -> Amber
        else -> Primary
    }
}

enum class OrbState { IDLE, LISTENING, THINKING, EXECUTING, LEARNING }

fun orbStateFromName(name: String?): OrbState = when (name) {
    "listening" -> OrbState.LISTENING
    "thinking" -> OrbState.THINKING
    "executing" -> OrbState.EXECUTING
    "learning" -> OrbState.LEARNING
    else -> OrbState.IDLE
}

fun orbColor(state: OrbState): Color = when (state) {
    OrbState.IDLE, OrbState.LISTENING -> VyRxColors.Blue
    OrbState.THINKING -> VyRxColors.PrimaryBright
    OrbState.EXECUTING -> VyRxColors.Green
    OrbState.LEARNING -> VyRxColors.Amber
}

fun orbStateLabel(state: OrbState): String = when (state) {
    OrbState.IDLE -> "Ready"
    OrbState.LISTENING -> "Listening"
    OrbState.THINKING -> "Thinking"
    OrbState.EXECUTING -> "Executing"
    OrbState.LEARNING -> "Learning"
}

@Composable
fun VyRxTheme(
    dark: Boolean = true,
    accent: String = "purple",
    fontScale: Float = 1.0f,
    content: @Composable () -> Unit
) {
    val accentColor = VyRxColors.accentColor(accent)
    val s = fontScale
    val typography = Typography(
        defaultBody = 14f * s,
        displaySmall = 24f * s, displayMedium = 28f * s, displayLarge = 34f * s,
        headlineSmall = 20f * s, headlineMedium = 22f * s, headlineLarge = 26f * s,
        titleSmall = 14f * s, titleMedium = 16f * s, titleLarge = 18f * s,
        bodySmall = 11f * s, bodyMedium = 14f * s, bodyLarge = 15f * s,
        labelSmall = 10f * s, labelMedium = 12f * s, labelLarge = 13f * s
    )
    val scheme = if (dark) {
        androidx.compose.material3.darkColorScheme(
            primary = accentColor,
            onPrimary = Color.White,
            secondary = VyRxColors.Blue,
            background = VyRxColors.Background,
            surface = VyRxColors.Surface,
            onBackground = VyRxColors.TextPrimary,
            onSurface = VyRxColors.TextPrimary,
            error = VyRxColors.Red
        )
    } else {
        androidx.compose.material3.lightColorScheme(
            primary = accentColor,
            onPrimary = Color.White,
            secondary = VyRxColors.BlueDeep,
            background = Color(0xFFF4F5FA),
            surface = Color.White,
            onBackground = Color(0xFF141821),
            onSurface = Color(0xFF141821),
            error = VyRxColors.Red
        )
    }
    MaterialTheme(colorScheme = scheme, typography = typography, content = content)
}

/** Glassmorphism card: translucent fill, 1px stroke, optional neon glow edge. */
@Composable
fun GlassCard(
    modifier: Modifier = Modifier,
    stroke: Color = VyRxColors.CardStroke,
    glow: Color? = null,
    shape: RoundedCornerShape = RoundedCornerShape(20.dp),
    contentPadding: androidx.compose.ui.unit.Dp = 16.dp,
    content: @Composable ColumnScope.() -> Unit
) {
    Box(modifier = modifier) {
        Box(
            Modifier
                .matchParentSize()
                .clip(shape)
                .background(VyRxColors.SurfaceGlass)
                .border(1.dp, stroke, shape)
        )
        if (glow != null) {
            Box(
                Modifier
                    .matchParentSize()
                    .clip(shape)
                    .background(Brush.linearGradient(listOf(glow.copy(alpha = 0.10f), Color.Transparent, Color.Transparent)))
            )
        }
        Column(Modifier.matchParentSize().padding(contentPadding), content = content)
    }
}

/** Wide-tracked uppercase section label. */
@Composable
fun SectionLabel(text: String, color: Color = VyRxColors.TextDim, modifier: Modifier = Modifier) {
    Text(
        text = text.uppercase(),
        modifier = modifier,
        color = color,
        fontSize = 10.sp,
        fontWeight = FontWeight.SemiBold,
        letterSpacing = 1.4.sp
    )
}

@Composable
fun NeonDot(color: Color, size: Int = 8, modifier: Modifier = Modifier) {
    Box(modifier = modifier.size(size.dp)) {
        Box(Modifier.matchParentSize().clip(CircleShape).background(color.copy(alpha = 0.30f)))
        Box(Modifier.align(Alignment.Center).size((size * 0.55).coerceAtLeast(3).dp).clip(CircleShape).background(color))
    }
}

@Composable
fun StatusDot(state: OrbState, modifier: Modifier = Modifier) =
    NeonDot(orbColor(state), modifier = modifier)

@Composable
fun CategoryBadge(category: String, modifier: Modifier = Modifier) {
    val (label, color) = when (category) {
        "personal" -> "Personal" to VyRxColors.PrimaryBright
        "experience" -> "Experience" to VyRxColors.Blue
        "knowledge" -> "Knowledge" to VyRxColors.Green
        else -> category.replaceFirstChar { it.uppercase() } to VyRxColors.TextDim
    }
    Row(
        Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(color.copy(alpha = 0.12f))
            .border(1.dp, color.copy(alpha = 0.45f), RoundedCornerShape(8.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(label, color = color, fontSize = 10.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
fun SectionHeader(title: String, action: String? = null, onAction: (() -> Unit)? = null, modifier: Modifier = Modifier) {
    Row(modifier = modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        SectionLabel(title)
        if (action != null) {
            Text(
                text = action,
                color = VyRxColors.PrimaryBright,
                fontSize = 12.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .padding(if (onAction != null) 4.dp else 0.dp)
                    .let { if (onAction != null) it.clickable(onClick = onAction) else it }
            )
        }
    }
}
