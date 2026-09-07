package com.ronin.ai.network

data class AskRequest(val message: String, val session_id: String = "default")
data class AndroidCommand(val action: String, val text: String? = null, val package_name: String? = null)
data class UpdateProposal(val proposal_id: String, val file_path: String, val module_name: String, val new_code: String, val summary: String)
data class AskResponse(val response: String, val route: String, val command: AndroidCommand? = null, val update_proposal: UpdateProposal? = null, val error: String? = null)
data class ApprovalRequest(val approved: Boolean, val proposal: UpdateProposal)
data class ApprovalResponse(val accepted: Boolean, val success: Boolean, val message: String)
enum class ProviderType(val wireName: String, val label: String) {
 OPENAI("openai", "OpenAI"), GEMINI("gemini", "Gemini"), GROQ("groq", "Groq"), OPENROUTER("openrouter", "OpenRouter"), CUSTOM("custom", "Custom API endpoint")
}
data class ProviderConfig(val id: String, val type: ProviderType, val name: String, val apiKey: String, val endpoint: String? = null, val model: String? = null, val enabled: Boolean = true)
