package com.ronin.ai

import androidx.activity.compose.setContent
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.launch
import android.os.Bundle
import android.widget.Toast
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricManager.Authenticators.BIOMETRIC_STRONG
import androidx.biometric.BiometricManager.Authenticators.DEVICE_CREDENTIAL
import androidx.biometric.BiometricPrompt
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Fingerprint
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.fragment.app.FragmentActivity
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.ronin.ai.data.AppSettingsRepository
import com.ronin.ai.data.BrainRepository
import com.ronin.ai.data.ProviderRepository
import com.ronin.ai.ui.ChatScreen
import com.ronin.ai.ui.dashboard.DashboardScreen
import com.ronin.ai.ui.drawer.VyRxDrawerContent
import com.ronin.ai.ui.home.HomeScreen
import com.ronin.ai.ui.memory.MemoryScreen
import com.ronin.ai.ui.navigation.OrbTap
import com.ronin.ai.ui.navigation.VyRxBottomBar
import com.ronin.ai.ui.providers.ApiProvidersScreen
import com.ronin.ai.ui.security.SecurityScreen
import com.ronin.ai.ui.settings.ProviderSettingsScreen
import com.ronin.ai.ui.settings.SettingsScreen
import com.ronin.ai.ui.theme.VyRxColors
import com.ronin.ai.ui.theme.VyRxTheme
import com.ronin.ai.ui.chat.ChatController
import com.ronin.ai.ui.tools.ToolsScreen

/**
 * VYRX — personal autonomous AI assistant (Body side of the Brain-Body split).
 *
 * FragmentActivity (not ComponentActivity) so AndroidX BiometricPrompt can be
 * used for the App Lock feature.
 */
open class MainActivity : FragmentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            VyRxApp(activity = this)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun VyRxApp(activity: FragmentActivity) {
    val context = activity
    val settingsRepo = remember { (context.application as RoninApp).settingsRepository }
    val providerRepo = remember { ProviderRepository(context) }
    val controller = remember {
        ChatController(
            context = context,
            providersProvider = { providerRepo.load() },
            settingsProvider = { settingsRepo.settings.value }
        )
    }
    androidx.compose.runtime.DisposableEffect(controller) {
        onDispose { controller.dispose() }
    }
    val settings by settingsRepo.settings.collectAsState()

    val navController = rememberNavController()
    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route ?: "home"

    LaunchedEffect(Unit) {
        BrainRepository.connect()
    }

    fun toast(message: String) {
        Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
    }

    val onNavigate: (String) -> Unit = { route ->
        if (route != currentRoute) {
            navController.navigate(route) {
                popUpTo("home") { saveState = true }
                launchSingleTop = true
                restoreState = true
            }
        }
        scope.launch { drawerState.close() }
    }

    if (settings.biometricLock && !AppLockState.sessionUnlocked) {
        BiometricGate(activity = activity, onToast = ::toast)
        return
    }

