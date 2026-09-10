package com.ronin.ai.ui.chat

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.widget.Toast
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.ronin.ai.ui.theme.VyRxColors

// ---------------------------------------------------------------------------
// Chat markdown renderer.
//
// The Brain streams the final answer as plain text containing common Markdown.
// Rendering that token stream with a single flat Text() shows raw `#`, `**`
// and `-` markers and crams everything into one cramped paragraph.
//
// This renderer parses every revealed snapshot into styled blocks (headings,
// paragraphs, lists, quotes, dividers, fenced code) with professional
// typography and spacing, so the answer looks premium *while* it types.
//
// Streaming tolerance: partial snapshots (an opening ``` or ** whose closing
// marker has not arrived yet) render as styled content with the marker hidden,
// never as raw syntax.
// ---------------------------------------------------------------------------

/**
 * Renders one AI answer with full Markdown styling.
 *
 * @param text the currently revealed answer snapshot (grows while streaming).
 * @param streaming true while the typewriter is still revealing tokens; shows
 * a blinking caret inline at the end of the last block.
 */
@Composable
fun ChatMarkdownText(
    text: String,
    streaming: Boolean,
    modifier: Modifier = Modifier
) {
    if (text.isBlank()) return
    val blocks = remember(text) { parseMarkdownBlocks(text) }
    if (blocks.isEmpty()) return

    val transition = rememberInfiniteTransition(label = "markdown-caret")
    val pulse by transition.animateFloat(
        initialValue = 1f,
        targetValue = 0.15f,
        animationSpec = infiniteRepeatable(tween(480), RepeatMode.Reverse),
        label = "caret-alpha"
    )

    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(7.dp)
    ) {
        blocks.forEachIndexed { index, block ->
            MarkdownBlock(
                block = block,
                isFirst = index == 0,
                showCaret = streaming && index == blocks.lastIndex,
                caretAlpha = pulse
            )
        }
        // A divider cannot host an inline caret, so the live cursor gets its
        // own line when the stream currently ends on one.
        if (streaming && blocks.last() is MarkdownBlock.Divider) {
            Text(
                caretString(pulse),
                color = VyRxColors.LogGreen,
                fontSize = 11.sp,
                fontFamily = FontFamily.Monospace
            )
        }
    }
}

// ---------------------------------------------------------------------------
// Block model
// ---------------------------------------------------------------------------

private sealed interface MarkdownBlock {
    data class Heading(val level: Int, val text: String) : MarkdownBlock
    data class Paragraph(val text: String) : MarkdownBlock
    data class BulletList(val items: List<MarkdownListItem>) : MarkdownBlock
    data class OrderedList(val items: List<MarkdownListItem>) : MarkdownBlock
    data class CodeBlock(val language: String, val code: String) : MarkdownBlock
    data class Quote(val text: String) : MarkdownBlock
    data object Divider : MarkdownBlock
}

private data class MarkdownListItem(
    val text: String,
    val indent: Int,
    val number: Int = 0
)

// ---------------------------------------------------------------------------
// Block parser (streaming tolerant)
// ---------------------------------------------------------------------------

private val HeadingPattern = Regex("^(#{1,6})\\s*(.*)$")
private val DividerPattern = Regex("^([-*_])(\\s*\\1){2,}\\s*$")
private val BulletPattern = Regex("^(\\s*)[-*+]\\s+(.+)$")
private val OrderedPattern = Regex("^(\\s*)(\\d+)[.)]\\s+(.+)$")
private val BareBulletPattern = Regex("^[-*+]\\s*$")
private val BareOrderedPattern = Regex("^\\d+[.)]\\s*$")
private val BareHeadingPattern = Regex("^#{1,6}\\s*$")

