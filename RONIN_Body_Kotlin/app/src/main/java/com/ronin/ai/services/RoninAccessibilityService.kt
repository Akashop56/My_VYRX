package com.ronin.ai.services

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.ronin.ai.network.AndroidCommand
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONArray
import org.json.JSONObject
import kotlin.coroutines.resume

/**
 * God-mode device control: screen reading + physical interaction.
 *
 * The Python Brain drives this service through the agent bridge
 * (CommandExecutor -> AgentAction): `read_screen` dumps the UI tree,
 * `click_xy` / `click_node` tap it. Node ids are short-lived descriptors
 * resolved against a FRESH tree on every click, so they survive the
 * Brain round-trip without holding stale AccessibilityNodeInfo refs.
 */
open class RoninAccessibilityService : AccessibilityService() {

    companion object {
        var instance: RoninAccessibilityService? = null
            private set

        /** Compact a dump for the Brain observation (bounded for HTTP). */
        const val MAX_OBSERVATION_CHARS = 8000
    }

    // ------------------------------------------------------------------
    // Node descriptors (fresh-tree resolution, no stale refs)
    // ------------------------------------------------------------------

    data class NodeDescriptor(
        val id: String,
        val text: String?,
        val contentDescription: String?,
        val className: String?,
        val bounds: Rect,
        val clickable: Boolean,
        val editable: Boolean,
        val scrollable: Boolean
    ) {
        val centerX: Int get() = bounds.centerX()
        val centerY: Int get() = bounds.centerY()
        fun toJson(): JSONObject = JSONObject()
            .put("id", id)
            .putOpt("text", text)
            .putOpt("desc", contentDescription)
            .putOpt("class", className)
            .put("bounds", JSONObject()
                .put("l", bounds.left).put("t", bounds.top)
                .put("r", bounds.right).put("b", bounds.bottom)
                .put("x", centerX).put("y", centerY))
            .put("clickable", clickable)
            .put("editable", editable)
            .put("scrollable", scrollable)
    }

    data class ScreenDump(
        val packageName: String?,
        val nodes: List<NodeDescriptor>,
        val truncated: Boolean
    ) {
        /** Human-readable summary ("Screen text: ...") for the Brain. */
        fun summaryText(): String {
            val texts = nodes.mapNotNull { it.text?.takeIf { t -> t.isNotBlank() } }.distinct()
            val descs = nodes.mapNotNull { it.contentDescription?.takeIf { d -> d.isNotBlank() } }.distinct()
            return buildString {
                append("Screen")
                if (!packageName.isNullOrBlank()) append(" [$packageName]")
                append(": ")
                val combined = (texts + descs).take(60)
                append(if (combined.isEmpty()) "(no readable text)" else combined.joinToString(" | "))
                if (truncated) append(" …(truncated)")
            }.take(RoninAccessibilityService.MAX_OBSERVATION_CHARS)
        }

        fun toJson(maxNodes: Int = 120): JSONObject {
            val arr = JSONArray()
            nodes.take(maxNodes).forEach { arr.put(it.toJson()) }
            return JSONObject()
                .putOpt("package", packageName)
                .put("node_count", nodes.size)
                .put("truncated", truncated)
                .put("nodes", arr)
        }

        /** Full observation string posted back to the Brain. */
        fun toObservation(maxNodes: Int = 120): String {
            val sb = StringBuilder(summaryText())
            sb.append("\nClickable nodes (id | text | desc | x,y):\n")
            nodes.filter { it.clickable }.take(maxNodes).forEach { n ->
                sb.append("- ${n.id} | ${n.text ?: ""} | ${n.contentDescription ?: ""} | ${n.centerX},${n.centerY}\n")
                if (sb.length > RoninAccessibilityService.MAX_OBSERVATION_CHARS) return sb.take(RoninAccessibilityService.MAX_OBSERVATION_CHARS).toString()
            }
            return sb.take(RoninAccessibilityService.MAX_OBSERVATION_CHARS).toString()
        }
    }

    private val nodeRegistry = LinkedHashMap<String, NodeDescriptor>()
    private var nodeCounter = 0

    // ------------------------------------------------------------------
    // Lifecycle
    // ------------------------------------------------------------------

