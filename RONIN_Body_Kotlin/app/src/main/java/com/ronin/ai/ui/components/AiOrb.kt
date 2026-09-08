package com.ronin.ai.ui.components

import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.rotate
import androidx.compose.ui.unit.dp
import com.ronin.ai.ui.theme.OrbState
import com.ronin.ai.ui.theme.VyRxColors
import com.ronin.ai.ui.theme.orbColor
import kotlin.math.cos
import kotlin.math.min
import kotlin.math.sin

private fun fract(v: Float) = v - Math.floor(v.toDouble()).toFloat()

/**
 * The VYRX AI orb: radial-gradient sphere, rotating particle field,
 * pulsing ring. Color follows the live brain state.
 */
@Composable
fun AiOrb(state: OrbState, modifier: Modifier = Modifier) {
    val color = orbColor(state)
    val speedFactor = when (state) {
        OrbState.THINKING -> 0.45f
        OrbState.EXECUTING -> 0.35f
        OrbState.LEARNING -> 0.7f
        else -> 1f
    }
    val transition = rememberInfiniteTransition()
    val pulse by transition.animateFloat(
        initialValue = 0f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween((1800 * speedFactor).toInt(), FastOutSlowInEasing), RepeatMode.Reverse)
    )
    val rotation by transition.animateFloat(
        initialValue = 0f, targetValue = 360f,
        animationSpec = infiniteRepeatable(tween((9000 * speedFactor).toInt()), RepeatMode.Restart)
    )
    Canvas(modifier = modifier) {
        val size = min(size.width, size.height)
        val center = androidx.compose.ui.geometry.Offset(size / 2f, size / 2f)
        val radius = size * 0.40f * (1f + 0.025f * pulse)

        // outer glow
        drawCircle(
            Brush.radialGradient(
                listOf(color.copy(alpha = 0.28f * (0.6f + 0.4f * pulse)), Color.Transparent),
                center, radius * 1.55f
            )
        )
        // sphere body
        drawCircle(
            Brush.radialGradient(
                listOf(color.copy(alpha = 0.85f), color.copy(alpha = 0.30f), VyRxColors.Background.copy(alpha = 0.9f)),
                center, radius
            )
        )
        // bright core
        drawCircle(
            Brush.radialGradient(
                listOf(Color.White.copy(alpha = 0.35f + 0.30f * pulse), color.copy(alpha = 0.25f), Color.Transparent),
                center, radius * 0.55f
            )
        )
        // particle field (deterministic golden-angle distribution, counter-rotating shells)
        for (i in 0 until 46) {
            val shell = (i % 4)
            val angle = (i * 2.39996f) + (rotation * (0.5f + shell * 0.14f)).let { Math.toRadians(it.toDouble()).toFloat() }
            val dist = radius * (0.42f + 0.52f * fract(sin(i * 12.9898) * 43758.5453f))
            val x = center.x + cos(angle) * dist
            val y = center.y + sin(angle) * dist * 0.94f
            val twinkle = 0.20f + 0.80f * fract(sin(i * 78.233f) * 43758.5453f + rotation / 40f)
            drawCircle(
                if (i % 9 == 0) Color.White else color,
                radius = size * 0.0045f,
                center = androidx.compose.ui.geometry.Offset(x, y),
                alpha = twinkle
            )
        }
        // rim
        drawCircle(color.copy(alpha = 0.55f + 0.30f * pulse), radius, center, style = Stroke(size * 0.014f))
        // inner energy ring
        rotate((rotation * 0.6f).toFloat()) {
            drawCircle(color.copy(alpha = 0.16f + 0.12f * pulse), radius * 0.72f, center, style = Stroke(size * 0.006f))
        }
    }
}

/** Compact glowing orb with a heartbeat trace — the center bottom-nav button. */
@Composable
fun HeartbeatOrb(state: OrbState, modifier: Modifier = Modifier, sizeDp: Int = 64) {
    val color = orbColor(state)
    val transition = rememberInfiniteTransition()
    val pulse by transition.animateFloat(
        initialValue = 0f, targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(1400), RepeatMode.Reverse)
    )
    val dash by transition.animateFloat(
        initialValue = 0f, targetValue = 200f,
        animationSpec = infiniteRepeatable(tween(1600), RepeatMode.Restart)
    )
    Box(
        modifier = modifier
            .size(sizeDp.dp)
            .clip(CircleShape)
            .background(
                Brush.radialGradient(
                    listOf(color.copy(alpha = 0.85f), VyRxColors.PrimaryDim.copy(alpha = 0.8f), VyRxColors.Background)
                )
            )
            .border(1.5.dp, color.copy(alpha = 0.5f + 0.3f * pulse), CircleShape)
    ) {
        Canvas(Modifier.matchParentSize()) {
            val w = size.width
            val mid = size.height / 2f
            val phase = (dash / 200f * 360f).let { Math.toRadians(it.toDouble()).toFloat() }
            val step = w / 24f
            for (i in 0 until 24) {
                val x = i * step
                val local = sin((i / 24f * 2f * Math.PI) + phase)
                val spike = if (i in 10..13) sin((i - 10f) / 3f * Math.PI) * 0.42f else local * 0.08f
                val y = mid + spike * size.height * 0.35f
                if (i > 0) {
                    val px = (i - 1) * step
                    val pl = sin(((i - 1) / 24f * 2f * Math.PI) + phase)
                    val pspike = if (i - 1 in 10..13) sin(((i - 1) - 10f) / 3f * Math.PI) * 0.42f else pl * 0.08f
                    val py = mid + pspike * size.height * 0.35f
                    drawLine(Color.White.copy(alpha = 0.85f), androidx.compose.ui.geometry.Offset(px, py), androidx.compose.ui.geometry.Offset(x, y), 2.2f)
                }
            }
        }
        Box(
            Modifier
                .matchParentSize()
                .background(
                    Brush.radialGradient(
                        listOf(Color.Transparent, VyRxColors.Background.copy(alpha = 0.25f * pulse))
                    )
                )
        )
    }
}

/** Large tappable center button wrapping [HeartbeatOrb] with a glow halo. */
@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
fun CenterOrbButton(
    state: OrbState,
    onTap: () -> Unit,
    onLongPress: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(
        modifier = modifier
            .size(72.dp)
            .clip(CircleShape)
            .combinedClickable(onClick = onTap, onLongClick = onLongPress)
            .background(Brush.radialGradient(listOf(VyRxColors.Primary.copy(alpha = 0.28f), Color.Transparent)))
    ) {
        HeartbeatOrb(state, Modifier.align(Alignment.Center).size(56.dp))
    }
}