private fun parseMarkdownBlocks(source: String): List<MarkdownBlock> {
    val lines = source.replace("\r\n", "\n").replace('\r', '\n').split('\n')
    val blocks = mutableListOf<MarkdownBlock>()
    val paragraph = mutableListOf<String>()

    fun flushParagraph() {
        if (paragraph.isEmpty()) return
        val text = paragraph.joinToString("\n").trim()
        if (text.isNotEmpty()) blocks.add(MarkdownBlock.Paragraph(text))
        paragraph.clear()
    }

    var i = 0
    while (i < lines.size) {
        val raw = lines[i]
        val trimmed = raw.trim()
        val stripped = raw.trimStart()

        // --- fenced code block -------------------------------------------
        if (stripped.startsWith("```")) {
            flushParagraph()
            val language = stripped.removePrefix("```").trim()
                .split(Regex("\\s+")).firstOrNull().orEmpty()
            val code = mutableListOf<String>()
            i++
            while (i < lines.size && !lines[i].trimStart().startsWith("```")) {
                code.add(lines[i].trimEnd())
                i++
            }
            // Consume the closing fence when it has already streamed in; an
            // unclosed fence simply renders everything so far as code.
            if (i < lines.size) i++
            while (code.isNotEmpty() && code.first().isBlank()) code.removeAt(0)
            while (code.isNotEmpty() && code.last().isBlank()) code.removeLast()
            blocks.add(MarkdownBlock.CodeBlock(language, code.joinToString("\n")))
            continue
        }

        // --- blank line = paragraph break ---------------------------------
        if (trimmed.isEmpty()) {
            flushParagraph()
            i++
            continue
        }

        // --- horizontal rule (before bullets: "***" is not a list) --------
        if (DividerPattern.matches(trimmed)) {
            flushParagraph()
            blocks.add(MarkdownBlock.Divider)
            i++
            continue
        }

        // --- headings ------------------------------------------------------
        if (trimmed.startsWith("#")) {
            // A lone "###" mid-stream is a partial marker, not content.
            if (BareHeadingPattern.matches(trimmed)) {
                i++
                continue
            }
            val match = HeadingPattern.matchEntire(trimmed)
            if (match != null) {
                val hashes = match.groupValues[1]
                val afterHashes = trimmed.substring(hashes.length)
                // "#1 priority" / hashtags are not headings.
                if (afterHashes.isNotEmpty() && afterHashes[0].isWhitespace()) {
                    flushParagraph()
                    val title = match.groupValues[2].trim().trimEnd('#').trim()
                    if (title.isNotEmpty()) {
                        blocks.add(MarkdownBlock.Heading(hashes.length, title))
                    }
                    i++
                    continue
                }
            }
        }

        // --- quotes (gather consecutive lines) -----------------------------
        if (stripped.startsWith(">")) {
            flushParagraph()
            val quoted = mutableListOf<String>()
            while (i < lines.size && lines[i].trimStart().startsWith(">")) {
                quoted.add(lines[i].trimStart().removePrefix(">").trimStart())
                i++
            }
            val text = quoted.joinToString("\n").trim()
            if (text.isNotEmpty()) blocks.add(MarkdownBlock.Quote(text))
            continue
        }

        // --- dangling list markers still waiting for their text ------------
        if (BareBulletPattern.matches(trimmed) || BareOrderedPattern.matches(trimmed)) {
            i++
            continue
        }

        // --- bullet lists (gather consecutive items) -----------------------
        if (BulletPattern.matches(raw.replace("\t", "  "))) {
            flushParagraph()
            val items = mutableListOf<MarkdownListItem>()
            while (i < lines.size) {
                val match = BulletPattern.matchEntire(lines[i].replace("\t", "  ")) ?: break
                val indent = (match.groupValues[1].length / 2).coerceIn(0, 4)
                items.add(MarkdownListItem(match.groupValues[2].trim(), indent))
                i++
            }
            if (items.isNotEmpty()) blocks.add(MarkdownBlock.BulletList(items))
            continue
        }

        // --- ordered lists (gather consecutive items) ----------------------
        if (OrderedPattern.matches(raw.replace("\t", "  "))) {
            flushParagraph()
            val items = mutableListOf<MarkdownListItem>()
            while (i < lines.size) {
                val match = OrderedPattern.matchEntire(lines[i].replace("\t", "  ")) ?: break
                val indent = (match.groupValues[1].length / 2).coerceIn(0, 4)
                items.add(
                    MarkdownListItem(
                        text = match.groupValues[3].trim(),
                        indent = indent,
                        number = match.groupValues[2].toIntOrNull() ?: 0
                    )
                )
                i++
            }
            if (items.isNotEmpty()) blocks.add(MarkdownBlock.OrderedList(items))
            continue
        }

        // --- plain paragraph line (single \n stays a line break) -----------
        paragraph.add(raw.trim())
        i++
    }
    flushParagraph()
    return blocks
}

