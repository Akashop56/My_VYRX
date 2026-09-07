package com.ronin.ai.utils
import android.content.Context
import android.content.Intent
import com.ronin.ai.network.AndroidCommand
import com.ronin.ai.services.FloatingBubbleService
import com.ronin.ai.services.RoninAccessibilityService

object CommandExecutor {
 private val allowed=setOf("click","set_text","scroll_forward","scroll_backward","global_back","global_home","open_bubble")
 fun execute(context:Context, command:AndroidCommand):Result<Boolean> = runCatching { require(command.action in allowed); if(command.action in setOf("click","set_text")) require(!command.text.isNullOrBlank()); if(command.action=="open_bubble") { context.startService(Intent(context,FloatingBubbleService::class.java)); true } else RoninAccessibilityService.instance?.execute(command) ?: false }
}
