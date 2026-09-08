package com.ronin.ai.services

import android.accessibilityservice.AccessibilityService
import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityEvent
import com.ronin.ai.network.AndroidCommand

open class RoninAccessibilityService : AccessibilityService() {
 companion object { var instance: RoninAccessibilityService? = null; private set }
 override fun onServiceConnected() { instance=this }
 override fun onDestroy() { if(instance===this) instance=null; super.onDestroy() }
 override fun onAccessibilityEvent(event: AccessibilityEvent?) { }
 override fun onInterrupt() { }
 fun execute(command: AndroidCommand): Boolean = when(command.action) {
  "click" -> command.text?.let(::clickText) ?: false
  "set_text" -> command.text?.let(::setFocusedText) ?: false
  "scroll_forward" -> rootInActiveWindow?.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD) ?: false
  "scroll_backward" -> rootInActiveWindow?.performAction(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD) ?: false
  "global_back" -> performGlobalAction(GLOBAL_ACTION_BACK)
  "global_home" -> performGlobalAction(GLOBAL_ACTION_HOME)
  else -> false
 }
 private fun clickText(text:String):Boolean { val nodes=rootInActiveWindow?.findAccessibilityNodeInfosByText(text).orEmpty(); return nodes.firstOrNull { it.isClickable || it.parent?.isClickable==true }?.let { run { val clickable = if (it.isClickable) it else it.parent; clickable?.performAction(AccessibilityNodeInfo.ACTION_CLICK) ?: false } } ?: false }
 private fun setFocusedText(text:String):Boolean { val node=rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return false; return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,text) }) }
}