// ---------------------------------------------------------------------------
// Block views
// ---------------------------------------------------------------------------

@Composable
private fun MarkdownBlock(
    block: MarkdownBlock,
    isFirst: Boolean,
    showCaret: Boolean,
    caretAlpha: Float
) {
    when (block) {
        is MarkdownBlock.Heading -> MarkdownHeading(block, isFirst, showCaret, caretAlpha)
        is MarkdownBlock.Paragraph -> MarkdownParagraph(block, showCaret, caretAlpha)
        is MarkdownBlock.BulletList -> MarkdownBulletList(block, showCaret, caretAlpha)
        is MarkdownBlock.OrderedList -> MarkdownOrderedList(block, showCaret, caretAlpha)
        is MarkdownBlock.CodeBlock -> MarkdownCodeBlock(block, showCaret, caretAlpha)
        is MarkdownBlock.Quote -> MarkdownQuote(block, showCaret, caretAlpha)
        MarkdownBlock.Divider -> MarkdownDivider()
    }
}

@Composable
private fun MarkdownHeading(
    block: MarkdownBlock.Heading,
    isFirst: Boolean,
    showCaret: Boolean,
    caretAlpha: Float
) {
    val text = remember(block.text, showCaret, caretAlpha) {
        inlineMarkdown(block.text).let { if (showCaret) it + caretString(caretAlpha) else it }
    }
    val (size, height) = when (block.level) {
        1 -> 20.sp to 27.sp
        2 -> 18.sp to 25.sp
        3 -> 16.sp to 23.sp
        4 -> 15.sp to 22.sp
        else -> 14.sp to 21.sp
    }
    Text(
        text = text,
        modifier = Modifier.padding(top = if (isFirst) 2.dp else 8.dp),
        color = VyRxColors.TextPrimary,
        fontSize = size,
        lineHeight = height,
        fontWeight = FontWeight.Bold,
        letterSpacing = (-0.1).sp
    )
}

@Composable
private fun MarkdownParagraph(
    block: MarkdownBlock.Paragraph,
    showCaret: Boolean,
    caretAlpha: Float
) {
    val text = remember(block.text, showCaret, caretAlpha) {
        inlineMarkdown(block.text).let { if (showCaret) it + caretString(caretAlpha) else it }
    }
    Text(
        text = text,
        color = VyRxColors.TextPrimary,
        fontSize = 14.sp,
        lineHeight = 21.sp
    )
}

@Composable
private fun MarkdownBulletList(
    block: MarkdownBlock.BulletList,
    showCaret: Boolean,
    caretAlpha: Float
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        block.items.forEachIndexed { index, item ->
            val isLast = index == block.items.lastIndex
            val text = remember(item.text, showCaret, isLast, caretAlpha) {
                inlineMarkdown(item.text).let {
                    if (showCaret && isLast) it + caretString(caretAlpha) else it
                }
            }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 4.dp + (item.indent * 16).dp),
                verticalAlignment = Alignment.Top
            ) {
                Text(
                    text = when (item.indent % 3) {
                        1 -> "◦"
                        2 -> "–"
                        else -> "•"
                    },
                    color = VyRxColors.PrimaryBright,
                    fontSize = 14.sp,
                    lineHeight = 20.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(end = 8.dp)
                )
                Text(
                    text = text,
                    color = VyRxColors.TextPrimary,
                    fontSize = 14.sp,
                    lineHeight = 20.sp,
                    modifier = Modifier.weight(1f, fill = false)
                )
            }
        }
    }
}

