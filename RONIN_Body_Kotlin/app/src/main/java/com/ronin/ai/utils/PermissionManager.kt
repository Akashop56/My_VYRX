package com.ronin.ai.utils
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.provider.Settings
import com.ronin.ai.services.RoninAccessibilityService
import com.ronin.ai.services.RoninNotificationListener

object PermissionManager {
 fun hasOverlay(context:Context)=Settings.canDrawOverlays(context)
 fun requestOverlay(context:Context)=context.startActivity(Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION))
 fun openAccessibilitySettings(context:Context)=context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
 fun openNotificationSettings(context:Context)=context.startActivity(Intent("android.settings.ACTION_NOTIFICATION_LISTENER_SETTINGS"))
 fun accessibilityEnabled()=RoninAccessibilityService.instance!=null
}
