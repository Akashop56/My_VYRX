package com.ronin.ai.ui.chat

import android.content.Context
import android.speech.tts.TextToSpeech
import java.util.Locale

/**
 * Lifecycle-owned native Android speech output for completed assistant turns.
 *
 * TextToSpeech initializes asynchronously, so a response that arrives before
 * the engine is ready is retained and spoken as soon as initialization ends.
 * This class receives only final conversational text from [ChatController],
 * never the live ThoughtTerminal/action-log stream.
 */
class VoiceManager(context: Context) : TextToSpeech.OnInitListener {
    private val appContext = context.applicationContext
    private var engine: TextToSpeech? = TextToSpeech(appContext, this)
    private var initialized = false
    private var released = false
    private var pendingSpeech: String? = null

    override fun onInit(status: Int) {
        if (released) return
        if (status != TextToSpeech.SUCCESS) {
            pendingSpeech = null
            engine?.shutdown()
            engine = null
            return
        }

        initialized = true
        engine?.let { tts ->
            val locale = Locale.getDefault()
            if (tts.isLanguageAvailable(locale) >= TextToSpeech.LANG_AVAILABLE) {
                tts.language = locale
            }
        }
        pendingSpeech?.also {
            pendingSpeech = null
            speakNow(it)
        }
    }

    /** Queue the final assistant reply, replacing any older unfinished speech. */
    fun speak(finalResponse: String) {
        val text = finalResponse.trim()
        if (released || text.isEmpty()) return
        if (!initialized) {
            pendingSpeech = text
            return
        }
        speakNow(text)
    }

    private fun speakNow(text: String) {
        val tts = engine ?: return
        tts.speak(
            text,
            TextToSpeech.QUEUE_FLUSH,
            null,
            "vyrx-final-${System.nanoTime()}"
        )
    }

    /** Stop audio and release the engine when the shared controller is disposed. */
    fun shutdown() {
        released = true
        pendingSpeech = null
        initialized = false
        engine?.stop()
        engine?.shutdown()
        engine = null
    }
}