@Composable
private fun MarkdownOrderedList(
    block: MarkdownBlock.OrderedList,
    showCaret: Boolean,
    caretAlpha: Float
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        block.items.forEachIndexed { index, item ->
            val isLast = index == block.items.lastIndex
            val text = remember(item.text, showCaret, isLast, caretAlpha) {
                inlineMarkdown(item.text).let {
                    if (showCaret && isLast) it + caretString(caretAlpha) else it
                }
            }
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(start = 4.dp + (item.indent * 16).dp),
                verticalAlignment = Alignment.Top
            ) {
                Text(
                    text = "${if (item.number > 0) item.number else index + 1}.",
                    color = VyRxColors.PrimaryBright,
                    fontSize = 14.sp,
                    lineHeight = 20.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.width(22.dp)
                )
                Text(
                    text = text,
                    color = VyRxColors.TextPrimary,
                    fontSize = 14.sp,
                    lineHeight = 20.sp,
                    modifier = Modifier.weight(1f, fill = false)
                )
            }
        }
    }
}

@Composable
private fun MarkdownQuote(
    block: MarkdownBlock.Quote,
    showCaret: Boolean,
    caretAlpha: Float
) {
    val text = remember(block.text, showCaret, caretAlpha) {
        inlineMarkdown(block.text).let { if (showCaret) it + caretString(caretAlpha) else it }
    }
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .height(IntrinsicSize.Min)
            .clip(RoundedCornerShape(8.dp))
            .background(Color(0x14A78BFA))
    ) {
        Box(
            modifier = Modifier
                .fillMaxHeight()
                .width(3.dp)
                .background(VyRxColors.Primary)
        )
        Text(
            text = text,
            color = VyRxColors.TextDim,
            fontSize = 13.5.sp,
            lineHeight = 20.sp,
            fontStyle = FontStyle.Italic,
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp)
        )
    }
}

@Composable
private fun MarkdownDivider() {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        contentAlignment = Alignment.Center
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(1.dp)
                .background(VyRxColors.CardStroke.copy(alpha = 0.8f))
        )
    }
}

@Composable
private fun MarkdownCodeBlock(
    block: MarkdownBlock.CodeBlock,
    showCaret: Boolean,
    caretAlpha: Float
) {
    val context = LocalContext.current
    val shape = RoundedCornerShape(12.dp)
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(CodeBlockBackground)
            .border(1.dp, CodeBlockStroke, shape)
    ) {
        // Header: language label + copy.
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .background(CodeBlockHeader)
                .padding(start = 12.dp, end = 6.dp, top = 5.dp, bottom = 5.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            CodeDots()
            Spacer(Modifier.width(8.dp))
            Text(
                text = block.language.ifBlank { "code" }.uppercase(),
                color = VyRxColors.TextFaint,
                fontSize = 10.sp,
                fontFamily = FontFamily.Monospace,
                fontWeight = FontWeight.SemiBold,
                letterSpacing = 0.8.sp
            )
            Spacer(Modifier.weight(1f))
            Row(
                modifier = Modifier
                    .clip(RoundedCornerShape(6.dp))
                    .clickable {
                        val clipboard =
                            context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        clipboard.setPrimaryClip(ClipData.newPlainText("code", block.code))
                        Toast.makeText(context, "Code copied", Toast.LENGTH_SHORT).show()
                    }
                    .padding(horizontal = 8.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                Icon(
                    Icons.Filled.ContentCopy,
                    contentDescription = "Copy code",
                    tint = VyRxColors.TextDim,
                    modifier = Modifier.size(12.dp)
                )
                Spacer(Modifier.width(4.dp))
                Text("Copy", color = VyRxColors.TextDim, fontSize = 10.sp)
            }
        }
        val code = remember(block.code, showCaret, caretAlpha) {
            AnnotatedString.Builder().apply {
                append(block.code)
                if (showCaret) {
                    val start = length
                    append("▊")
                    addStyle(
                        SpanStyle(color = VyRxColors.LogGreen.copy(alpha = caretAlpha)),
                        start,
                        length
                    )
                }
            }.toAnnotatedString()
        }
        Text(
            text = code,
            color = CodeText,
            fontSize = 12.5.sp,
            lineHeight = 18.sp,
            fontFamily = FontFamily.Monospace,
            softWrap = false,
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(12.dp)
        )
    }
}

