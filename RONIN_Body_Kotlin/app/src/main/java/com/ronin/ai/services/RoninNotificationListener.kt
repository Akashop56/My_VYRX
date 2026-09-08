package com.ronin.ai.services

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Notification ear for the agent bridge. Keeps a small ring buffer of recent
 * notifications so the Brain's `get_notifications` tool can read them, and
 * still logs every post for diagnostics.
 */
open class RoninNotificationListener : NotificationListenerService() {

    data class SeenNotification(
        val packageName: String,
        val title: String,
        val text: String,
        val time: Long = System.currentTimeMillis()
    ) {
        fun formatted(): String {
            val clock = SimpleDateFormat("HH:mm", Locale.US).format(Date(time))
            return "[$clock] $packageName — $title: $text"
        }
    }

    companion object {
        private const val TAG = "RONIN_NOTIFICATION"
        private const val MAX_KEPT = 30
        private val buffer = ArrayDeque<SeenNotification>()
        private val lock = Any()

        var instance: RoninNotificationListener? = null
            private set

        /** Most recent notifications, newest first. */
        fun snapshot(limit: Int = 10): List<SeenNotification> = synchronized(lock) {
            buffer.takeLast(limit.coerceIn(1, MAX_KEPT)).reversed()
        }

        /** Observation string posted back to the Brain. */
        fun formatted(limit: Int = 10): String {
            val items = snapshot(limit)
            if (items.isEmpty()) return "No notifications captured yet."
            return "Notifications (newest first):\n" + items.joinToString("\n") { it.formatted() }
        }
    }

    override fun onListenerConnected() {
        instance = this
    }

    override fun onListenerDisconnected() {
        if (instance === this) instance = null
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        try {
            val extras = sbn.notification.extras
            val title = extras.getCharSequence("android.title")?.toString().orEmpty()
            val text = (extras.getCharSequence("android.text")
                ?: extras.getCharSequence("android.bigText"))?.toString().orEmpty()
            Log.i(TAG, "${sbn.packageName}|$title|$text")
            if (title.isBlank() && text.isBlank()) return
            synchronized(lock) {
                buffer.addLast(SeenNotification(sbn.packageName ?: "?", title, text))
                while (buffer.size > MAX_KEPT) buffer.removeFirst()
            }
        } catch (ex: Exception) {
            Log.w(TAG, "Normalization failed", ex)
        }
    }
}
