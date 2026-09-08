package com.ronin.ai.ui.memory

import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AutoStories
import androidx.compose.material.icons.filled.Book
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.FilterList
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.PushPin
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Share
import androidx.compose.material.icons.filled.Sort
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import com.ronin.ai.data.AppSettings
import com.ronin.ai.network.ApiClient
import com.ronin.ai.network.MemoryItem
import com.ronin.ai.network.MemoryStats
import com.ronin.ai.ui.components.VyRxIconButton
import com.ronin.ai.ui.components.VyRxTopBar
import com.ronin.ai.ui.theme.CategoryBadge
import com.ronin.ai.ui.theme.GlassCard
import com.ronin.ai.ui.theme.SectionHeader
import com.ronin.ai.ui.theme.SectionLabel
import com.ronin.ai.ui.theme.VyRxColors
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private val TABS = listOf("all" to "All Memory", "personal" to "Personal", "experience" to "Experience", "knowledge" to "Knowledge")

private fun categoryIcon(category: String): ImageVector = when (category) {
    "personal" -> Icons.Filled.Person
    "experience" -> Icons.Filled.Code
    "knowledge" -> Icons.Filled.AutoStories
    else -> Icons.Filled.Book
}

private fun categoryTint(category: String): Color = when (category) {
    "personal" -> VyRxColors.PrimaryBright
    "experience" -> VyRxColors.Blue
    "knowledge" -> VyRxColors.Green
    else -> VyRxColors.TextDim
}

private fun formatMemoryDate(iso: String): String =
    try {
        val parsed = java.time.OffsetDateTime.parse(iso)
        SimpleDateFormat("dd MMM yyyy • hh:mm a", Locale.getDefault()).format(Date.from(parsed.toInstant()))
    } catch (_: Exception) {
        iso.take(16)
    }

