package com.ronin.ai.ui
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.ronin.ai.network.*
import com.ronin.ai.ui.components.MessageBubble
import com.ronin.ai.utils.CommandExecutor
import kotlinx.coroutines.launch

data class ChatMessage(val text:String,val mine:Boolean)

private enum class BrainStatus { CHECKING, STARTING, ONLINE, OFFLINE }

@Composable fun ChatScreen(providers: List<ProviderConfig>, onOpenSettings: () -> Unit){
 var input by remember{mutableStateOf("")}; var loading by remember{mutableStateOf(false)}; var error by remember{mutableStateOf<String?>(null)}; var proposal by remember{mutableStateOf<UpdateProposal?>(null)}; val messages=remember{mutableStateListOf(ChatMessage("RONIN online. How can I help?",false))}; val scope=rememberCoroutineScope(); val context=androidx.compose.ui.platform.LocalContext.current
 var brainStatus by remember { mutableStateOf(BrainStatus.CHECKING) }
 val connection = remember(context) { BrainConnectionManager(context) }
 suspend fun ensureBrainStarted() {
  brainStatus = BrainStatus.CHECKING
  brainStatus = if (connection.isAvailable()) {
   BrainStatus.ONLINE
  } else {
   brainStatus = BrainStatus.STARTING
   if (connection.requestStartupAndCheck()) BrainStatus.ONLINE else BrainStatus.OFFLINE
  }
 }
 LaunchedEffect(connection) { ensureBrainStarted() }
 MaterialTheme(colorScheme=darkColorScheme(primary=Color(0xFF9D7BFF),background=Color(0xFF0B0D12),surface=Color(0xFF151922))) {
  Surface(Modifier.fillMaxSize()) {
   Column(Modifier.padding(16.dp)) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) { Text("RONIN",style=MaterialTheme.typography.headlineMedium,color=Color.White); TextButton(onClick=onOpenSettings) { Text("AI Providers") } }
    Row { Text(when (brainStatus) { BrainStatus.ONLINE -> "Brain connected"; BrainStatus.STARTING -> "Starting Brain..."; BrainStatus.OFFLINE -> "Brain unavailable — Termux could not start the local Brain."; BrainStatus.CHECKING -> "Checking local Brain..." }, color = if(brainStatus == BrainStatus.OFFLINE) MaterialTheme.colorScheme.error else Color.LightGray, style = MaterialTheme.typography.bodySmall); if(brainStatus == BrainStatus.OFFLINE) TextButton(onClick={scope.launch { ensureBrainStarted() }}) { Text("Retry") } }
    Spacer(Modifier.height(12.dp))
    LazyColumn(Modifier.weight(1f)) { items(messages) { MessageBubble(it.text,it.mine) } }
    error?.let { Text(it,color=MaterialTheme.colorScheme.error) }
    if(loading || brainStatus == BrainStatus.STARTING || brainStatus == BrainStatus.CHECKING) LinearProgressIndicator(Modifier.fillMaxWidth())
    Row {
     OutlinedTextField(input,{input=it},Modifier.weight(1f),label={Text("Message RONIN")},enabled=!loading)
     Spacer(Modifier.width(8.dp))
     Button(enabled=input.isNotBlank()&&!loading&&brainStatus == BrainStatus.ONLINE,onClick={
      val prompt=input
      input=""
      messages.add(ChatMessage(prompt,true))
      loading=true
      error=null
      scope.launch {
       try {
        val r=ApiClient.ask(prompt, providers=providers)
        messages.add(ChatMessage(r.response,false))
        r.command?.let {
         val executed=CommandExecutor.execute(context,it).getOrDefault(false)
         if(!executed) messages.add(ChatMessage("Android command was prepared but could not run; enable Accessibility or required permissions.",false))
        }
        proposal=r.update_proposal
       } catch(e:Exception) {
        brainStatus=BrainStatus.OFFLINE
        error=e.message?:"Connection to RONIN Brain failed."
       } finally {
        loading=false
       }
      }
     }) { Text("Send") }
    }
   }
  }
 }
 proposal?.let{p->ApprovalDialog(p,onApprove={scope.launch{try{val r=ApiClient.approve(true,p);messages.add(ChatMessage(r.message,false))}catch(e:Exception){error=e.message}finally{proposal=null}}},onReject={scope.launch{try{val r=ApiClient.approve(false,p);messages.add(ChatMessage(r.message,false))}catch(e:Exception){error=e.message}finally{proposal=null}}})}
}
