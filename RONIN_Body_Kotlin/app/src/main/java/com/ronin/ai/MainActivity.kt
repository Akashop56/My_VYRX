package com.ronin.ai
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import com.ronin.ai.ui.ChatScreen
import com.ronin.ai.ui.settings.ProviderSettingsScreen
import com.ronin.ai.ui.settings.ProviderSettingsViewModel
import androidx.compose.runtime.*
import androidx.lifecycle.viewmodel.compose.viewModel
class MainActivity:ComponentActivity(){ override fun onCreate(savedInstanceState:Bundle?){super.onCreate(savedInstanceState);setContent{
 var settings by remember { mutableStateOf(false) }; val providers: ProviderSettingsViewModel = viewModel()
 if(settings) ProviderSettingsScreen(onBack={settings=false}, viewModel=providers) else ChatScreen(providers=providers.providers, onOpenSettings={settings=true})
}} }