@Composable
fun MemoryScreen(
    onOpenDrawer: () -> Unit,
    onToast: (String) -> Unit
) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var tab by remember { mutableStateOf("all") }
    var query by remember { mutableStateOf("") }
    var sort by remember { mutableStateOf("newest") }
    var filterOpen by remember { mutableStateOf(false) }
    var items by remember { mutableStateOf<List<MemoryItem>?>(null) }
    var stats by remember { mutableStateOf<MemoryStats?>(null) }
    var addOpen by remember { mutableStateOf(false) }
    var editing by remember { mutableStateOf<MemoryItem?>(null) }
    var detail by remember { mutableStateOf<MemoryItem?>(null) }
    var longPressMenu by remember { mutableStateOf<MemoryItem?>(null) }
    var loading by remember { mutableStateOf(false) }

    suspend fun refresh() {
        try {
            loading = true
            val result = ApiClient.memories(
                category = if (tab == "all") null else tab,
                search = query.ifBlank { null },
                sort = sort
            )
            items = result.first
            stats = result.second
        } catch (e: Exception) {
            onToast("Brain offline — cannot load memories")
        } finally {
            loading = false
        }
    }

    fun performAction(memory: MemoryItem, action: String) {
        scope.launch {
            try {
                when (action) {
                    "pin" -> ApiClient.updateMemory(memory.id, pinned = !memory.pinned)
                    "important" -> ApiClient.updateMemory(memory.id, importance = 5)
                    "delete" -> ApiClient.deleteMemory(memory.id)
                    "export" -> exportMemories(context, listOf(memory), onToast)
                    "edit" -> { editing = memory }
                }
                refresh()
            } catch (e: Exception) {
                onToast("Action failed: ${e.message}")
            }
        }
    }

    LaunchedEffect(tab, sort) { refresh() }
    LaunchedEffect(query) {
        if (query.length > 2) delay(350)
        refresh()
    }

    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            VyRxTopBar(
                title = "MEMORY",
                subtitle = "AI Knowledge & Experience",
                onMenu = onOpenDrawer,
                trailing = {
                    Row {
                        VyRxIconButton(Icons.Filled.Search, onClick = { query = if (query.isEmpty()) " " else "" })
                        VyRxIconButton(Icons.Filled.FilterList, onClick = { filterOpen = true })
                    }
                }
            )

            // Category tabs
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 6.dp)
                    .clip(RoundedCornerShape(16.dp))
                    .background(Color(0xFF0B0F17))
                    .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(16.dp))
                    .padding(4.dp),
                horizontalArrangement = Arrangement.spacedBy(2.dp)
            ) {
                TABS.forEach { (value, label) ->
                    val active = tab == value
                    Box(
                        Modifier
                            .weight(1f)
                            .clip(RoundedCornerShape(12.dp))
                            .background(if (active) VyRxColors.Primary.copy(alpha = 0.22f) else Color.Transparent)
                            .border(1.dp, if (active) VyRxColors.Primary.copy(alpha = 0.55f) else Color.Transparent, RoundedCornerShape(12.dp))
                            .clickable { tab = value }
                            .padding(vertical = 8.dp),
                        contentAlignment = Alignment.Center
                    ) {
                        Text(label, color = if (active) VyRxColors.PrimaryBright else VyRxColors.TextDim, fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    }
                }
            }

            LazyColumn(
                Modifier.fillMaxWidth(),
                contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 6.dp, bottom = 96.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                // Overview card
                item {
                    GlassCard(Modifier.fillMaxWidth()) {
                        SectionHeader("MEMORY OVERVIEW", "View Details", null)
                        Spacer(Modifier.height(10.dp))
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OverviewTile(Modifier.weight(1f), Icons.Filled.Person, VyRxColors.PrimaryBright, fmtBig(stats?.total ?: 0), "Total Memories")
                            OverviewTile(Modifier.weight(1f), Icons.Filled.Book, VyRxColors.Blue, fmtSize(stats?.sizeBytes ?: 0L), "Memory Size")
                            OverviewTile(Modifier.weight(1f), Icons.Filled.Star, VyRxColors.Green, "${stats?.healthScore ?: 0}%", "Health Score")
                            OverviewTile(Modifier.weight(1f), Icons.Filled.Sort, VyRxColors.Amber, "${stats?.retentionDays ?: 0} days", "Total Retention")
                        }
                    }
                }

                // Search bar
                item {
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(16.dp))
                            .background(Color(0xFF0C1018))
                            .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(16.dp))
                    ) {
                        Row(Modifier.fillMaxWidth().padding(horizontal = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Filled.Search, null, tint = VyRxColors.TextFaint, modifier = Modifier.size(17.dp))
                            Spacer(Modifier.width(8.dp))
                            BasicTextField(
                                value = query,
                                onValueChange = { query = it },
                                modifier = Modifier.weight(1f).padding(vertical = 14.dp),
                                textStyle = TextStyle(color = VyRxColors.TextPrimary, fontSize = 13.sp),
                                cursorBrush = SolidColor(VyRxColors.PrimaryBright),
                                singleLine = true
                            )
                            if (query.isEmpty()) {
                                Text(
                                    "Search memories...",
                                    color = VyRxColors.TextFaint,
                                    fontSize = 13.sp,
                                    modifier = Modifier.align(Alignment.CenterStart).offset(x = 42.dp)
                                )
                            }
                        }
                    }
                }

                // Recent memories header
                item {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        SectionLabel("RECENT MEMORIES", VyRxColors.PrimaryBright)
                        Box(
                            Modifier
                                .clip(RoundedCornerShape(10.dp))
                                .clickable { sort = if (sort == "newest") "oldest" else "newest" }
                                .padding(horizontal = 8.dp, vertical = 4.dp)
                        ) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Text(if (sort == "newest") "New First" else "Old First", color = VyRxColors.PrimaryBright, fontSize = 11.sp)
                                Spacer(Modifier.width(4.dp))
                                Icon(Icons.Filled.Sort, null, tint = VyRxColors.PrimaryBright, modifier = Modifier.size(13.dp))
                            }
                        }
                    }
                }

                if (items == null) {
                    item {
                        Box(Modifier.fillMaxWidth().padding(vertical = 30.dp), contentAlignment = Alignment.Center) {
                            Text(if (loading) "Loading memories..." else "Brain offline", color = VyRxColors.TextDim, fontSize = 12.sp)
                        }
                    }
                } else if (items!!.isEmpty()) {
                    item {
                        Box(Modifier.fillMaxWidth().padding(vertical = 30.dp), contentAlignment = Alignment.Center) {
                            Text("No memories yet. Tap \"Add Memory\" to create the first one.", color = VyRxColors.TextDim, fontSize = 12.sp)
                        }
                    }
                } else {
                    items(items!!, key = { it.id }) { memory ->
                        MemoryCard(
                            memory = memory,
                            onClick = { detail = memory },
                            onLongClick = { longPressMenu = memory },
                            onAction = { action -> performAction(memory, action) }
                        )
                    }
                }
            }
        }

        // Floating Add Memory button
        Button(
            onClick = { addOpen = true },
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(16.dp)
                .clip(RoundedCornerShape(18.dp)),
            colors = ButtonDefaults.buttonColors(containerColor = VyRxColors.Primary, contentColor = Color.White)
        ) {
            Icon(Icons.Filled.Add, null, modifier = Modifier.size(18.dp))
            Spacer(Modifier.width(8.dp))
            Text("Add Memory", fontWeight = FontWeight.SemiBold)
        }
    }

    // Add dialog
    if (addOpen) {
        MemoryEditorDialog(
            initial = null,
            onDismiss = { addOpen = false },
            onSave = { category, title, content, importance ->
                addOpen = false
                scope.launch {
                    try {
                        ApiClient.addMemory(category, title, content, importance, source = "user")
                        refresh()
                        onToast("Memory saved ✓")
                    } catch (e: Exception) {
                        onToast("Failed: ${e.message}")
                    }
                }
            }
        )
    }

    // Edit dialog
    editing?.let { memory ->
        MemoryEditorDialog(
            initial = memory,
            onDismiss = { editing = null },
            onSave = { category, title, content, importance ->
                editing = null
                scope.launch {
                    try {
                        ApiClient.updateMemory(memory.id, category = category, title = title, content = content, importance = importance)
                        refresh()
                    } catch (e: Exception) {
                        onToast("Failed: ${e.message}")
                    }
                }
            }
        )
    }

    // Detail dialog
    detail?.let { memory ->
        AlertDialog(
            onDismissRequest = { detail = null },
            title = { Text("Memory Details", fontWeight = FontWeight.Bold) },
            text = {
                Column(Modifier.fillMaxWidth()) {
                    Text(memory.title, color = VyRxColors.TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                    Spacer(Modifier.height(8.dp))
                    Text(memory.content, color = VyRxColors.TextPrimary, fontSize = 12.sp)
                    Spacer(Modifier.height(12.dp))
                    DetailRow("Created", formatMemoryDate(memory.createdAt))
                    DetailRow("Source", memory.source)
                    DetailRow("Confidence", "${(memory.confidence * 100).toInt()}%")
                    DetailRow("Used", "${memory.useCount} times")
                    DetailRow("Category", memory.category)
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    exportMemories(context, listOf(memory), onToast)
                    detail = null
                }) { Text("Export", color = VyRxColors.PrimaryBright) }
            },
            dismissButton = {
                TextButton(onClick = {
                    scope.launch {
                        try {
                            ApiClient.deleteMemory(memory.id)
                            refresh()
                        } catch (e: Exception) { onToast("Failed: ${e.message}") }
                    }
                    detail = null
                }) { Text("Delete", color = VyRxColors.Red) }
            }
        )
    }

    // Long-press action menu
    longPressMenu?.let { memory ->
        AlertDialog(
            onDismissRequest = { longPressMenu = null },
            title = { Text(memory.title.take(40), fontWeight = FontWeight.SemiBold) },
            text = {
                Column(Modifier.fillMaxWidth()) {
                    Row { TextButton(onClick = { longPressMenu = null; performAction(memory, "important") }) { Text("⭐ Important") } }
                    Row { TextButton(onClick = { longPressMenu = null; performAction(memory, "edit") }) { Text("✏️ Edit") } }
                    Row { TextButton(onClick = { longPressMenu = null; performAction(memory, "pin") }) { Text(if (memory.pinned) "📌 Unpin" else "📌 Pin") } }
                    Row { TextButton(onClick = { longPressMenu = null; performAction(memory, "export") }) { Text("📤 Export") } }
                    Row { TextButton(onClick = { longPressMenu = null; performAction(memory, "delete") }) { Text("🗑 Delete", color = VyRxColors.Red) } }
                }
            },
            confirmButton = {}
        )
    }

    // Filter dialog
    if (filterOpen) {
        FilterDialog(
            currentSort = sort,
            onDismiss = { filterOpen = false },
            onSort = { sort = it }
        )
    }
}

