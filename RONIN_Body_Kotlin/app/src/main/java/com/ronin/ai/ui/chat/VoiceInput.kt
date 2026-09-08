package com.ronin.ai.ui.chat

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import java.util.Locale

/** Real device speech recognition via Android's SpeechRecognizer. */
object VoiceInput {
    var isListening by mutableStateOf(false); private set
    private var recognizer: SpeechRecognizer? = null

    fun hasPermission(context: Context): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    fun requestPermission(context: Context) {
        if (!hasPermission(context)) {
            (context as? android.app.Activity)?.requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 42)
        }
    }

    fun start(context: Context, onResult: (String) -> Unit, onError: (String) -> Unit) {
        stop()
        val appContext = context.applicationContext
        if (!hasPermission(appContext)) {
            onError("Microphone permission is required for voice input.")
            return
        }
        if (!SpeechRecognizer.isRecognitionAvailable(appContext)) {
            onError("Speech recognition is not available on this device.")
            return
        }
        val rec = SpeechRecognizer.createSpeechRecognizer(appContext)
        recognizer = rec
        isListening = true
        rec.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: android.os.Bundle?) {}
            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {
                // Keep the session active while final transcription is pending.
            }
            override fun onError(error: Int) {
                isListening = false
                recognizer?.destroy()
                recognizer = null
                onError("Voice input failed (error $error).")
            }
            override fun onResults(results: android.os.Bundle?) {
                isListening = false
                recognizer?.destroy()
                recognizer = null
                val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                val text = matches?.firstOrNull()?.trim().orEmpty()
                if (text.isBlank()) onError("Nothing heard. Try again.") else onResult(text)
            }
            override fun onPartialResults(partialResults: android.os.Bundle?) {}
            override fun onEvent(eventType: Int, params: android.os.Bundle?) {}
        })
        val intent = android.content.Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
        }
        rec.startListening(intent)
    }

    fun stop() {
        try {
            recognizer?.cancel()
            recognizer?.destroy()
        } catch (_: Exception) { /* already destroyed */ }
        recognizer = null
        isListening = false
    }
}
