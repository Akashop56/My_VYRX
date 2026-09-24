package com.ronin.ai.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.ui.theme.VyRxColors

private const val MAX_VISIBLE_CAPABILITY_ACTIVITIES = 6

/**
 * Compact, inline rendering of the already-validated capability activity stream.
 *
 * This component is deliberately presentation-only: it consumes the structured
 * CapabilityActivity fields and never derives locality, retrieval, health, or
 * recovery from a tool name, network state, or display text.
 */
@Composable
fun CapabilityActivityStrip(
    events: List<CapabilityActivity>,
    modifier: Modifier = Modifier
) {
    val visible = capabilityActivityRenderItems(events)
    if (visible.isEmpty()) return

    val shape = RoundedCornerShape(10.dp)
    Column(
        modifier
            .fillMaxWidth()
            .clip(shape)
            .background(VyRxColors.Surface.copy(alpha = 0.72f))
            .border(1.dp, VyRxColors.CardStrokeSoft, shape)
            .padding(horizontal = 10.dp, vertical = 8.dp)
    ) {
        Text(
            text = "CAPABILITY ACTIVITY",
            color = VyRxColors.LogGreen,
            fontSize = 9.sp,
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold,
            letterSpacing = 0.55.sp
        )
        Spacer(Modifier.width(1.dp))
        visible.forEach { activity ->
            CapabilityActivityRow(activity)
        }
    }
}

@Composable
private fun CapabilityActivityRow(activity: CapabilityActivity) {
    Column(
        Modifier
            .fillMaxWidth()
            .padding(top = 5.dp)
    ) {
        Row(
            Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text(
                text = capabilityActivityLabel(activity),
                color = activityStateColor(activity.state),
                fontSize = 10.5.sp,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.SemiBold
            )
            if (activity.sequence > 0) {
                Text(
                    text = "#${activity.sequence}",
                    color = VyRxColors.TextFaint,
                    fontSize = 9.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
        }

        capabilityActivityDetails(activity).forEach { detail ->
            Text(
                text = "  $detail",
                color = VyRxColors.TextDim,
                fontSize = 9.5.sp,
                lineHeight = 12.sp,
                fontFamily = FontFamily.Monospace
            )
        }

        capabilityActivityCitationLabels(activity).forEach { citation ->
            Text(
                text = "  ↳ $citation",
                color = VyRxColors.TextFaint,
                fontSize = 9.sp,
                lineHeight = 12.sp,
                fontFamily = FontFamily.Monospace
            )
        }
    }
}

/** Keeps the latest ordered events without inventing a replacement for absent data. */
internal fun capabilityActivityRenderItems(
    events: List<CapabilityActivity>
): List<CapabilityActivity> = events.takeLast(MAX_VISIBLE_CAPABILITY_ACTIVITIES)

internal fun capabilityActivityLabel(activity: CapabilityActivity): String =
    "${activity.capability.label} • ${activity.state.label}"

/** Returns only fields that were transported as structured optional values. */
internal fun capabilityActivityDetails(activity: CapabilityActivity): List<String> = buildList {
    activity.location?.let { add(it.label) }
    activity.health?.let { add(it.label) }
    activity.implementation?.takeIf { it.isNotBlank() }?.let { add(it) }
    activity.retrieval?.let { add("Retrieval: ${it.label}") }
    activity.recovery?.let { recovery ->
        add("Recovery: ${recovery.summary.label}")
        if (recovery.sequence > 0) add("Recovery sequence: ${recovery.sequence}")
    }
}

/** Citations are rendered only after CapabilityPresentationAdapter validation. */
internal fun capabilityActivityCitationLabels(activity: CapabilityActivity): List<String> =
    activity.citations.map { citation ->
        buildString {
            append(citation.source)
            append(" • ")
            append(citation.locationLabel)
            citation.retrieval?.let { append(" • Retrieval: ").append(it.label) }
        }
    }

private fun activityStateColor(state: CapabilityActivityState): Color = when (state) {
    CapabilityActivityState.ACTIVE -> VyRxColors.Blue
    CapabilityActivityState.SUCCESS -> VyRxColors.Green
    CapabilityActivityState.PARTIAL -> VyRxColors.Amber
    CapabilityActivityState.FAILED -> VyRxColors.Red
}