// ---------------------------------------------------------------------------

@Composable
private fun DetailRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(label, color = VyRxColors.TextDim, fontSize = 11.sp)
        Text(value, color = VyRxColors.TextPrimary, fontSize = 11.sp)
    }
}

@Composable
private fun OverviewTile(modifier: Modifier, icon: ImageVector, tint: Color, value: String, label: String) {
    Box(
        modifier
            .clip(RoundedCornerShape(14.dp))
            .background(Color(0xFF0B0F17))
            .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(14.dp))
            .padding(10.dp)
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Box(
                Modifier
                    .size(30.dp)
                    .clip(CircleShape)
                    .background(tint.copy(alpha = 0.13f))
                    .border(1.dp, tint.copy(alpha = 0.35f), CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Icon(icon, null, tint = tint, modifier = Modifier.size(15.dp))
            }
            Spacer(Modifier.height(8.dp))
            Text(value, color = VyRxColors.TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.Bold, maxLines = 1)
            Text(label, color = VyRxColors.TextDim, fontSize = 9.sp, maxLines = 1)
        }
    }
}

@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
private fun MemoryCard(memory: MemoryItem, onClick: () -> Unit, onLongClick: () -> Unit, onAction: (String) -> Unit) {
    val tint = categoryTint(memory.category)
    var menuOpen by remember { mutableStateOf(false) }
    Row(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(18.dp))
            .background(Color(0xFF0D1220))
            .border(1.dp, VyRxColors.CardStrokeSoft, RoundedCornerShape(18.dp))
            .combinedClickable(onClick = onClick, onLongClick = onLongClick)
            .padding(14.dp),
        verticalAlignment = Alignment.Top
    ) {
        Box(
            Modifier
                .size(40.dp)
                .clip(CircleShape)
                .background(tint.copy(alpha = 0.13f))
                .border(1.dp, tint.copy(alpha = 0.4f), CircleShape),
            contentAlignment = Alignment.Center
        ) {
            Icon(categoryIcon(memory.category), null, tint = tint, modifier = Modifier.size(19.dp))
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(memory.title, color = VyRxColors.TextPrimary, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, maxLines = 1)
                if (memory.pinned) Icon(Icons.Filled.PushPin, null, tint = VyRxColors.Amber, modifier = Modifier.size(13.dp))
                CategoryBadge(memory.category)
            }
            Spacer(Modifier.height(4.dp))
            Text(memory.content, color = VyRxColors.TextDim, fontSize = 11.sp, maxLines = 2)
            Spacer(Modifier.height(6.dp))
            Text(formatMemoryDate(memory.createdAt), color = VyRxColors.TextFaint, fontSize = 10.sp)
        }
        Spacer(Modifier.width(6.dp))
        Box(
            Modifier
                .clip(CircleShape)
                .clickable { menuOpen = true }
                .padding(6.dp),
            contentAlignment = Alignment.Center
        ) {
            Icon(Icons.Filled.MoreVert, null, tint = VyRxColors.TextDim, modifier = Modifier.size(17.dp))
        }
        DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
            DropdownMenuItem(leadingIcon = { Icon(Icons.Filled.Star, null, Modifier.size(16.dp)) }, text = { Text("Important") }, onClick = { menuOpen = false; onAction("important") })
            DropdownMenuItem(leadingIcon = { Icon(Icons.Filled.Edit, null, Modifier.size(16.dp)) }, text = { Text("Edit") }, onClick = { menuOpen = false; onAction("edit") })
            DropdownMenuItem(leadingIcon = { Icon(Icons.Filled.PushPin, null, Modifier.size(16.dp)) }, text = { Text(if (memory.pinned) "Unpin" else "Pin") }, onClick = { menuOpen = false; onAction("pin") })
            DropdownMenuItem(leadingIcon = { Icon(Icons.Filled.Share, null, Modifier.size(16.dp)) }, text = { Text("Export") }, onClick = { menuOpen = false; onAction("export") })
            DropdownMenuItem(leadingIcon = { Icon(Icons.Filled.Delete, null, Modifier.size(16.dp)) }, text = { Text("Delete", color = VyRxColors.Red) }, onClick = { menuOpen = false; onAction("delete") })
        }
    }
}

