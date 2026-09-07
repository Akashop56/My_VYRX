package com.ronin.ai.ui.settings

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.ronin.ai.data.ProviderRepository
import com.ronin.ai.network.ProviderConfig

class ProviderSettingsViewModel(application: Application) : AndroidViewModel(application) {
 private val repository = ProviderRepository(application)
 var providers: List<ProviderConfig> by mutableStateOf(repository.load()); private set
 fun upsert(provider: ProviderConfig) {
  val index = providers.indexOfFirst { it.id == provider.id }
  providers = if (index < 0) providers + provider else providers.toMutableList().also { it[index] = provider }
  repository.save(providers)
 }
 fun delete(id: String) { providers = providers.filterNot { it.id == id }; repository.save(providers) }
}
