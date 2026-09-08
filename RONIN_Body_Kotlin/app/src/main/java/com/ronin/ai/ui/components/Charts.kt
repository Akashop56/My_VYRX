package com.ronin.ai.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.network.TaskPoint
import com.ronin.ai.ui.theme.VyRxColors

/** Circular gauge (memory/RAM ring). */
@Composable
fun RingProgress(value: Float, color: Color, modifier: Modifier, label: String = "", sublabel: String = "") {
    Box(modifier, contentAlignment = Alignment.Center) {
        Canvas(Modifier.matchParentSize()) {
            val sw = size.minDimension * 0.09f
            val inset = sw / 2f
            val arc = androidx.compose.ui.geometry.Size(size.width - sw, size.height - sw)
            drawArc(
                color = color.copy(alpha = 0.15f),
                startAngle = 0f, sweepAngle = 360f, useCenter = false,
                style = Stroke(sw), size = arc, topLeft = Offset(inset, inset)
            )
            drawArc(
                color = color,
                startAngle = -90f, sweepAngle = 360f * value.coerceIn(0f, 1f), useCenter = false,
                style = Stroke(width = sw, cap = androidx.compose.ui.graphics.StrokeCap.Round), size = arc, topLeft = Offset(inset, inset)
            )
        }
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Text("${(value * 100).toInt()}%", color = VyRxColors.TextPrimary, fontSize = 13.sp, fontWeight = FontWeight.Bold)
            Text(label, color = VyRxColors.TextDim, fontSize = 9.sp)
            if (sublabel.isNotBlank()) Text(sublabel, color = VyRxColors.TextFaint, fontSize = 8.sp)
        }
    }
}

/** Horizontal rounded progress bar. */
@Composable
fun LinearBar(value: Float, color: Color, modifier: Modifier = Modifier) {
    Box(
        modifier
            .clip(RoundedCornerShape(4.dp))
            .background(VyRxColors.CardStroke.copy(alpha = 0.5f))
    ) {
        Box(
            Modifier
                .fillMaxWidth(value.coerceIn(0f, 1f))
                .height(6.dp)
                .clip(RoundedCornerShape(4.dp))
                .background(Brush.linearGradient(listOf(color.copy(alpha = 0.7f), color)))
        )
    }
}

/** 7-day tasks bar chart (purple gradient bars, last bar highlighted). */
@Composable
fun TaskBarChart(points: List<TaskPoint>, modifier: Modifier = Modifier) {
    if (points.isEmpty()) return
    val max = (points.maxOf { it.tasks }.coerceAtLeast(1))
    Column(modifier) {
        Box(Modifier.fillMaxWidth().weight(1f).padding(bottom = 6.dp), contentAlignment = Alignment.BottomStart) {
            androidx.compose.foundation.layout.Row(Modifier.fillMaxWidth().height(120.dp), horizontalArrangement = Arrangement.SpaceEvenly, verticalAlignment = Alignment.Bottom) {
                points.forEachIndexed { index, point ->
                    val isLast = index == points.size - 1
                    val barColor = if (isLast) VyRxColors.Blue else VyRxColors.Primary
                    Column(
                        Modifier.weight(1f).height(120.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.Bottom
                    ) {
                        if (isLast && point.tasks > 0) {
                            Text("${point.tasks}", color = VyRxColors.Blue, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                        }
                        Box(
                            Modifier
                                .width(14.dp)
                                .height((40.dp * (point.tasks.toFloat() / max.toFloat())).coerceAtLeast(if (point.tasks > 0) 6.dp else 2.dp))
                                .clip(RoundedCornerShape(topStart = 4.dp, topEnd = 4.dp))
                                .background(
                                    if (isLast) Brush.linearGradient(listOf(VyRxColors.Blue, VyRxColors.BlueDeep.copy(alpha = 0.6f)))
                                    else Brush.linearGradient(listOf(barColor.copy(alpha = 0.9f), barColor.copy(alpha = 0.35f)))
                                )
                        )
                    }
                }
            }
        }
        androidx.compose.foundation.layout.Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            points.forEach { point ->
                Text(
                    point.label,
                    color = if (point.label == "Today") VyRxColors.TextPrimary else VyRxColors.TextFaint,
                    fontSize = 9.sp,
                    modifier = Modifier.weight(1f),
                    textAlign = TextAlign.Center
                )
            }
        }
    }
}

/** CPU waveform sparkline from real sampled history. */
@Composable
fun Waveform(values: List<Float>, color: Color, modifier: Modifier = Modifier) {
    Canvas(modifier) {
        if (values.size < 2) {
            // draw a flat baseline while history is filling in
            drawLine(color.copy(alpha = 0.5f), Offset(size.width * 0.1f, size.height / 2), Offset(size.width * 0.9f, size.height / 2), 2f)
            return@Canvas
        }
        val step = size.width / (values.size - 1)
        val maxV = 100f
        for (i in 1 until values.size) {
            val x0 = (i - 1) * step
            val x1 = i * step
            val y0 = size.height - (values[i - 1] / maxV) * size.height * 0.85f
            val y1 = size.height - (values[i] / maxV) * size.height * 0.85f
            drawLine(color.copy(alpha = 0.9f), Offset(x0, y0), Offset(x1, y1), 2f)
        }
        // soft fill under the line
        val path = androidx.compose.ui.graphics.Path()
        path.moveTo(0f, size.height)
        values.forEachIndexed { i, v ->
            path.lineTo(i * step, size.height - (v / maxV) * size.height * 0.85f)
        }
        path.lineTo(size.width, size.height)
        path.close()
        drawPath(path, color.copy(alpha = 0.12f))
    }
}
