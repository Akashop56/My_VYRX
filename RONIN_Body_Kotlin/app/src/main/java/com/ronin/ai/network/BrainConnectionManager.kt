package com.ronin.ai.network

import android.content.Context
import android.content.Intent
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.withContext

class BrainConnectionManager(context: Context) {
 private val appContext = context.applicationContext
 private val startupMutex = Mutex()

 suspend fun isAvailable(): Boolean = withTimeoutOrNull(HEALTH_TIMEOUT_MS) {
  ApiClient.health()
 } == true

 suspend fun requestStartupAndCheck(): Boolean = startupMutex.withLock {
  if (isAvailable()) return@withLock true
  if (!requestStartup()) return@withLock false

  withTimeoutOrNull(STARTUP_TIMEOUT_MS) {
   var available = false
   while (!available) {
    delay(500)
    available = isAvailable()
   }
   available
  } == true
 }

 private suspend fun requestStartup(): Boolean {
  val ready = withContext(Dispatchers.IO) {
   isTermuxAvailable() && File(START_BRAIN_SCRIPT).isFile
  }
  if (!ready) return false

  return withContext(Dispatchers.Main) {
   runCatching {
    val intent = Intent(ACTION_RUN_COMMAND)
     .setClassName(TERMUX_PACKAGE, RUN_COMMAND_SERVICE)
     .putExtra(EXTRA_COMMAND_PATH, TERMUX_BASH_PATH)
     .putExtra(EXTRA_ARGUMENTS, arrayOf(START_BRAIN_SCRIPT))
     .putExtra(EXTRA_WORKDIR, BRAIN_WORKDIR)
     .putExtra(EXTRA_BACKGROUND, true)
    appContext.startService(intent) != null
   }.getOrDefault(false)
  }
 }

 private fun isTermuxAvailable(): Boolean = runCatching {
  appContext.packageManager.getApplicationInfo(TERMUX_PACKAGE, 0).enabled
 }.getOrDefault(false)

 private companion object {
  const val HEALTH_TIMEOUT_MS = 2_000L
  const val STARTUP_TIMEOUT_MS = 15_000L
  const val TERMUX_PACKAGE = "com.termux"
  const val RUN_COMMAND_SERVICE = "com.termux.app.RunCommandService"
  const val ACTION_RUN_COMMAND = "com.termux.RUN_COMMAND"
  const val EXTRA_COMMAND_PATH = "com.termux.RUN_COMMAND_PATH"
  const val EXTRA_ARGUMENTS = "com.termux.RUN_COMMAND_ARGUMENTS"
  const val EXTRA_WORKDIR = "com.termux.RUN_COMMAND_WORKDIR"
  const val EXTRA_BACKGROUND = "com.termux.RUN_COMMAND_BACKGROUND"
  const val TERMUX_BASH_PATH = "/data/data/com.termux/files/usr/bin/bash"
  const val BRAIN_WORKDIR = "/mnt/sdcard/terai/RONIN_Workspace/RONIN_Brain_Python"
  const val START_BRAIN_SCRIPT = "$BRAIN_WORKDIR/start_brain.sh"
 }
}
