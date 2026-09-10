package com.ronin.ai.ui.chat

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.ui.theme.VyRxColors
import kotlin.math.roundToInt

/**
 * The "Thought Process" terminal: VYRX's live agentic loop rendered as a
 * hacker-style log instead of hidden behind a spinner.
 *
 * It is a collapsible block that sits inside the active chat bubble (and stays
 * attached to the answer afterwards, so Boss can re-read how a task was
 * actually solved). Monospaced, green-phosphor, scanlined — with a phase chip,
 * step counter, elapsed time and a blinking cursor while the loop is running.
 */
@Composable
fun ThoughtTerminal(
    lines: List<ThoughtLine>,
    phase: AgentPhase,
    phaseLabel: String,
    running: Boolean,
    elapsedMs: Int,
    steps: Int,
    expanded: Boolean,
    onToggle: () -> Unit,
    modifier: Modifier = Modifier
) {
    val accent = terminalAccent(phase)
    val shape = RoundedCornerShape(10.dp)
    val visibleLines = if (expanded) MAX_EXPANDED else MAX_COLLAPSED
    val shown = lines.takeLast(visibleLines)
    val hidden = lines.size - shown.size

    Column(
        modifier
            .fillMaxWidth()
            .clip(shape)
            .background(TerminalBackground)
            .border(1.dp, accent.copy(alpha = if (running) 0.55f else 0.28f), shape)
            .drawBehind {
                // Phosphor CRT scanlines.
                var y = 2f
                while (y < size.height) {
                    drawLine(
                        color = Color.White.copy(alpha = 0.025f),
                        start = Offset(0f, y),
                        end = Offset(size.width, y),
                        strokeWidth = 1f
                    )
                    y += 3f
                }
            }
    ) {
        // --- title bar -----------------------------------------------------
        Row(
            Modifier
                .fillMaxWidth()
                .background(TerminalHeader)
                .clickable(onClick = onToggle)
                .padding(start = 10.dp, end = 10.dp, top = 7.dp, bottom = 7.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            TerminalDots(running)
            Spacer(Modifier.width(8.dp))
            Text(
                "THOUGHT_PROCESS.LOG",
                color = VyRxColors.LogGreen,
                fontSize = 9.5.sp,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold,
                letterSpacing = 0.6.sp
            )
            Spacer(Modifier.width(8.dp))
            PhaseChip(phase = phase, running = running)
            Spacer(Modifier.weight(1f))
            Text(
                terminalStatus(steps, elapsedMs, running),
                color = VyRxColors.TextFaint,
                fontSize = 9.sp,
                fontFamily = FontFamily.Monospace
            )
            Spacer(Modifier.width(6.dp))
            Text(
                if (expanded) "▾" else "▴",
                color = VyRxColors.LogGreen,
                fontSize = 10.sp,
                fontFamily = FontFamily.Monospace
            )
        }

        // --- body ----------------------------------------------------------
        if (shown.isEmpty() && !running) return
        Column(
            Modifier
                .fillMaxWidth()
                .padding(start = 10.dp, end = 10.dp, top = 6.dp, bottom = 8.dp)
        ) {
            if (hidden > 0) {
                Text(
                    "… $hidden earlier line${if (hidden == 1) "" else "s"} (tap header to expand)",
                    color = VyRxColors.TextFaint,
                    fontSize = 9.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
            shown.forEachIndexed { index, line ->
                val isLast = index == shown.lastIndex
                TerminalLine(line = line, active = running && isLast, showTime = expanded)
            }
            if (running && shown.isEmpty()) {
                Text(
                    "> booting agent loop…",
                    color = VyRxColors.TextDim,
                    fontSize = 10.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
            if (running) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        "> ",
                        color = accent,
                        fontSize = 10.5.sp,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold
                    )
                    Text(
                        oneLineOf(phaseLabel, 90),
                        color = accent,
                        fontSize = 10.5.sp,
                        fontFamily = FontFamily.Monospace
                    )
                    Spacer(Modifier.width(2.dp))
                    BlockCursor(color = accent)
                }
            }
        }
    }
}

/** One log row: `> [time] glyph message (…) 12ms`. */
@Composable
private fun TerminalLine(line: ThoughtLine, active: Boolean, showTime: Boolean) {
    val color = levelColor(line.level)
    val glyph = levelGlyph(line.level)
    Column(Modifier.fillMaxWidth().padding(vertical = 1.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.Top) {
            Text(
                ">",
                color = color.copy(alpha = 0.65f),
                fontSize = 10.5.sp,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.Bold
            )
            Spacer(Modifier.width(4.dp))
            if (showTime) {
                Text(
                    line.time,
                    color = VyRxColors.LogTime,
                    fontSize = 9.sp,
                    fontFamily = FontFamily.Monospace
                )
                Spacer(Modifier.width(5.dp))
            }
            Text(
                glyph,
                color = color,
                fontSize = 10.sp,
                fontFamily = FontFamily.Monospace
            )
            Spacer(Modifier.width(4.dp))
            Text(
                line.text,
                modifier = Modifier.weight(1f, fill = false),
                color = if (active) color else color.copy(alpha = 0.88f),
                fontSize = 10.5.sp,
                lineHeight = 14.sp,
                fontFamily = FontFamily.Monospace
            )
            if (line.ms > 0) {
                Spacer(Modifier.width(6.dp))
                Text(
                    "${line.ms}ms",
                    color = VyRxColors.TextFaint,
                    fontSize = 9.sp,
                    fontFamily = FontFamily.Monospace
                )
            }
        }
        line.detail?.let { detail ->
            Text(
                "  ${oneLineOf(detail, 160)}",
                color = VyRxColors.TextFaint,
                fontSize = 9.sp,
                lineHeight = 12.sp,
                fontFamily = FontFamily.Monospace,
                modifier = Modifier.padding(start = 9.dp)
            )
        }
    }
}

@Composable
private fun PhaseChip(phase: AgentPhase, running: Boolean) {
    val accent = terminalAccent(phase)
    val pulse by rememberInfiniteTransition(label = "phase").animateFloat(
        initialValue = 0.45f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(700), RepeatMode.Reverse),
        label = "pulse"
    )
    Box(
        Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(accent.copy(alpha = 0.14f))
            .border(1.dp, accent.copy(alpha = 0.45f), RoundedCornerShape(6.dp))
            .padding(horizontal = 6.dp, vertical = 2.dp)
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(5.dp)
                    .clip(CircleShape)
                    .background(accent.copy(alpha = if (running) pulse else 0.9f))
            )
            Spacer(Modifier.width(4.dp))
            Text(
                phase.name.lowercase(),
                color = accent,
                fontSize = 8.5.sp,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 0.8.sp
            )
        }
    }
}

