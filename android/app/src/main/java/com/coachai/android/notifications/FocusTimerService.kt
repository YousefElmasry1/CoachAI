package com.coachai.android.notifications

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.coachai.android.MainActivity
import com.coachai.android.R
import java.util.Locale
import kotlin.math.max

/** Keeps a user-started focus countdown visible while Zen Maestro is backgrounded. */
class FocusTimerService : Service() {
    private val handler = Handler(Looper.getMainLooper())
    private var taskTitle = "Focus session"
    private var remainingAtUpdate = 0
    private var updatedAtElapsed = 0L
    private var paused = false

    private val tick = object : Runnable {
        override fun run() {
            getSystemService(NotificationManager::class.java)
                .notify(NOTIFICATION_ID, notification(currentRemaining()))
            handler.postDelayed(this, UPDATE_INTERVAL_MS)
        }
    }

    override fun onCreate() {
        super.onCreate()
        createChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            handler.removeCallbacks(tick)
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }

        taskTitle = intent?.getStringExtra(EXTRA_TITLE)?.trim().orEmpty().ifBlank { "Focus session" }
        remainingAtUpdate = max(0, intent?.getIntExtra(EXTRA_REMAINING_SECONDS, 0) ?: 0)
        paused = intent?.getBooleanExtra(EXTRA_PAUSED, false) ?: false
        updatedAtElapsed = SystemClock.elapsedRealtime()
        startForeground(NOTIFICATION_ID, notification(currentRemaining()))
        handler.removeCallbacks(tick)
        handler.postDelayed(tick, UPDATE_INTERVAL_MS)
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        handler.removeCallbacks(tick)
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun currentRemaining(): Int {
        if (paused) return remainingAtUpdate
        val elapsed = ((SystemClock.elapsedRealtime() - updatedAtElapsed) / 1_000L).toInt()
        return max(0, remainingAtUpdate - elapsed)
    }

    private fun notification(seconds: Int): Notification {
        val openApp = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val pendingIntent = PendingIntent.getActivity(
            this,
            2401,
            openApp,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(taskTitle)
            .setContentText(if (paused) "Paused · ${formatRemaining(seconds)} left" else "Focus time · ${formatRemaining(seconds)} left")
            .setCategory(Notification.CATEGORY_STOPWATCH)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setContentIntent(pendingIntent)
            .build()
    }

    private fun createChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Focus timer",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Ongoing task countdowns"
            setSound(null, null)
            enableVibration(false)
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        private const val CHANNEL_ID = "zen_focus_timer"
        private const val NOTIFICATION_ID = 2401
        private const val UPDATE_INTERVAL_MS = 15_000L
        private const val ACTION_START_OR_UPDATE = "com.zenmaestro.app.FOCUS_TIMER_UPDATE"
        private const val ACTION_STOP = "com.zenmaestro.app.FOCUS_TIMER_STOP"
        private const val EXTRA_TITLE = "title"
        private const val EXTRA_REMAINING_SECONDS = "remaining_seconds"
        private const val EXTRA_PAUSED = "paused"

        fun show(context: Context, title: String, remainingSeconds: Int, paused: Boolean): Boolean {
            val visible = NotificationManagerCompat.from(context).areNotificationsEnabled()
            val intent = Intent(context, FocusTimerService::class.java).apply {
                action = ACTION_START_OR_UPDATE
                putExtra(EXTRA_TITLE, title)
                putExtra(EXTRA_REMAINING_SECONDS, remainingSeconds)
                putExtra(EXTRA_PAUSED, paused)
            }
            ContextCompat.startForegroundService(context, intent)
            return visible
        }

        fun stop(context: Context) {
            context.startService(Intent(context, FocusTimerService::class.java).setAction(ACTION_STOP))
        }

        internal fun formatRemaining(seconds: Int): String {
            val safe = max(0, seconds)
            val hours = safe / 3600
            val minutes = (safe % 3600) / 60
            val secs = safe % 60
            return if (hours > 0) String.format(Locale.US, "%d:%02d:%02d", hours, minutes, secs)
            else String.format(Locale.US, "%02d:%02d", minutes, secs)
        }
    }
}