@Composable
private fun CodeDots() {
    Row(
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(Color(0xFFF87171).copy(alpha = 0.55f)))
        Box(Modifier.size(7.dp).clip(CircleShape).background(Color(0xFFFBBF24).copy(alpha = 0.55f)))
        Box(Modifier.size(7.dp).clip(CircleShape).background(Color(0xFF4ADE80).copy(alpha = 0.55f)))
    }
}

// ---------------------------------------------------------------------------
// Inline Markdown (**bold**, *italic*, `code`, links, …)
// ---------------------------------------------------------------------------

private fun inlineMarkdown(source: String): AnnotatedString {
    val builder = AnnotatedString.Builder()
    appendInline(builder, source)
    return builder.toAnnotatedString()
}

private fun caretString(alpha: Float): AnnotatedString =
    AnnotatedString.Builder().apply {
        append("▊")
        addStyle(SpanStyle(color = VyRxColors.LogGreen.copy(alpha = alpha)), 0, 1)
    }.toAnnotatedString()

private fun appendStyled(
    builder: AnnotatedString.Builder,
    style: SpanStyle,
    content: (AnnotatedString.Builder) -> Unit
) {
    val start = builder.length
    content(builder)
    if (builder.length > start) builder.addStyle(style, start, builder.length)
}

/**
 * Lightweight inline parser. Markers without a closing partner are treated as
 * partial stream state: the marker is hidden and the text so far still gets
 * the style, so bold/code never flashes as raw `**` / backticks while typing.
 */