@Composable
private fun TerminalDots(running: Boolean) {
    val pulse by rememberInfiniteTransition(label = "dots").animateFloat(
        initialValue = 0.35f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(tween(900), RepeatMode.Reverse),
        label = "rec"
    )
    Row(horizontalArrangement = Arrangement.spacedBy(3.dp), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(5.dp).clip(CircleShape).background(Color(0xFFF87171).copy(alpha = 0.5f)))
        Box(Modifier.size(5.dp).clip(CircleShape).background(Color(0xFFFBBF24).copy(alpha = 0.5f)))
        Box(
            Modifier
                .size(5.dp)
                .clip(CircleShape)
                .background(Color(0xFF4ADE80).copy(alpha = if (running) pulse else 0.35f))
        )
    }
}

/** Blinking block cursor used for the live line and the typing bubble. */
@Composable
fun BlockCursor(color: Color, modifier: Modifier = Modifier) {
    val transition = rememberInfiniteTransition(label = "cursor")
    val fraction by transition.animateFloat(
        initialValue = 1f,
        targetValue = 0.05f,
        animationSpec = infiniteRepeatable(tween(560), RepeatMode.Reverse),
        label = "cursor-blink"
    )
    Text(
        "▊",
        modifier = modifier.alpha(fraction),
        color = color,
        fontSize = 10.5.sp,
        fontFamily = FontFamily.Monospace
    )
}

/**
 * Format the answer as it is being typed and append a live neon caret.
 *
 * The Brain deliberately sends the final answer as plain text containing common
 * Markdown. Rendering the raw token stream with [Text] makes headings and bold
 * markers visible, so this small dependency-free formatter runs on every
 * revealed snapshot instead. It intentionally covers the Markdown used in chat
 * (headings, lists, quotes, emphasis, code and links) without changing the
 * streaming transport.
 */