    override fun onServiceConnected() {
        instance = this
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    // ------------------------------------------------------------------
    // Legacy command path (synchronous, kept for backward compatibility)
    // ------------------------------------------------------------------

    fun execute(command: AndroidCommand): Boolean = when (command.action) {
        "click" -> command.text?.let(::clickText) ?: false
        "click_node" -> command.node_id?.let(::clickNodeById) ?: command.text?.let(::clickText) ?: false
        "click_xy" -> if (command.x != null && command.y != null) tapAtAsync(command.x, command.y) else false
        "set_text" -> command.text?.let(::setFocusedText) ?: false
        "scroll_forward", "scroll" -> scroll(command.direction ?: "forward")
        "scroll_backward" -> scroll("backward")
        "global_back", "press_back" -> performGlobalAction(GLOBAL_ACTION_BACK)
        "global_home", "press_home" -> performGlobalAction(GLOBAL_ACTION_HOME)
        else -> false
    }

    // ------------------------------------------------------------------
    // Screen reading
    // ------------------------------------------------------------------

    /** Dump the current screen's UI tree. Called for Brain `read_screen`. */
    fun dumpScreen(maxNodes: Int = 200): ScreenDump {
        val root = rootInActiveWindow ?: return ScreenDump(null, emptyList(), false)
        val pkg = runCatching { root.packageName?.toString() }.getOrNull()
        val out = ArrayList<NodeDescriptor>(maxNodes)
        var truncated = false
        try {
            val stack = ArrayDeque<AccessibilityNodeInfo>()
            stack.add(root)
            while (stack.isNotEmpty() && out.size < maxNodes) {
                val node = stack.removeLast()
                try {
                    val text = node.text?.toString()
                    val desc = node.contentDescription?.toString()
                    // Keep nodes that carry signal; skip empty layout containers.
                    if (!text.isNullOrBlank() || !desc.isNullOrBlank() || node.isClickable || node.isEditable) {
                        val bounds = Rect()
                        node.getBoundsInScreen(bounds)
                        val id = "n${nodeCounter++}"
                        val d = NodeDescriptor(
                            id = id, text = text, contentDescription = desc,
                            className = node.className?.toString(), bounds = Rect(bounds),
                            clickable = node.isClickable || node.isCheckable,
                            editable = node.isEditable || node.isFocusable,
                            scrollable = node.isScrollable
                        )
                        out.add(d)
                        nodeRegistry[id] = d
                        if (nodeRegistry.size > 600) nodeRegistry.remove(nodeRegistry.keys.first())
                    }
                    for (i in 0 until node.childCount) {
                        node.getChild(i)?.let { stack.add(it) } ?: Unit
                    }
                } finally {
                    if (node != root) node.recycle()
                }
            }
            truncated = stack.isNotEmpty()
            // Recycle leftovers.
            while (stack.isNotEmpty()) {
                val n = stack.removeLast()
                if (n != root) n.recycle()
            }
        } finally {
            root.recycle()
        }
        return ScreenDump(pkg, out, truncated)
    }

    // ------------------------------------------------------------------
    // Clicking: text / node id / coordinates
    // ------------------------------------------------------------------

    /** Tap the first node matching visible text (or content description). */
    fun clickText(text: String): Boolean {
        val root = rootInActiveWindow ?: return false
        try {
            val byText = root.findAccessibilityNodeInfosByText(text).orEmpty()
            val target = byText.firstOrNull { it.isClickable || it.parent?.isClickable == true }
                ?: findByDescription(root, text)
                ?: return false
            val clickable = if (target.isClickable) target else target.parent ?: return false
            return clickable.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        } finally {
            root.recycle()
        }
    }

    private fun findByDescription(root: AccessibilityNodeInfo, text: String): AccessibilityNodeInfo? {
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.add(root)
        while (stack.isNotEmpty()) {
            val node = stack.removeLast()
            if (node.contentDescription?.toString()?.contains(text, ignoreCase = true) == true) return node
            for (i in 0 until node.childCount) node.getChild(i)?.let { stack.add(it) }
        }
        return null
    }

    /**
     * Tap a node previously seen via [dumpScreen]. The descriptor is resolved
     * against a FRESH tree (match by text+class+nearest bounds), so ids stay
     * valid across the Brain HTTP round-trip. Falls back to tapping the
     * recorded coordinates when the tree changed.
     */
    fun clickNodeById(nodeId: String): Boolean {
        val wanted = nodeRegistry[nodeId] ?: return false
        val root = rootInActiveWindow
        if (root != null) {
            try {
                val match = findBestMatch(root, wanted)
                if (match != null) {
                    val clickable = generateSequence(match) { it.parent }
                        .firstOrNull { it.isClickable }
                    if (clickable?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true) return true
                }
            } finally {
                root.recycle()
            }
        }
        // Fallback: the layout shifted — tap the last known coordinates.
        return tapAtAsync(wanted.centerX.toFloat(), wanted.centerY.toFloat())
    }

    private fun findBestMatch(root: AccessibilityNodeInfo, wanted: NodeDescriptor): AccessibilityNodeInfo? {
        var best: AccessibilityNodeInfo? = null
        var bestScore = 0
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.add(root)
        while (stack.isNotEmpty()) {
            val node = stack.removeLast()
            var score = 0
            if (!wanted.text.isNullOrBlank() && node.text?.toString() == wanted.text) score += 4
            if (!wanted.contentDescription.isNullOrBlank() &&
                node.contentDescription?.toString() == wanted.contentDescription) score += 4
            if (wanted.className != null && node.className?.toString() == wanted.className) score += 1
            if (node.isClickable == wanted.clickable) score += 1
            if (score > bestScore) {
                bestScore = score
                best = node
            }
            for (i in 0 until node.childCount) node.getChild(i)?.let { stack.add(it) }
        }
        return best?.takeIf { bestScore >= 4 }
    }

    // ------------------------------------------------------------------
    // Gesture taps (X,Y) — suspend (agent path) + fire-and-forget (legacy)
    // ------------------------------------------------------------------

    /** Suspend tap at absolute screen coordinates. Returns gesture completion. */
    suspend fun tapAt(x: Float, y: Float): Boolean {
        val path = Path().apply { moveTo(x, y) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0L, 80L))
            .build()
        return withTimeoutOrNull(3000L) {
            suspendCancellableCoroutine { cont ->
                val dispatched = dispatchGesture(gesture, object : GestureResultCallback() {
                    override fun onCompleted(gestureDescription: GestureDescription?) {
                        if (cont.isActive) cont.resume(true)
                    }
                    override fun onCancelled(gestureDescription: GestureDescription?) {
                        if (cont.isActive) cont.resume(false)
                    }
                }, null)
                if (!dispatched && cont.isActive) cont.resume(false)
            }
        } ?: false
    }

