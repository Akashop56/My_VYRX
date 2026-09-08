package com.ronin.ai.data

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONObject

/** All VYRX body-side settings, persisted in EncryptedSharedPreferences. */
data class AppSettings(
    val username: String = "Akash",
    val personality: String = "assistant",     // assistant | professional | friendly | creative | developer
    val responseMode: String = "balanced",     // fast | balanced | deep
    val voiceEnabled: Boolean = true,
    val backgroundAgent: Boolean = true,
    val autoStart: Boolean = false,
    val backgroundActivity: Boolean = true,
    val darkMode: Boolean = true,
    val accent: String = "purple",             // purple | blue | green | amber
    val fontScale: Float = 1.0f,               // 0.85 | 1.0 | 1.15
    val biometricLock: Boolean = false,
    val appNotifications: Boolean = true,
    val taskNotifications: Boolean = true,
    val systemAlerts: Boolean = true,
    val notificationStyle: String = "detailed",
    val toolsEnabled: Map<String, Boolean> = defaultTools()
) {
    companion object {
        val ALL_TOOLS = listOf(
            "app_control", "web_search", "code_executor", "file_manager", "task_automation",
            "notification_manager", "telegram", "email", "system_monitor", "custom_api",
            "note_creator", "image_analyzer"
        )

        fun defaultTools(): Map<String, Boolean> = ALL_TOOLS.associateWith { it != "custom_api" }
    }
}

class AppSettingsRepository(context: Context) {

    private val prefs: SharedPreferences = runCatching {
        EncryptedSharedPreferences.create(
            context, "vyrx_settings",
            MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }.getOrElse {
        context.getSharedPreferences("vyrx_settings_plain", Context.MODE_PRIVATE)
    }

    private val _settings = MutableStateFlow(load())
    val settings: StateFlow<AppSettings> = _settings.asStateFlow()

    fun update(transform: (AppSettings) -> AppSettings) {
        _settings.update { current ->
            val next = transform(current)
            save(next)
            next
        }
    }

    fun setToolEnabled(toolId: String, enabled: Boolean) {
        update { s -> s.copy(toolsEnabled = s.toolsEnabled + (toolId to enabled)) }
    }

    fun resetAll() {
        prefs.edit().clear().apply()
        _settings.value = AppSettings()
    }

    private fun load(): AppSettings {
        val tools = runCatching {
            val obj = JSONObject(prefs.getString("tools", "{}"))
            val map = AppSettings.defaultTools().toMutableMap()
            obj.keys().forEach { key -> map[key] = obj.getBoolean(key) }
            map
        }.getOrElse { AppSettings.defaultTools() }
        return AppSettings(
            username = prefs.getString("username", "Akash") ?: "Akash",
            personality = prefs.getString("personality", "assistant") ?: "assistant",
            responseMode = prefs.getString("response_mode", "balanced") ?: "balanced",
            voiceEnabled = prefs.getBoolean("voice_enabled", true),
            backgroundAgent = prefs.getBoolean("background_agent", true),
            autoStart = prefs.getBoolean("auto_start", false),
            backgroundActivity = prefs.getBoolean("background_activity", true),
            darkMode = prefs.getBoolean("dark_mode", true),
            accent = prefs.getString("accent", "purple") ?: "purple",
            fontScale = prefs.getFloat("font_scale", 1.0f),
            biometricLock = prefs.getBoolean("biometric_lock", false),
            appNotifications = prefs.getBoolean("app_notifications", true),
            taskNotifications = prefs.getBoolean("task_notifications", true),
            systemAlerts = prefs.getBoolean("system_alerts", true),
            notificationStyle = prefs.getString("notification_style", "detailed") ?: "detailed",
            toolsEnabled = tools
        )
    }

    private fun save(s: AppSettings) {
        val tools = JSONObject()
        s.toolsEnabled.forEach { (k, v) -> tools.put(k, v) }
        prefs.edit()
            .putString("username", s.username)
            .putString("personality", s.personality)
            .putString("response_mode", s.responseMode)
            .putBoolean("voice_enabled", s.voiceEnabled)
            .putBoolean("background_agent", s.backgroundAgent)
            .putBoolean("auto_start", s.autoStart)
            .putBoolean("background_activity", s.backgroundActivity)
            .putBoolean("dark_mode", s.darkMode)
            .putString("accent", s.accent)
            .putFloat("font_scale", s.fontScale)
            .putBoolean("biometric_lock", s.biometricLock)
            .putBoolean("app_notifications", s.appNotifications)
            .putBoolean("task_notifications", s.taskNotifications)
            .putBoolean("system_alerts", s.systemAlerts)
            .putString("notification_style", s.notificationStyle)
            .putString("tools", tools.toString())
            .apply()
    }
}
