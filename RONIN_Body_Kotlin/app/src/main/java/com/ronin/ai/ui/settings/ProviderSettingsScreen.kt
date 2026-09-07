package com.ronin.ai.ui.settings

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import com.ronin.ai.network.ProviderConfig
import com.ronin.ai.network.ProviderType
import java.util.UUID

@Composable fun ProviderSettingsScreen(onBack: () -> Unit, viewModel: ProviderSettingsViewModel = viewModel()) {
 var editing by remember { mutableStateOf<ProviderConfig?>(null) }; var creating by remember { mutableStateOf(false) }
 Column(Modifier.fillMaxSize().padding(16.dp)) {
  Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text("AI Providers", style = MaterialTheme.typography.headlineSmall); TextButton(onClick = onBack) { Text("Done") } }
  Text("The first enabled provider is primary; later entries are automatic fallbacks.", style = MaterialTheme.typography.bodySmall)
  Spacer(Modifier.height(12.dp))
  LazyColumn(Modifier.weight(1f)) { items(viewModel.providers, key = { it.id }) { provider ->
   ListItem(headlineContent = { Text(provider.name) }, supportingContent = { Text(provider.type.label + if (provider.enabled) "" else " (disabled)") }, trailingContent = { Row { TextButton(onClick = { editing = provider }) { Text("Edit") }; TextButton(onClick = { viewModel.delete(provider.id) }) { Text("Delete") } } })
  } }
  Button(onClick = { creating = true }, modifier = Modifier.fillMaxWidth()) { Text("Add provider") }
 }
 val selected = editing ?: if (creating) ProviderConfig(UUID.randomUUID().toString(), ProviderType.OPENAI, "OpenAI", "") else null
 selected?.let { ProviderEditor(it, onSave = { viewModel.upsert(it); editing = null; creating = false }, onDismiss = { editing = null; creating = false }) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun ProviderEditor(initial: ProviderConfig, onSave: (ProviderConfig) -> Unit, onDismiss: () -> Unit) {
 var type by remember(initial.id) { mutableStateOf(initial.type) }; var name by remember(initial.id) { mutableStateOf(initial.name) }; var key by remember(initial.id) { mutableStateOf(initial.apiKey) }; var endpoint by remember(initial.id) { mutableStateOf(initial.endpoint.orEmpty()) }; var model by remember(initial.id) { mutableStateOf(initial.model.orEmpty()) }; var enabled by remember(initial.id) { mutableStateOf(initial.enabled) }; var expanded by remember { mutableStateOf(false) }
 AlertDialog(onDismissRequest = onDismiss, title = { Text("Provider") }, text = { Column {
  ExposedDropdownMenuBox(expanded = expanded, onExpandedChange = { expanded = it }) { OutlinedTextField(type.label, {}, Modifier.menuAnchor().fillMaxWidth(), label = { Text("Provider") }, readOnly = true); ExposedDropdownMenu(expanded, { expanded = false }) { ProviderType.entries.forEach { choice -> DropdownMenuItem({ Text(choice.label) }, { type = choice; if (name.isBlank() || name == initial.type.label) name = choice.label; expanded = false }) } } }
  OutlinedTextField(name, { name = it }, label = { Text("Name") }, modifier = Modifier.fillMaxWidth())
  OutlinedTextField(key, { key = it }, label = { Text("API key") }, modifier = Modifier.fillMaxWidth(), visualTransformation = androidx.compose.ui.text.input.PasswordVisualTransformation())
  if (type == ProviderType.CUSTOM) OutlinedTextField(endpoint, { endpoint = it }, label = { Text("Chat completions endpoint") }, modifier = Modifier.fillMaxWidth())
  OutlinedTextField(model, { model = it }, label = { Text("Model (optional)") }, modifier = Modifier.fillMaxWidth())
  Row { Checkbox(enabled, { enabled = it }); Text("Enabled") }
 } }, confirmButton = { TextButton(enabled = name.isNotBlank() && key.isNotBlank() && (type != ProviderType.CUSTOM || endpoint.isNotBlank()), onClick = { onSave(ProviderConfig(initial.id, type, name, key, endpoint.ifBlank { null }, model.ifBlank { null }, enabled)) }) { Text("Save") } }, dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } })
}
