package com.ronin.ai.data

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.ronin.ai.network.ProviderConfig
import com.ronin.ai.network.ProviderType
import org.json.JSONArray
import org.json.JSONObject

class ProviderRepository(context: Context) {
 private val preferences = EncryptedSharedPreferences.create(context, "ronin_provider_credentials", MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(), EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV, EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM)
 fun load(): List<ProviderConfig> = runCatching { JSONArray(preferences.getString("providers", "[]")).let { entries -> List(entries.length()) { index -> entries.getJSONObject(index).toConfig() } } }.getOrDefault(emptyList())
 fun save(providers: List<ProviderConfig>) { val entries = JSONArray(); providers.forEach { entries.put(it.toJson()) }; preferences.edit().putString("providers", entries.toString()).apply() }
 private fun ProviderConfig.toJson() = JSONObject().put("id", id).put("type", type.name).put("name", name).put("apiKey", apiKey).putOpt("endpoint", endpoint).putOpt("model", model).put("enabled", enabled)
 private fun JSONObject.toConfig() = ProviderConfig(getString("id"), ProviderType.valueOf(getString("type")), getString("name"), getString("apiKey"), optString("endpoint").ifBlank { null }, optString("model").ifBlank { null }, optBoolean("enabled", true))
}