@Composable
fun typedAnswer(text: String, streaming: Boolean): AnnotatedString {
    val formatted = remember(text) { markdownAnnotatedString(text) }
    if (!streaming) return formatted

    val transition = rememberInfiniteTransition(label = "type-caret")
    val fraction by transition.animateFloat(
        initialValue = 1f,
        targetValue = 0.15f,
        animationSpec = infiniteRepeatable(tween(480), RepeatMode.Reverse),
        label = "caret"
    )
    return remember(formatted, fraction) {
        AnnotatedString.Builder().apply {
            append(formatted)
            append("▊")
            addStyle(
                SpanStyle(color = VyRxColors.LogGreen.copy(alpha = fraction)),
                formatted.length,
                formatted.length + 1
            )
        }.toAnnotatedString()
    }
}

private fun markdownAnnotatedString(source: String): AnnotatedString {
    val builder = AnnotatedString.Builder()
    val lines = source.replace("\r\n", "\n").replace('\r', '\n').split('\n')
    var inCodeFence = false

    lines.forEachIndexed { index, rawLine ->
        if (index > 0) builder.append('\n')
        val line = rawLine.trimStart()
        if (line.startsWith("```")) {
            inCodeFence = !inCodeFence
            return@forEachIndexed
        }
        if (inCodeFence) {
            appendStyled(builder, SpanStyle(fontFamily = FontFamily.Monospace, color = VyRxColors.TextDim)) { target ->
                target.append(rawLine)
            }
            return@forEachIndexed
        }

        val heading = Regex("^#{1,6}\\s+(.+?)\\s*#*\\s*$").matchEntire(line)
        when {
            heading != null -> appendStyled(
                builder,
                SpanStyle(fontWeight = FontWeight.Bold, color = VyRxColors.PrimaryBright)
            ) { target ->
                appendInlineMarkdown(target, heading.groupValues[1])
            }

            // Do not flash an incomplete heading marker while the next token is
            // still on the wire.
            line.matches(Regex("^#{1,6}\\s*")) -> Unit

            line.matches(Regex("[-*_]{3,}\\s*")) -> appendStyled(
                builder,
                SpanStyle(color = VyRxColors.TextFaint)
            ) { target ->
                target.append("────────")
            }

            line.startsWith("> ") || line == ">" -> appendStyled(
                builder,
                SpanStyle(color = VyRxColors.TextDim)
            ) { target ->
                target.append("│ ")
                appendInlineMarkdown(target, line.removePrefix("> "))
            }

            else -> {
                val bullet = Regex("^[-*+]\\s+(.+)$").matchEntire(line)
                val numbered = Regex("^(\\d+)[.)]\\s+(.+)$").matchEntire(line)
                when {
                    bullet != null -> {
                        builder.append("• ")
                        appendInlineMarkdown(builder, bullet.groupValues[1])
                    }
                    numbered != null -> {
                        builder.append(numbered.groupValues[1])
                        builder.append(". ")
                        appendInlineMarkdown(builder, numbered.groupValues[2])
                    }
                    else -> appendInlineMarkdown(builder, rawLine)
                }
            }
        }
    }
    return builder.toAnnotatedString()
}

private fun appendStyled(
    builder: AnnotatedString.Builder,
    style: SpanStyle,
    content: (AnnotatedString.Builder) -> Unit
) {
    val start = builder.length
    content(builder)
    if (builder.length > start) builder.addStyle(style, start, builder.length)
}