@Composable
private fun FilterDialog(currentSort: String, onDismiss: () -> Unit, onSort: (String) -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Filter") },
        text = {
            Column(Modifier.fillMaxWidth()) {
                SectionLabel("SORT")
                Spacer(Modifier.height(8.dp))
                listOf("newest" to "Newest", "oldest" to "Oldest", "most_used" to "Most Used", "important" to "Important").forEach { (value, label) ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(10.dp))
                            .clickable { onSort(value) }
                            .padding(vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Box(
                            Modifier
                                .size(16.dp)
                                .clip(CircleShape)
                                .border(1.5.dp, if (currentSort == value) VyRxColors.Primary else VyRxColors.CardStroke, CircleShape)
                        ) {
                            if (currentSort == value) {
                                Box(Modifier.size(9.dp).align(Alignment.Center).clip(CircleShape).background(VyRxColors.Primary))
                            }
                        }
                        Spacer(Modifier.width(10.dp))
                        Text(label, color = VyRxColors.TextPrimary, fontSize = 13.sp)
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Done") } }
    )
}

@Composable
private fun MemoryEditorDialog(
    initial: MemoryItem?,
    onDismiss: () -> Unit,
    onSave: (String, String, String, Int) -> Unit
) {
    var category by remember { mutableStateOf(initial?.category ?: "personal") }
    var title by remember { mutableStateOf(initial?.title ?: "") }
    var content by remember { mutableStateOf(initial?.content ?: "") }
    var importance by remember { mutableStateOf(initial?.importance ?: 3) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (initial == null) "Add Memory" else "Edit Memory", fontWeight = FontWeight.Bold) },
        text = {
            Column(Modifier.fillMaxWidth()) {
                SectionLabel("CATEGORY")
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf("personal" to "Personal", "experience" to "Experience", "knowledge" to "Knowledge").forEach { (value, label) ->
                        Box(
                            Modifier
                                .clip(RoundedCornerShape(10.dp))
                                .background(if (category == value) VyRxColors.Primary.copy(alpha = 0.22f) else Color(0xFF0B0F17))
                                .border(1.dp, if (category == value) VyRxColors.Primary else VyRxColors.CardStroke, RoundedCornerShape(10.dp))
                                .clickable { category = value }
                                .padding(horizontal = 12.dp, vertical = 7.dp)
                        ) {
                            Text(label, color = if (category == value) VyRxColors.PrimaryBright else VyRxColors.TextDim, fontSize = 12.sp)
                        }
                    }
                }
                Spacer(Modifier.height(14.dp))
                SectionLabel("TITLE")
                Spacer(Modifier.height(4.dp))
                BasicTextField(
                    value = title,
                    onValueChange = { title = it },
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(10.dp))
                        .background(Color(0xFF0C1018))
                        .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(10.dp))
                        .padding(10.dp),
                    textStyle = TextStyle(color = VyRxColors.TextPrimary, fontSize = 13.sp),
                    cursorBrush = SolidColor(VyRxColors.PrimaryBright),
                    singleLine = true
                )
                Spacer(Modifier.height(12.dp))
                SectionLabel("CONTENT")
                Spacer(Modifier.height(4.dp))
                BasicTextField(
                    value = content,
                    onValueChange = { content = it },
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(90.dp)
                        .clip(RoundedCornerShape(10.dp))
                        .background(Color(0xFF0C1018))
                        .border(1.dp, VyRxColors.CardStroke, RoundedCornerShape(10.dp))
                        .padding(10.dp),
                    textStyle = TextStyle(color = VyRxColors.TextPrimary, fontSize = 13.sp),
                    cursorBrush = SolidColor(VyRxColors.PrimaryBright)
                )
                Spacer(Modifier.height(12.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    SectionLabel("IMPORTANCE")
                    Spacer(Modifier.width(10.dp))
                    repeat(5) { i ->
                        Icon(
                            Icons.Filled.Star,
                            null,
                            tint = if (i < importance) VyRxColors.Amber else VyRxColors.CardStroke,
                            modifier = Modifier
                                .size(20.dp)
                                .clickable { importance = i + 1 }
                        )
                    }
                }
            }
        },
        confirmButton = {
            Button(
                enabled = title.isNotBlank() && content.isNotBlank(),
                onClick = { onSave(category, title.trim(), content.trim(), importance) },
                colors = ButtonDefaults.buttonColors(containerColor = VyRxColors.Primary)
            ) { Text("Save") }
        },
        dismissButton = { OutlinedButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

private fun fmtBig(n: Int): String = if (n >= 1000) String.format(Locale.getDefault(), "%,.0fK", n / 1000f) else "$n"

private fun fmtSize(bytes: Long): String = when {
    bytes >= 1024L * 1024L * 1024L -> String.format(Locale.getDefault(), "%.2f GB", bytes / (1024.0 * 1024.0 * 1024.0))
    bytes >= 1024L * 1024L -> String.format(Locale.getDefault(), "%.0f MB", bytes / (1024.0 * 1024.0))
    bytes >= 1024L -> String.format(Locale.getDefault(), "%.0f KB", bytes / 1024.0)
    else -> "$bytes B"
}

/** Real export: write memories to a JSON file in app storage and share via chooser. */
private fun exportMemories(context: android.content.Context, memories: List<MemoryItem>, onToast: (String) -> Unit) {
    val scope = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO + kotlinx.coroutines.SupervisorJob())
    scope.launch {
        try {
            val json = org.json.JSONArray()
            memories.forEach { m ->
                json.put(
                    org.json.JSONObject()
                        .put("id", m.id)
                        .put("category", m.category)
                        .put("title", m.title)
                        .put("content", m.content)
                        .put("importance", m.importance)
                        .put("pinned", m.pinned)
                        .put("created_at", m.createdAt)
                )
            }
            val dir = File(context.getExternalFilesDir(null), "exports")
            dir.mkdirs()
            val file = File(dir, "vyrx_memory_${System.currentTimeMillis()}.json")
            file.writeText(json.toString(2))
            kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main) {
                val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
                val intent = Intent(Intent.ACTION_SEND).apply {
                    type = "application/json"
                    putExtra(Intent.EXTRA_STREAM, uri)
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
                context.startActivity(Intent.createChooser(intent, "Export memories"))
            }
        } catch (e: Exception) {
            kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Main) {
                onToast("Export failed: ${e.message}")
            }
        }
    }
}
