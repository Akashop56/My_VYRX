package com.ronin.ai.utils

import android.content.Context
import android.content.Intent
import com.ronin.ai.network.AgentAction
import com.ronin.ai.network.AgentResult
import com.ronin.ai.network.AndroidCommand
import com.ronin.ai.services.FloatingBubbleService
import com.ronin.ai.services.RoninAccessibilityService
import com.ronin.ai.services.RoninNotificationListener

/**
 * Kotlin-Python bridge: executes Brain-dispatched tools on the device.
 *
 * Two paths:
 * - [executeAction] (suspend, agentic): runs an [AgentAction] and returns a
 *   structured [AgentResult] observation for POSTing back to /agent/result.
 * - [execute] (legacy, synchronous): runs an [AndroidCommand] for the offline
 *   fast path. Kept for backward compatibility.
 */
object CommandExecutor {
    private val allowed = setOf("click", "set_text", "scroll_forward", "scroll_backward", "global_back", "global_home", "open_bubble", "open_app")

    // ------------------------------------------------------------------
    // Agentic path (suspend) — the autonomous OS core
    // ------------------------------------------------------------------

    suspend fun executeAction(context: Context, action: AgentAction): AgentResult {
        return try {
            when (action.tool) {
                "open_app" -> {
                    val ok = launchApp(context, action.argString("package"), action.argString("app_name"))
                    if (ok) AgentResult.ok("App launched: ${action.argString("package") ?: action.argString("app_name")}")
                    else AgentResult.fail("App could not be launched (not found or ambiguous).")
                }
                "list_apps" -> AgentResult.ok(listApps(context, action.argString("query"), action.argInt("limit", 50)))
                "read_screen" -> {
                    val svc = RoninAccessibilityService.instance
                        ?: return AgentResult.fail("Accessibility service is not enabled; cannot read screen.")
                    AgentResult.ok(svc.dumpScreen(action.argInt("max_nodes", 200).coerceIn(20, 300)).toObservation())
                }
                "click_xy" -> {
                    val x = action.argDouble("x")?.toFloat()
                    val y = action.argDouble("y")?.toFloat()
                    if (x == null || y == null) return AgentResult.fail("click_xy requires numeric x and y.")
                    val svc = RoninAccessibilityService.instance
                        ?: return AgentResult.fail("Accessibility service is not enabled; cannot tap.")
                    if (svc.tapAt(x, y)) AgentResult.ok("Tapped at $x,$y.")
                    else AgentResult.fail("Tap at $x,$y failed (gesture rejected).")
                }
                "click_node" -> {
                    val svc = RoninAccessibilityService.instance
                        ?: return AgentResult.fail("Accessibility service is not enabled; cannot click.")
                    val nodeId = action.argString("node_id")
                    val text = action.argString("text")
                    val ok = if (!nodeId.isNullOrBlank()) svc.clickNodeById(nodeId)
                    else if (!text.isNullOrBlank()) svc.clickText(text)
                    else return AgentResult.fail("click_node requires node_id or text.")
                    if (ok) AgentResult.ok("Clicked ${if (!nodeId.isNullOrBlank()) "node $nodeId" else "text \"$text\""}.")
                    else AgentResult.fail("Click target not found (screen may have changed; call read_screen again).")
                }
                "click" -> {
                    val text = action.argString("text")
                        ?: return AgentResult.fail("click requires text.")
                    val svc = RoninAccessibilityService.instance
                        ?: return AgentResult.fail("Accessibility service is not enabled; cannot click.")
                    if (svc.clickText(text)) AgentResult.ok("Clicked \"$text\".")
                    else AgentResult.fail("Text \"$text\" not found (call read_screen to see current screen).")
                }
                "set_text" -> {
                    val text = action.argString("text")
                        ?: return AgentResult.fail("set_text requires text.")
                    val svc = RoninAccessibilityService.instance
                        ?: return AgentResult.fail("Accessibility service is not enabled; cannot type.")
                    if (svc.setFocusedText(text)) AgentResult.ok("Typed text into focused field.")
                    else AgentResult.fail("No focused input field found.")
                }
                "scroll" -> {
                    val svc = RoninAccessibilityService.instance
                        ?: return AgentResult.fail("Accessibility service is not enabled; cannot scroll.")
                    val dir = action.argString("direction") ?: "forward"
                    if (svc.scroll(dir)) AgentResult.ok("Scrolled $dir.")
                    else AgentResult.fail("Nothing scrollable on screen.")
                }
                "press_back" -> {
                    val ok = RoninAccessibilityService.instance?.performGlobalAction(
                        android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_BACK) ?: false
                    if (ok) AgentResult.ok("Pressed Back.") else AgentResult.fail("Back action failed.")
                }
                "press_home" -> {
                    val ok = RoninAccessibilityService.instance?.performGlobalAction(
                        android.accessibilityservice.AccessibilityService.GLOBAL_ACTION_HOME) ?: false
                    if (ok) AgentResult.ok("Pressed Home.") else AgentResult.fail("Home action failed.")
                }
                "get_notifications" ->
                    AgentResult.ok(RoninNotificationListener.formatted(action.argInt("limit", 10)))
                "open_bubble" -> {
                    context.startService(Intent(context, FloatingBubbleService::class.java))
                    AgentResult.ok("Overlay bubble opened.")
                }
                else -> AgentResult.fail("Unsupported device tool: ${action.tool}")
            }
        } catch (e: Exception) {
            AgentResult.fail("Execution failed: ${e.localizedMessage ?: e.javaClass.simpleName}")
        }
    }

