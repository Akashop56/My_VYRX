package com.ronin.ai

import android.app.Application
import com.ronin.ai.data.AppSettingsRepository

class RoninApp : Application() {
    val settingsRepository by lazy { AppSettingsRepository(this) }
}