    /** Fire-and-forget tap for synchronous legacy callers. */
    fun tapAtAsync(x: Float, y: Float): Boolean {
        val path = Path().apply { moveTo(x, y) }
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0L, 80L))
            .build()
        return runCatching { dispatchGesture(gesture, null, null) }.getOrDefault(false)
    }

    // ------------------------------------------------------------------
    // Text entry + scrolling
    // ------------------------------------------------------------------

    fun setFocusedText(text: String): Boolean {
        val root = rootInActiveWindow ?: return false
        return try {
            val node = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
                ?: findEditable(root)
                ?: return false
            val args = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        } finally {
            root.recycle()
        }
    }

    private fun findEditable(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.add(root)
        while (stack.isNotEmpty()) {
            val node = stack.removeLast()
            if (node.isEditable || node.actionList.any { it.id == AccessibilityNodeInfo.ACTION_SET_TEXT }) return node
            for (i in 0 until node.childCount) node.getChild(i)?.let { stack.add(it) }
        }
        return null
    }

    fun scroll(direction: String): Boolean {
        val root = rootInActiveWindow ?: return false
        return try {
            val forward = when (direction.lowercase()) {
                "backward", "up", "left" -> false
                else -> true
            }
            val action = if (forward) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            findScrollable(root)?.performAction(action)
                ?: root.performAction(action)
        } finally {
            root.recycle()
        }
    }

    private fun findScrollable(root: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        val stack = ArrayDeque<AccessibilityNodeInfo>()
        stack.add(root)
        while (stack.isNotEmpty()) {
            val node = stack.removeLast()
            if (node.isScrollable) return node
            for (i in 0 until node.childCount) node.getChild(i)?.let { stack.add(it) }
        }
        return null
    }
}