    // ------------------------------------------------------------------
    // Legacy path (synchronous) — offline fast path
    // ------------------------------------------------------------------

    fun execute(context: Context, command: AndroidCommand): Result<Boolean> = runCatching {
        require(command.action in allowed || command.action in setOf(
            "read_screen", "click_xy", "click_node", "scroll", "press_back", "press_home",
            "get_notifications", "list_apps")) { "Unsupported Android command" }
        when (command.action) {
            "open_app" -> launchApp(context, command.package_name, command.text)
            "open_bubble" -> {
                context.startService(Intent(context, FloatingBubbleService::class.java))
                true
            }
            else -> {
                if (command.action in setOf("click", "set_text")) require(!command.text.isNullOrBlank())
                RoninAccessibilityService.instance?.execute(command) ?: false
            }
        }
    }

    // ------------------------------------------------------------------
    // App launching / listing
    // ------------------------------------------------------------------

    @Suppress("DEPRECATION")
    private fun launchApp(context: Context, packageName: String?, label: String?): Boolean {
        val pm = context.packageManager
        val target = packageName?.takeIf { it.isNotBlank() } ?: run {
            val name = label?.trim().orEmpty()
            require(name.isNotEmpty()) { "Specify an app name or package" }
            // Exact label/package matching avoids launching an unrelated app.
            val matches = pm.queryIntentActivities(
                Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0
            ).filter {
                it.loadLabel(pm).toString().equals(name, ignoreCase = true) ||
                    it.activityInfo.packageName == name
            }.map { it.activityInfo.packageName }.distinct()
            // Fallback: substring match on label for agent convenience.
            val resolved = if (matches.size == 1) matches.single() else {
                val fuzzy = pm.queryIntentActivities(
                    Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0
                ).filter { it.loadLabel(pm).toString().contains(name, ignoreCase = true) }
                    .map { it.activityInfo.packageName }.distinct()
                require(fuzzy.size == 1) { "App not found or name is ambiguous; specify its package name" }
                fuzzy.single()
            }
            resolved
        }
        val intent = pm.getLaunchIntentForPackage(target)
            ?: error("App is not installed or has no launcher activity")
        context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        return true
    }

    @Suppress("DEPRECATION")
    private fun listApps(context: Context, query: String?, limit: Int): String {
        val pm = context.packageManager
        val all = pm.queryIntentActivities(
            Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0
        ).map { it.loadLabel(pm).toString() to it.activityInfo.packageName }
            .distinctBy { it.second }
            .sortedBy { it.first.lowercase() }
        val filtered = if (query.isNullOrBlank()) all
        else all.filter { it.first.contains(query, ignoreCase = true) || it.second.contains(query, ignoreCase = true) }
        val shown = filtered.take(limit.coerceIn(1, 200))
        if (shown.isEmpty()) return "No installed apps match \"$query\"."
        return buildString {
            append("Installed apps (${filtered.size} match${if (filtered.size == 1) "" else "es"}):\n")
            shown.forEach { (label, pkg) -> append("- $label [$pkg]\n") }
        }.take(6000)
    }
}