/** Append inline Markdown while keeping the parser deliberately lightweight. */
private fun appendInlineMarkdown(builder: AnnotatedString.Builder, source: String) {
    var cursor = 0
    while (cursor < source.length) {
        // Links are displayed as their label; the URL is not useful in a chat
        // bubble and should never leak through as raw Markdown syntax.
        if (source[cursor] == '[') {
            val closeLabel = source.indexOf("](", cursor + 1)
            val closeUrl = if (closeLabel >= 0) source.indexOf(')', closeLabel + 2) else -1
            if (closeLabel > cursor && closeUrl > closeLabel) {
                appendInlineMarkdown(builder, source.substring(cursor + 1, closeLabel))
                cursor = closeUrl + 1
                continue
            }
        }

        val marker = when {
            source.startsWith("**", cursor) -> "**"
            source.startsWith("__", cursor) -> "__"
            source.startsWith("~~", cursor) -> "~~"
            source[cursor] == '`' -> "`"
            source[cursor] == '*' -> "*"
            source[cursor] == '_' && (cursor == 0 || !source[cursor - 1].isLetterOrDigit()) -> "_"
            else -> null
        }
        if (marker == null) {
            builder.append(source[cursor])
            cursor++
            continue
        }

        val close = source.indexOf(marker, cursor + marker.length)
        if (close <= cursor + marker.length) {
            // An unfinished stream chunk may contain only an opening marker.
            // Hide that marker for now; the next snapshot will format it once
            // its closing marker arrives.
            cursor += marker.length
            continue
        }

        val inner = source.substring(cursor + marker.length, close)
        val style = when (marker) {
            "**", "__" -> SpanStyle(fontWeight = FontWeight.Bold)
            "*", "_" -> SpanStyle(fontStyle = FontStyle.Italic)
            "~~" -> SpanStyle(textDecoration = TextDecoration.LineThrough)
            else -> SpanStyle(
                fontFamily = FontFamily.Monospace,
                color = VyRxColors.PrimaryBright,
                background = Color(0x221E293B)
            )
        }
        appendStyled(builder, style) { target ->
            if (marker == "`") target.append(inner) else appendInlineMarkdown(target, inner)
        }
        cursor = close + marker.length
    }
}

@Composable
private fun terminalAccent(phase: AgentPhase): Color = when (phase) {
    AgentPhase.PLANNING, AgentPhase.REASONING -> VyRxColors.PrimaryBright
    AgentPhase.ACTING -> VyRxColors.Blue
    AgentPhase.OBSERVING -> VyRxColors.LogGreen
    AgentPhase.CORRECTING -> VyRxColors.Amber
    AgentPhase.TYPING -> VyRxColors.LogGreen
    AgentPhase.DONE -> VyRxColors.LogGreen
    AgentPhase.IDLE -> VyRxColors.TextDim
}

private fun levelColor(level: String): Color = when (level) {
    ThoughtLine.LEVEL_PLAN -> VyRxColors.PrimaryBright
    ThoughtLine.LEVEL_THINK -> Color(0xFF9FB4D6)
    ThoughtLine.LEVEL_CALL -> VyRxColors.Blue
    ThoughtLine.LEVEL_OK -> VyRxColors.LogGreen
    ThoughtLine.LEVEL_FAIL -> VyRxColors.Red
    ThoughtLine.LEVEL_FIX -> VyRxColors.Amber
    ThoughtLine.LEVEL_ANSWER -> VyRxColors.LogGreen
    else -> VyRxColors.TextDim
}

private fun levelGlyph(level: String): String = when (level) {
    ThoughtLine.LEVEL_PLAN -> "◇"
    ThoughtLine.LEVEL_THINK -> "~"
    ThoughtLine.LEVEL_CALL -> "▶"
    ThoughtLine.LEVEL_OK -> "✔"
    ThoughtLine.LEVEL_FAIL -> "✖"
    ThoughtLine.LEVEL_FIX -> "↺"
    ThoughtLine.LEVEL_ANSWER -> "◆"
    else -> "·"
}

private fun terminalStatus(steps: Int, elapsedMs: Int, running: Boolean): String {
    val seconds = (elapsedMs / 100f).roundToInt() / 10f
    val clock = if (elapsedMs <= 0) "0.0s" else "%.1fs".format(seconds)
    return "${if (running) "●" else "○"} $steps step${if (steps == 1) "" else "s"} · $clock"
}

private fun oneLineOf(text: String, limit: Int): String {
    val flat = text.replace('\n', ' ').replace('\r', ' ').trim()
    return if (flat.length <= limit) flat else flat.take(limit - 1) + "…"
}

private val TerminalBackground = Color(0xFF03070F)
private val TerminalHeader = Color(0xFF0A121D)
private const val MAX_COLLAPSED = 3
private const val MAX_EXPANDED = 24