    VyRxTheme(dark = settings.darkMode, accent = settings.accent, fontScale = settings.fontScale) {
        ModalNavigationDrawer(
            drawerState = drawerState,
            gesturesEnabled = true,
            drawerContent = {
                ModalDrawerSheet(
                    Modifier,
                    RoundedCornerShape(0.dp),
                    Color(0xFF0A0D14)
                ) {
                    VyRxDrawerContent(
                        currentRoute = currentRoute,
                        onNavigate = onNavigate,
                        onToast = ::toast
                    )
                }
            }
        ) {
            Scaffold(
                containerColor = VyRxColors.Background,
                bottomBar = {
                    if (currentRoute !in listOf("provider_manager")) {
                        VyRxBottomBar(
                            currentRoute = currentRoute,
                            onNavigate = onNavigate,
                            onOrbTap = { mode ->
                                if (mode == OrbTap.LONG) {
                                    controller.prefill = "Emergency: "
                                    toast("Emergency mode — tell VYRX what's wrong.")
                                }
                                onNavigate("chat")
                            }
                        )
                    }
                }
            ) { innerPadding ->
                // Apply the Scaffold's PaddingValues here so every screen
                // (Home, Chat, Dashboard, Memory, Tools, Settings, Providers)
                // lays out inside the padded area and never draws behind the
                // top area / status bar or the bottom navigation bar.
                Box(
                    Modifier
                        .fillMaxSize()
                        .background(VyRxColors.Background)
                        .padding(innerPadding)
                ) {
                    NavHost(
                        navController = navController,
                        startDestination = "home",
                        modifier = Modifier.fillMaxSize()
                    ) {
                        composable("home") {
                            HomeScreen(
                                controller = controller,
                                onOpenDrawer = { scope.launch { drawerState.open() } },
                                onNavigate = onNavigate,
                                onOrbTap = { onNavigate("chat") },
                                onOrbLongPress = {
                                    controller.prefill = "Emergency: "
                                    onNavigate("chat")
                                },
                                onVoiceStart = { toast("Speak now — listening…") }
                            )
                        }
                        composable("chat") {
                            ChatScreen(
                                controller = controller,
                                onOpenDrawer = { scope.launch { drawerState.open() } },
                                onNavigate = onNavigate,
                                onVoiceStart = { toast("Speak now — listening…") }
                            )
                        }
                        composable("dashboard") {
                            DashboardScreen(
                                onOpenDrawer = { scope.launch { drawerState.open() } },
                                onNavigate = onNavigate
                            )
                        }
                        composable("memory") {
                            MemoryScreen(
                                onOpenDrawer = { scope.launch { drawerState.open() } },
                                onToast = ::toast
                            )
                        }
                        composable("tools") {
                            ToolsScreen(
                                settingsRepo = settingsRepo,
                                onOpenDrawer = { scope.launch { drawerState.open() } }
                            )
                        }
                        composable("settings") {
                            SettingsScreen(
                                settingsRepo = settingsRepo,
                                activity = activity,
                                onNavigate = onNavigate,
                                onToast = ::toast
                            )
                        }
                        composable("providers") {
                            ApiProvidersScreen(
                                settingsRepo = settingsRepo,
                                onOpenDrawer = { scope.launch { drawerState.open() } },
                                onNavigate = onNavigate,
                                onToast = ::toast
                            )
                        }
                        composable("provider_manager") {
                            ProviderSettingsScreen(onBack = { navController.popBackStack() })
                        }
                        composable("security") {
                            SecurityScreen(
                                settingsRepo = settingsRepo,
                                onOpenDrawer = { scope.launch { drawerState.open() } },
                                onToast = ::toast
                            )
                        }
                    }
                }
            }
        }
    }
}

/** Session-scoped unlock state (per app process, never persisted). */
object AppLockState {
    @Volatile
    var sessionUnlocked: Boolean = false
}

/** Full-screen gate shown while App Lock (biometric) is enabled. */
@Composable
private fun BiometricGate(activity: FragmentActivity, onToast: (String) -> Unit) {
    var failed by remember { mutableStateOf(false) }

    Column(
        Modifier
            .fillMaxSize()
            .background(VyRxColors.Background),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {
        Box(
            Modifier
                .size(72.dp)
                .clip(RoundedCornerShape(20.dp))
                .background(VyRxColors.Primary.copy(alpha = 0.15f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(Icons.Filled.Fingerprint, null, tint = VyRxColors.PrimaryBright, modifier = Modifier.size(36.dp))
        }
        Spacer(Modifier.size(16.dp))
        Text("VYRX is locked", color = VyRxColors.TextPrimary, fontSize = 20.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.size(8.dp))
        Text(
            "Verify your identity to continue",
            color = VyRxColors.TextDim,
            fontSize = 12.sp
        )
        if (failed) {
            Spacer(Modifier.size(8.dp))
            Text("Unlock failed. Try again.", color = VyRxColors.Red, fontSize = 11.sp)
        }
        Spacer(Modifier.size(24.dp))
        Button(
            onClick = {
                val manager = BiometricManager.from(activity)
                if (manager.canAuthenticate(BIOMETRIC_STRONG or DEVICE_CREDENTIAL) != BiometricManager.BIOMETRIC_SUCCESS) {
                    onToast("No biometric enrolled — disable App Lock in Settings first.")
                    return@Button
                }
                val callback = object : BiometricPrompt.AuthenticationCallback() {
                    override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                        // Verified for this session — parent recomposes into the app.
                        AppLockState.sessionUnlocked = true
                    }

                    override fun onAuthenticationFailed() {
                        failed = true
                    }

                    override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                        failed = true
                        onToast("App lock unavailable: $errString")
                    }
                }
                val info = BiometricPrompt.PromptInfo.Builder()
                    .setTitle("Unlock VYRX")
                    .setSubtitle("Personal AI Assistant")
                    .setAllowedAuthenticators(BIOMETRIC_STRONG or DEVICE_CREDENTIAL)
                    .build()
                BiometricPrompt(activity, callback).authenticate(info)
            },
            colors = ButtonDefaults.buttonColors(containerColor = VyRxColors.Primary, contentColor = Color.White)
        ) {
            Text("Unlock", fontWeight = FontWeight.SemiBold)
        }
    }
}
