package com.coachai.android.notifications

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.graphics.Color
import android.media.AudioAttributes
import android.provider.Settings
import com.coachai.android.CoachAiApplication
import com.coachai.android.MainActivity
import com.coachai.android.R
import com.coachai.android.data.NotificationPresentationSettings
import com.coachai.android.data.SharedPreferencesNotificationPresentationStore
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class CoachFirebaseMessagingService : FirebaseMessagingService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onCreate() {
        super.onCreate()
        createChannel(presentationSettings())
    }

    override fun onNewToken(token: String) {
        super.onNewToken(token)
        val registrar = (application as? CoachAiApplication)?.container?.notificationDeviceRegistrar ?: return
        scope.launch { registrar.registerPushToken(token) }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        super.onMessageReceived(message)
        val presentation = presentationSettings()
        val channelId = createChannel(presentation)
        val title = message.notification?.title ?: message.data["title"] ?: "Zen Maestro"
        val body = message.notification?.body ?: message.data["body"] ?: "You have a new update."
        val notificationId = notificationIds.incrementAndGet()
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            action = ACTION_OPEN_NOTIFICATION
            message.data.forEach { (key, value) -> putExtra(key, value) }
            message.data["type"]?.let { putExtra("notification_type", it) }
        }
        val pendingIntent = PendingIntent.getActivity(
            this,
            notificationId,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = Notification.Builder(this, channelId)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(Color.rgb(255, 122, 69))
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(pendingIntent)
            .build()
        getSystemService(NotificationManager::class.java)
            .notify(notificationId, notification)
    }

    private fun presentationSettings(): NotificationPresentationSettings =
        SharedPreferencesNotificationPresentationStore(this).read()

    private fun createChannel(settings: NotificationPresentationSettings): String {
        val policy = notificationChannelPolicy(settings)
        val channel = NotificationChannel(
            policy.id,
            policy.name,
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = "Task reminders, daily summaries, and coach check-ins"
            enableVibration(settings.vibrationEnabled)
            vibrationPattern = if (settings.vibrationEnabled) longArrayOf(0, 180, 90, 180) else null
            if (settings.soundEnabled) {
                val attributes = AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build()
                setSound(Settings.System.DEFAULT_NOTIFICATION_URI, attributes)
            } else {
                setSound(null, null)
            }
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        return policy.id
    }

    private companion object {
        const val ACTION_OPEN_NOTIFICATION = "com.coachai.android.OPEN_NOTIFICATION"
        val notificationIds = AtomicInteger(1000)
    }
}

internal data class NotificationChannelPolicy(val id: String, val name: String)

internal fun notificationChannelPolicy(settings: NotificationPresentationSettings): NotificationChannelPolicy =
    when {
        settings.soundEnabled && settings.vibrationEnabled ->
            NotificationChannelPolicy("coachai_alerts_sound_vibration", "Plans and coaching")
        settings.soundEnabled ->
            NotificationChannelPolicy("coachai_alerts_sound", "Plans and coaching · sound")
        settings.vibrationEnabled ->
            NotificationChannelPolicy("coachai_alerts_vibration", "Plans and coaching · vibration")
        else -> NotificationChannelPolicy("coachai_alerts_silent", "Plans and coaching · silent")
    }
