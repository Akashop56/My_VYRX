package com.ronin.ai.service

import android.accessibilityservice.AccessibilityServiceInfo
import android.view.accessibility.AccessibilityEvent
import com.ronin.ai.services.RoninAccessibilityService

/**
 * Manifest-bound God-mode service (BIND_ACCESSIBILITY_SERVICE).
 *
 * All screen-reading / gesture / node-click machinery lives in
 * [RoninAccessibilityService]; this subclass is the component Android
 * actually instantiates, so it owns service configuration, the static
 * accessor used by the agent bridge, and event-driven state tracking.
 */
class AccessibilityHelperService : RoninAccessibilityService() {

    companion object {
        /** The running service instance (this class is what the manifest binds). */
        var helperInstance: AccessibilityHelperService? = null
            private set

        /** True once the user has enabled VYRX in accessibility settings. */
        val isEnabled: Boolean get() = helperInstance != null

        /** Last known foreground package (tracked via window-state events). */
        var lastPackage: String? = null
            private set
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        helperInstance = this
        // Harden runtime config (XML is the source of truth; this re-asserts).
        serviceInfo = serviceInfo?.apply {
            eventTypes = AccessibilityEvent.TYPES_ALL_MASK
            feedbackType = AccessibilityServiceInfo.FEEDBACK_GENERIC
            notificationTimeout = 100
            flags = flags or AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS
        }
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            event.packageName?.toString()?.takeIf { it.isNotBlank() }?.let { lastPackage = it }
        }
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        if (helperInstance === this) helperInstance = null
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        if (helperInstance === this) helperInstance = null
        super.onDestroy()
    }
}
