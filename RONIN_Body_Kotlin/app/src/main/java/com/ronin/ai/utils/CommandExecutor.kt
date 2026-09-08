package com.ronin.ai.utils

import android.content.Context
import android.content.Intent
import com.ronin.ai.network.AndroidCommand
import com.ronin.ai.services.FloatingBubbleService
import com.ronin.ai.services.RoninAccessibilityService

object CommandExecutor {
    private val allowed = setOf("click", "set_text", "scroll_forward", "scroll_backward", "global_back", "global_home", "open_bubble", "open_app")

    fun execute(context: Context, command: AndroidCommand): Result<Boolean> = runCatching {
        require(command.action in allowed) { "Unsupported Android command" }
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
            require(matches.size == 1) { "App not found or name is ambiguous; specify its package name" }
            matches.single()
        }
        val intent = pm.getLaunchIntentForPackage(target)
            ?: error("App is not installed or has no launcher activity")
        context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        return true
    }
}
