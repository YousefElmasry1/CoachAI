package com.coachai.android

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.remember
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.ime
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.credentials.ClearCredentialStateRequest
import androidx.credentials.CredentialManager
import com.coachai.android.ui.home.HomeWebView
import com.coachai.android.ui.analytics.AnalyticsWebView
import com.coachai.android.ui.auth.AuthScreen
import com.coachai.android.ui.auth.AuthViewModel
import com.coachai.android.ui.auth.PasswordResetLinkState
import com.coachai.android.ui.ask.AskCoachScreen
import com.coachai.android.ui.ask.AskCoachViewModel
import com.coachai.android.ui.home.HomeUiState
import com.coachai.android.ui.home.HomeViewModel
import com.coachai.android.domain.TodayPlanState
import com.coachai.android.ui.coach.CoachReportScreen
import com.coachai.android.ui.coach.CoachReportViewModel
import com.coachai.android.ui.calendar.CalendarConnectionsScreen
import com.coachai.android.ui.calendar.CalendarConnectionsViewModel
import com.coachai.android.ui.navigation.AppBottomNavigation
import com.coachai.android.ui.navigation.AppDestination
import com.coachai.android.ui.navigation.navigateBackStack
import com.coachai.android.ui.navigation.navigateToStack
import com.coachai.android.ui.more.MoreScreen
import com.coachai.android.ui.profile.ProfileScreen
import com.coachai.android.ui.profile.ProfileViewModel
import com.coachai.android.ui.profile.ProfileUiState
import com.coachai.android.ui.notifications.NotificationPreferencesScreen
import com.coachai.android.ui.notifications.NotificationPreferencesViewModel
import com.coachai.android.ui.schedule.ScheduleScreen
import com.coachai.android.ui.schedule.ScheduleSessionState
import com.coachai.android.ui.schedule.ScheduleSyncViewModel
import com.coachai.android.ui.schedule.toHomeTask
import com.coachai.android.ui.future.FuturePlanningScreen
import com.coachai.android.ui.future.FuturePlanningViewModel
import com.coachai.android.ui.settings.AppSettingsScreen
import com.coachai.android.ui.settings.AppSettingsViewModel
import com.coachai.android.ui.theme.Background
import com.coachai.android.ui.theme.CoachAiTheme
import com.coachai.android.ui.theme.TextPrimary
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private val calendarOAuthCallback = MutableStateFlow<Uri?>(null)
    private val passwordResetCallback = MutableStateFlow<Uri?>(null)
    private val notificationDestination = MutableStateFlow<AppDestination?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        calendarOAuthCallback.value = intent?.data
        passwordResetCallback.value = intent?.data
        notificationDestination.value = destinationFromNotification(intent)
        enableEdgeToEdge()
        setContent {
            val authViewModel: AuthViewModel = viewModel()
            val authState by authViewModel.uiState.collectAsStateWithLifecycle()
            val passwordResetLink by passwordResetCallback.collectAsStateWithLifecycle()
            LaunchedEffect(passwordResetLink) {
                authViewModel.handlePasswordResetLink(passwordResetLink)
                passwordResetCallback.value = null
            }
            val appSettingsViewModel: AppSettingsViewModel = viewModel()
            val appSettingsState by appSettingsViewModel.uiState.collectAsStateWithLifecycle()
            val notificationPermissionLauncher = rememberLauncherForActivityResult(
                ActivityResultContracts.RequestPermission(),
            ) { }
            val container = (application as CoachAiApplication).container
            LaunchedEffect(authState.signedIn) {
                if (authState.signedIn) {
                    container.notificationDeviceRegistrar.registerCurrentToken()
                }
            }
            val showingAuth = !authState.signedIn || authState.passwordResetLinkState != PasswordResetLinkState.None
            val lightTheme = showingAuth || appSettingsState.theme == "Light"
            CoachAiTheme(lightTheme = lightTheme) {
                if (showingAuth) {
                    AuthScreen(
                        state = authState,
                        onSignIn = authViewModel::signIn,
                        onRegister = authViewModel::register,
                        onReset = authViewModel::resetPassword,
                        onGoogleToken = authViewModel::signInWithGoogle,
                        onGoogleError = authViewModel::reportError,
                        onConfirmPasswordReset = authViewModel::confirmPasswordReset,
                        onClosePasswordReset = authViewModel::closePasswordReset,
                        onClearFeedback = authViewModel::clearFeedback,
                    )
                } else {
                val homeViewModel: HomeViewModel = viewModel(
                    key = "home-${authState.userId}",
                    factory = HomeViewModel.Factory(
                        repository = container.homeRepository,
                        analyticsRepository = container.analyticsRepository,
                        quoteSelector = container.quoteSelector,
                    ),
                )
                val homeState by homeViewModel.uiState.collectAsStateWithLifecycle()
                val profileViewModel: ProfileViewModel = viewModel(
                    key = "profile-${authState.userId}",
                    factory = ProfileViewModel.Factory(container.profileRepository),
                )
                val profileState by profileViewModel.uiState.collectAsStateWithLifecycle()
                val notificationViewModel: NotificationPreferencesViewModel = viewModel(
                    key = "notifications-${authState.userId}",
                    factory = NotificationPreferencesViewModel.Factory(
                        container.notificationPreferencesRepository,
                    ),
                )
                val notificationState by notificationViewModel.uiState.collectAsStateWithLifecycle()
                val calendarViewModel: CalendarConnectionsViewModel = viewModel(
                    key = "calendar-${authState.userId}",
                    factory = CalendarConnectionsViewModel.Factory(container.calendarRepository),
                )
                val calendarState by calendarViewModel.uiState.collectAsStateWithLifecycle()
                val askCoachViewModel: AskCoachViewModel = viewModel(
                    key = "ask-${authState.userId}",
                    factory = AskCoachViewModel.Factory(container.coachRepository),
                )
                val askCoachState by askCoachViewModel.uiState.collectAsStateWithLifecycle()
                val coachReportViewModel: CoachReportViewModel = viewModel(
                    key = "report-${authState.userId}",
                    factory = CoachReportViewModel.Factory(container.coachRepository),
                )
                val coachReportState by coachReportViewModel.uiState.collectAsStateWithLifecycle()
                val scheduleSyncViewModel: ScheduleSyncViewModel = viewModel(
                    key = "schedule-${authState.userId}",
                    factory = ScheduleSyncViewModel.Factory(container.taskRepository),
                )
                val futurePlanningViewModel: FuturePlanningViewModel = viewModel(
                    key = "future-${authState.userId}",
                    factory = FuturePlanningViewModel.Factory(container.futurePlanningRepository),
                )
                val scheduleSession = remember { ScheduleSessionState() }
                var backStack by rememberSaveable(authState.userId) {
                    mutableStateOf(listOf(AppDestination.Home.name))
                }
                val destination = AppDestination.valueOf(backStack.last())
                val navigateTo: (AppDestination) -> Unit = { target ->
                    backStack = navigateToStack(backStack, target)
                }
                val navigateBack: () -> Unit = {
                    backStack = navigateBackStack(backStack)
                }
                val pendingNotificationDestination by notificationDestination.collectAsStateWithLifecycle()
                LaunchedEffect(pendingNotificationDestination) {
                    val target = pendingNotificationDestination ?: return@LaunchedEffect
                    navigateTo(target)
                    notificationDestination.value = null
                }
                val oauthCallback by calendarOAuthCallback.collectAsStateWithLifecycle()
                LaunchedEffect(oauthCallback) {
                    val callback = oauthCallback ?: return@LaunchedEffect
                    if (callback.scheme == "coachai" && callback.host == "calendar") {
                        navigateTo(AppDestination.Calendar)
                        calendarViewModel.completeAuthorization(
                            code = callback.getQueryParameter("code"),
                            state = callback.getQueryParameter("state"),
                            error = callback.getQueryParameter("error"),
                        )
                    }
                    calendarOAuthCallback.value = null
                }
                val density = LocalDensity.current
                val isKeyboardVisible = WindowInsets.ime.getBottom(density) > 0
                // The server is authoritative. Schedule keeps an optimistic
                // local preview, but Home never masks refreshed server data.
                val displayedHomeState = homeState
                LaunchedEffect(destination) {
                    if (destination == AppDestination.Home) homeViewModel.refresh()
                }
                val reportTasks = (
                    (displayedHomeState as? HomeUiState.Ready)?.snapshot?.planState
                        as? TodayPlanState.Available
                    )?.tasks.orEmpty()
                val remotePlanId = (displayedHomeState as? HomeUiState.Ready)?.snapshot?.planId
                LaunchedEffect(reportTasks, remotePlanId) {
                    scheduleSession.syncFromHome(
                        reportTasks,
                        remotePlanId,
                    )
                }

                BackHandler(enabled = backStack.size > 1, onBack = navigateBack)

                Surface(color = Background, contentColor = TextPrimary) {
                    Column(Modifier.fillMaxSize()) {
                        Box(Modifier.weight(1f)) {
                            when (destination) {
                                AppDestination.Home -> HomeWebView(
                                    uiState = displayedHomeState,
                                    lightTheme = lightTheme,
                                    onNavigateToSchedule = { navigateTo(AppDestination.Schedule) },
                                    onNavigateToCoach = { navigateTo(AppDestination.Coach) },
                                    onRetry = homeViewModel::refresh,
                                )
                                AppDestination.Schedule -> ScheduleScreen(
                                    onNavigateHome = { navigateTo(AppDestination.Home) },
                                    onNavigateCoach = { navigateTo(AppDestination.Coach) },
                                    onNavigateMore = { navigateTo(AppDestination.More) },
                                    onOpenFuturePlanning = { navigateTo(AppDestination.FuturePlanning) },
                                    displayName = (profileState as? ProfileUiState.Ready)?.profile?.displayName,
                                    sessionState = scheduleSession,
                                    syncViewModel = scheduleSyncViewModel,
                                )
                                AppDestination.FuturePlanning -> FuturePlanningScreen(
                                    viewModel = futurePlanningViewModel,
                                    onBack = navigateBack,
                                    onPlanChanged = {
                                        scheduleSession.hasLocalChanges = false
                                        homeViewModel.refresh()
                                    },
                                )
                                AppDestination.Coach -> CoachReportScreen(
                                    tasks = reportTasks,
                                    uiState = coachReportState,
                                    onGenerate = coachReportViewModel::generate,
                                    onReset = coachReportViewModel::reset,
                                    onNavigateHome = { navigateTo(AppDestination.Home) },
                                    onNavigateSchedule = { navigateTo(AppDestination.Schedule) },
                                )
                                AppDestination.Ask -> AskCoachScreen(
                                    messages = askCoachState.messages,
                                    isSending = askCoachState.isSending,
                                    isConnected = askCoachState.isConnected,
                                    onSendMessage = askCoachViewModel::send,
                                )
                                AppDestination.More -> MoreScreen(
                                    onOpenReport = { navigateTo(AppDestination.Coach) },
                                    onOpenProfile = { navigateTo(AppDestination.Profile) },
                                    onOpenNotifications = {
                                        if (
                                            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                                            ContextCompat.checkSelfPermission(
                                                this@MainActivity,
                                                Manifest.permission.POST_NOTIFICATIONS,
                                            ) != PackageManager.PERMISSION_GRANTED
                                        ) {
                                            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
                                        }
                                        navigateTo(AppDestination.Notifications)
                                    },
                                    onOpenCalendar = { navigateTo(AppDestination.Calendar) },
                                    onOpenSettings = { navigateTo(AppDestination.Settings) },
                                )
                                AppDestination.Profile -> ProfileScreen(
                                    uiState = profileState,
                                    onBack = navigateBack,
                                    onUpdateName = profileViewModel::updateDisplayName,
                                    onUpdateTimezone = profileViewModel::updateTimezone,
                                    onRetry = profileViewModel::refresh,
                                )
                                AppDestination.Notifications -> NotificationPreferencesScreen(
                                    uiState = notificationState,
                                    onBack = navigateBack,
                                    onDailySummary = notificationViewModel::setDailySummary,
                                    onCoachCheckins = notificationViewModel::setCoachCheckins,
                                    onReminderLead = notificationViewModel::setReminderLead,
                                    onQuietHours = notificationViewModel::setQuietHours,
                                    onSound = notificationViewModel::setSound,
                                    onVibration = notificationViewModel::setVibration,
                                    onRetry = notificationViewModel::refresh,
                                )
                                AppDestination.Calendar -> CalendarConnectionsScreen(
                                    uiState = calendarState,
                                    onBack = navigateBack,
                                    onConnect = calendarViewModel::connect,
                                    onToggleCalendar = calendarViewModel::toggleCalendar,
                                    onSaveSelection = calendarViewModel::saveSelection,
                                    onChangeCalendars = calendarViewModel::changeCalendars,
                                    onSync = calendarViewModel::syncNow,
                                    onDisconnect = calendarViewModel::disconnect,
                                    onRetry = calendarViewModel::refresh,
                                    onAuthorizationOpened = calendarViewModel::authorizationOpened,
                                )
                                AppDestination.Settings -> AppSettingsScreen(
                                    state = appSettingsState,
                                    onBack = navigateBack,
                                    onTheme = { value ->
                                        appSettingsViewModel.setTheme(value)
                                        this@MainActivity.recreate()
                                    },
                                    onLanguage = appSettingsViewModel::setLanguage,
                                    onTimeFormat = appSettingsViewModel::setTimeFormat,
                                    onHaptics = appSettingsViewModel::setHapticFeedback,
                                    onReduceMotion = appSettingsViewModel::setReduceMotion,
                                    onSignOut = {
                                        lifecycleScope.launch {
                                            runCatching { container.notificationDeviceRegistrar.unregister() }
                                            authViewModel.signOut()
                                            runCatching {
                                                CredentialManager.create(this@MainActivity)
                                                    .clearCredentialState(ClearCredentialStateRequest())
                                            }
                                        }
                                    },
                                )
                                AppDestination.Analytics -> AnalyticsWebView(
                                    repository = container.analyticsRepository,
                                    lightTheme = lightTheme,
                                    onNavigate = { requested ->
                                        navigateTo(when (requested.lowercase()) {
                                            "home" -> AppDestination.Home
                                            "schedule" -> AppDestination.Schedule
                                            "coach" -> AppDestination.Coach
                                            "ask" -> AppDestination.Ask
                                            else -> AppDestination.More
                                        })
                                    },
                                )
                            }
                        }
                        if (!isKeyboardVisible) {
                            AppBottomNavigation(
                                selected = destination,
                                onSelect = navigateTo,
                            )
                        }
                    }
                }
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        calendarOAuthCallback.value = intent.data
        passwordResetCallback.value = intent.data
        notificationDestination.value = destinationFromNotification(intent)
    }

    private fun destinationFromNotification(intent: Intent?): AppDestination? {
        if (intent?.action != "com.coachai.android.OPEN_NOTIFICATION") return null
        val type = (intent.getStringExtra("notification_type") ?: intent.getStringExtra("type"))
            .orEmpty()
            .lowercase()
        return when {
            "coach" in type -> AppDestination.Coach
            "summary" in type -> AppDestination.Home
            "task" in type || "deadline" in type || "plan" in type -> AppDestination.Schedule
            else -> AppDestination.Notifications
        }
    }
}
