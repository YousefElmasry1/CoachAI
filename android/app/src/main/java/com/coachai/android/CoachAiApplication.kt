package com.coachai.android

import android.app.Application
import com.coachai.android.data.AnalyticsRepository
import com.coachai.android.data.CalendarRepository
import com.coachai.android.data.CoachRepository
import com.coachai.android.data.HomeRepository
import com.coachai.android.data.RealAnalyticsRepository
import com.coachai.android.data.RealCalendarRepository
import com.coachai.android.data.RealCoachRepository
import com.coachai.android.data.TaskRepository
import com.coachai.android.data.FuturePlanningRepository
import com.coachai.android.data.RealHomeRepository
import com.coachai.android.data.ProfileRepository
import com.coachai.android.data.RealProfileRepository
import com.coachai.android.data.NotificationPreferencesRepository
import com.coachai.android.data.NotificationDeviceRegistrar
import com.coachai.android.data.RealNotificationPreferencesRepository
import com.coachai.android.data.SharedPreferencesNotificationPresentationStore
import com.coachai.android.data.quote.DailyQuoteSelector
import com.coachai.android.data.quote.QuoteLibrary
import com.coachai.android.data.quote.SharedPreferencesQuoteHistoryStore
import com.coachai.android.data.remote.AnalyticsApi
import com.coachai.android.data.remote.CalendarApi
import com.coachai.android.data.remote.CoachApi
import com.coachai.android.data.remote.TaskApi
import com.coachai.android.data.remote.FuturePlanningApi
import com.coachai.android.data.remote.FirebaseAuthTokenProvider
import com.coachai.android.data.remote.HomeApi
import com.coachai.android.data.remote.ProfileApi
import com.coachai.android.data.remote.NotificationPreferencesApi
import com.coachai.android.data.remote.NotificationDeviceApi

class CoachAiApplication : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        val apiBaseUrl = BuildConfig.COACHAI_API_BASE_URL
        val authTokenProvider = FirebaseAuthTokenProvider()
        container = AppContainer(
            quoteSelector = DailyQuoteSelector(
                quotes = QuoteLibrary.all,
                historyStore = SharedPreferencesQuoteHistoryStore(this),
            ),
            homeRepository = RealHomeRepository(
                apiBaseUrl = apiBaseUrl,
                authTokenProvider = authTokenProvider,
                homeApi = HomeApi(apiBaseUrl),
            ),
            analyticsRepository = RealAnalyticsRepository(
                authTokenProvider = authTokenProvider,
                analyticsApi = AnalyticsApi(apiBaseUrl),
            ),
            profileRepository = RealProfileRepository(
                authTokenProvider = authTokenProvider,
                profileApi = ProfileApi(apiBaseUrl),
            ),
            notificationPreferencesRepository = RealNotificationPreferencesRepository(
                authTokenProvider = authTokenProvider,
                api = NotificationPreferencesApi(apiBaseUrl),
                presentationStore = SharedPreferencesNotificationPresentationStore(this),
            ),
            notificationDeviceRegistrar = NotificationDeviceRegistrar(
                context = this,
                tokens = authTokenProvider,
                api = NotificationDeviceApi(apiBaseUrl),
            ),
            calendarRepository = RealCalendarRepository(
                authTokenProvider = authTokenProvider,
                api = CalendarApi(apiBaseUrl),
            ),
            coachRepository = RealCoachRepository(
                tokens = authTokenProvider,
                api = CoachApi(apiBaseUrl),
            ),
            taskRepository = TaskRepository(authTokenProvider, TaskApi(apiBaseUrl)),
            futurePlanningRepository = FuturePlanningRepository(
                authTokenProvider, FuturePlanningApi(apiBaseUrl),
            ),
        )
    }
}

data class AppContainer(
    val quoteSelector: DailyQuoteSelector,
    val homeRepository: HomeRepository,
    val analyticsRepository: AnalyticsRepository,
    val profileRepository: ProfileRepository,
    val notificationPreferencesRepository: NotificationPreferencesRepository,
    val notificationDeviceRegistrar: NotificationDeviceRegistrar,
    val calendarRepository: CalendarRepository,
    val coachRepository: CoachRepository,
    val taskRepository: TaskRepository,
    val futurePlanningRepository: FuturePlanningRepository,
)
