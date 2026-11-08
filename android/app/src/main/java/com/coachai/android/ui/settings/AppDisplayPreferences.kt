package com.coachai.android.ui.settings

import android.content.Context
import java.time.LocalTime
import java.time.format.DateTimeFormatter

internal const val APP_SETTINGS_PREFERENCES = "app_settings"
internal const val TIME_FORMAT_KEY = "time_format"
internal const val HAPTICS_KEY = "haptics"
internal const val REDUCE_MOTION_KEY = "reduce_motion"

fun Context.uses24HourAppTime(): Boolean =
    getSharedPreferences(APP_SETTINGS_PREFERENCES, Context.MODE_PRIVATE)
        .getString(TIME_FORMAT_KEY, "12-hour") == "24-hour"

fun Context.appHapticsEnabled(): Boolean =
    getSharedPreferences(APP_SETTINGS_PREFERENCES, Context.MODE_PRIVATE)
        .getBoolean(HAPTICS_KEY, true)

fun Context.reduceAppMotion(): Boolean =
    getSharedPreferences(APP_SETTINGS_PREFERENCES, Context.MODE_PRIVATE)
        .getBoolean(REDUCE_MOTION_KEY, false)

internal fun formatAppTime(value: String, use24Hour: Boolean): String {
    val formatter = DateTimeFormatter.ofPattern(if (use24Hour) "HH:mm" else "h:mm a")
    return runCatching { LocalTime.parse(value).format(formatter) }.getOrDefault(value)
}

