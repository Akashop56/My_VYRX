package com.ronin.ai.service

/** Reserved component. Never begins recording or monitoring. */
class RoninOverlayService : android.app.Service() {
    override fun onBind(intent: android.content.Intent?): android.os.IBinder? = null
    override fun onStartCommand(intent: android.content.Intent?, flags: Int, startId: Int): Int {
        stopSelf()
        return START_NOT_STICKY
    }
}
