package com.ronin.ai.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit

object ApiClient {
 private val client = OkHttpClient.Builder().connectTimeout(8,TimeUnit.SECONDS).readTimeout(45,TimeUnit.SECONDS).build()
 private const val BASE = "http://127.0.0.1:8000"
 private val json = "application/json; charset=utf-8".toMediaType()
 suspend fun ask(message:String, sessionId:String="default", providers: List<ProviderConfig> = emptyList()): AskResponse = withContext(Dispatchers.IO) {
  val configuredProviders = org.json.JSONArray()
  providers.filter { it.enabled }.forEach { configuredProviders.put(JSONObject().put("provider", it.type.wireName).put("api_key", it.apiKey).putOpt("endpoint", it.endpoint).putOpt("model", it.model)) }
  val body=JSONObject().put("message",message).put("session_id",sessionId).put("providers", configuredProviders).toString().toRequestBody(json)
  execute("$BASE/ask_ronin",body).let(::parseAsk)
 }
 suspend fun approve(approved:Boolean, proposal:UpdateProposal): ApprovalResponse = withContext(Dispatchers.IO) {
  val p=JSONObject().put("proposal_id",proposal.proposal_id).put("file_path",proposal.file_path).put("module_name",proposal.module_name).put("new_code",proposal.new_code).put("summary",proposal.summary)
  val raw=execute("$BASE/approve_update",JSONObject().put("approved",approved).put("proposal",p).toString().toRequestBody(json)); val o=JSONObject(raw)
  ApprovalResponse(o.getBoolean("accepted"),o.getBoolean("success"),o.getString("message"))
 }
 suspend fun health(): Boolean = withContext(Dispatchers.IO) {
  try { client.newCall(Request.Builder().url("$BASE/health").get().build()).execute().use { response -> response.isSuccessful && JSONObject(response.body?.string().orEmpty()).optString("status") == "ok" } } catch (_: IOException) { false }
 }
 private fun execute(url:String, body:RequestBody):String { client.newCall(Request.Builder().url(url).post(body).build()).execute().use { r -> val t=r.body?.string().orEmpty(); if(!r.isSuccessful) throw IOException("HTTP ${r.code}: $t"); if(t.isBlank()) throw IOException("Empty response"); return t } }
 private fun parseAsk(raw:String):AskResponse { val o=JSONObject(raw); val c=o.optJSONObject("command")?.let{AndroidCommand(it.getString("action"),it.optString("text").takeIf { value -> value.isNotBlank() },it.optString("package_name").takeIf { value -> value.isNotBlank() })}; val p=o.optJSONObject("update_proposal")?.let{UpdateProposal(it.getString("proposal_id"),it.getString("file_path"),it.getString("module_name"),it.getString("new_code"),it.getString("summary"))}; return AskResponse(o.getString("response"),o.getString("route"),c,p,o.optString("error").takeIf { value -> value.isNotBlank() }) }
}
