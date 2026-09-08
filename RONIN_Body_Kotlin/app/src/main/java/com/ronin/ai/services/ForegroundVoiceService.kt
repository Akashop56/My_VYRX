package com.ronin.ai.services

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.Bundle
import android.os.IBinder
import android.os.ResultReceiver
import com.ronin.ai.RoninApp
import com.ronin.ai.ui.chat.VoiceInput

/** One user-initiated utterance, never an always-on microphone. */
class ForegroundVoiceService : Service() {
    private var receiver: ResultReceiver? = null

    override fun onBind(intent: Intent?): IBinder? = null

    @Suppress("DEPRECATION")
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        receiver = intent?.getParcelableExtra("receiver")
        try {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel("voice", "Voice input", NotificationManager.IMPORTANCE_LOW))
            startForeground(41, Notification.Builder(this, "voice")
                .setSmallIcon(android.R.drawable.ic_btn_speak_now)
                .setContentTitle("VYRX is listening")
                .setContentText("Recording one voice command")
                .setOngoing(true).build())
            if (!(application as RoninApp).settingsRepository.settings.value.voiceEnabled) {
                finish("Voice Assistant is disabled in Settings.", false)
            } else {
                VoiceInput.start(this,
                    onResult = { finish(it, true) },
                    onError = { finish(it, false) })
            }
        } catch (e: Exception) {
            finish("Unable to start microphone: ${e.localizedMessage}", false)
        }
        return START_NOT_STICKY
    }

    private fun finish(text: String, success: Boolean) {
        receiver?.send(if (success) 1 else 0, Bundle().apply { putString("text", text) })
        receiver = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        VoiceInput.stop()
        receiver = null
        super.onDestroy()
    }
}