private fun appendInline(builder: AnnotatedString.Builder, source: String) {
    var cursor = 0
    while (cursor < source.length) {
        // Escaped punctuation: "\*" renders as a literal asterisk.
        if (source[cursor] == '\\' && cursor + 1 < source.length &&
            "\\`*_{}[]()#+-.!~>|".contains(source[cursor + 1])
        ) {
            builder.append(source[cursor + 1])
            cursor += 2
            continue
        }

        // Links: [label](url) renders as an underlined label.
        if (source[cursor] == '[') {
            val closeLabel = source.indexOf("](", cursor + 1)
            val closeUrl = if (closeLabel > cursor) source.indexOf(')', closeLabel + 2) else -1
            if (closeLabel > cursor + 1 && closeUrl > closeLabel + 2) {
                val label = source.substring(cursor + 1, closeLabel)
                val url = source.substring(closeLabel + 2, closeUrl)
                val start = builder.length
                appendInline(builder, label.ifBlank { url })
                if (builder.length > start) {
                    builder.addStyle(
                        SpanStyle(color = VyRxColors.Blue, textDecoration = TextDecoration.Underline),
                        start,
                        builder.length
                    )
                    builder.addStringAnnotation("URL", url, start, builder.length)
                }
                cursor = closeUrl + 1
                continue
            }
            builder.append('[')
            cursor++
            continue
        }

        // Inline code: `...` (unclosed = rest of the line is code so far).
        if (source[cursor] == '`') {
            val close = source.indexOf('`', cursor + 1)
            if (close > cursor + 1) {
                appendStyled(builder, InlineCodeStyle) { it.append(source.substring(cursor + 1, close)) }
                cursor = close + 1
                continue
            }
            if (close == cursor + 1) {
                cursor += 2 // Empty "``": skip both ticks.
                continue
            }
            val rest = source.substring(cursor + 1)
            if (rest.isEmpty()) {
                cursor++
                continue
            }
            appendStyled(builder, InlineCodeStyle) { it.append(rest) }
            cursor = source.length
            continue
        }

        // Bold: **...** or __...__.
        val boldMarker = when {
            source.startsWith("**", cursor) -> "**"
            source.startsWith("__", cursor) &&
                !(cursor > 0 && source[cursor - 1].isLetterOrDigit() &&
                    cursor + 2 < source.length && source[cursor + 2].isLetterOrDigit()) -> "__"
            else -> null
        }
        if (boldMarker != null) {
            val close = source.indexOf(boldMarker, cursor + 2)
            if (close > cursor + 2) {
                appendStyled(builder, SpanStyle(fontWeight = FontWeight.Bold)) {
                    appendInline(it, source.substring(cursor + 2, close))
                }
                cursor = close + 2
                continue
            }
            val rest = source.substring(cursor + 2)
            if (rest.isEmpty()) {
                cursor += 2
                continue
            }
            appendStyled(builder, SpanStyle(fontWeight = FontWeight.Bold)) {
                appendInline(it, rest)
            }
            cursor = source.length
            continue
        }

        // Strikethrough: ~~...~~.
        if (source.startsWith("~~", cursor)) {
            val close = source.indexOf("~~", cursor + 2)
            if (close > cursor + 2) {
                appendStyled(builder, SpanStyle(textDecoration = TextDecoration.LineThrough)) {
                    appendInline(it, source.substring(cursor + 2, close))
                }
                cursor = close + 2
                continue
            }
            val rest = source.substring(cursor + 2)
            if (rest.isEmpty()) {
                cursor += 2
                continue
            }
            appendStyled(builder, SpanStyle(textDecoration = TextDecoration.LineThrough)) {
                appendInline(it, rest)
            }
            cursor = source.length
            continue
        }

        // Italic: *...* (a lone "*" without a partner is literal text, so
        // maths like "3 * 4" never gets mangled mid-stream).
        if (source[cursor] == '*') {
            val close = source.indexOf('*', cursor + 1)
            if (close > cursor + 1) {
                appendStyled(builder, SpanStyle(fontStyle = FontStyle.Italic)) {
                    appendInline(it, source.substring(cursor + 1, close))
                }
                cursor = close + 1
                continue
            }
            builder.append('*')
            cursor++
            continue
        }

        // Italic: _..._ (never inside a word, so snake_case survives).
        if (source[cursor] == '_' && (cursor == 0 || !source[cursor - 1].isLetterOrDigit())) {
            var close = source.indexOf('_', cursor + 1)
            while (close > cursor + 1 && close + 1 < source.length && source[close + 1].isLetterOrDigit()) {
                close = source.indexOf('_', close + 1)
            }
            if (close > cursor + 1) {
                appendStyled(builder, SpanStyle(fontStyle = FontStyle.Italic)) {
                    appendInline(it, source.substring(cursor + 1, close))
                }
                cursor = close + 1
                continue
            }
            builder.append('_')
            cursor++
            continue
        }

        builder.append(source[cursor])
        cursor++
    }
}

private val InlineCodeStyle = SpanStyle(
    fontFamily = FontFamily.Monospace,
    color = VyRxColors.PrimaryBright,
    background = Color(0xFF232C44)
)

private val CodeBlockBackground = Color(0xFF070B14)
private val CodeBlockHeader = Color(0xFF0C1322)
private val CodeBlockStroke = Color(0xFF1F2B45)
private val CodeText = Color(0xFFE2E8F0)
olor(0xFF0C1322)
private val CodeBlockStroke = Color(0xFF1F2B45)
private val CodeText = Color(0xFFE2E8F0)
