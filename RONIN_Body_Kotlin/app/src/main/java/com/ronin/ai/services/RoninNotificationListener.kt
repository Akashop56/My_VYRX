package com.ronin.ai.services

import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log

class RoninNotificationListener : NotificationListenerService() {
 override fun onNotificationPosted(sbn: StatusBarNotification) { try { val e=sbn.notification.extras; Log.i("RONIN_NOTIFICATION", "${sbn.packageName}|${e.getCharSequence("android.title")}|${e.getCharSequence("android.text")}") } catch (ex: Exception) { Log.w("RONIN_NOTIFICATION","Normalization failed",ex) } }
}
