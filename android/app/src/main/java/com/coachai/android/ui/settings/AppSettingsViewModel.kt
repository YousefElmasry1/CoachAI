package com.coachai.android.ui.settings

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

data class AppSettingsUiState(
    val theme: String = "Light",
    val language: String = "English",
    val timeFormat: String = "12-hour",
    val hapticFeedback: Boolean = true,
    val reduceMotion: Boolean = false,
)

class AppSettingsViewModel(application: Application) : AndroidViewModel(application) {
    private val preferences = application.getSharedPreferences(APP_SETTINGS_PREFERENCES, 0)
    private val _uiState = MutableStateFlow(
        AppSettingsUiState(
            theme = preferences.getString("theme", "Light") ?: "Light",
            language = preferences.getString("language", "English") ?: "English",
            timeFormat = preferences.getString(TIME_FORMAT_KEY, "12-hour") ?: "12-hour",
            hapticFeedback = preferences.getBoolean(HAPTICS_KEY, true),
            reduceMotion = preferences.getBoolean(REDUCE_MOTION_KEY, false),
        ),
    )
    val uiState: StateFlow<AppSettingsUiState> = _uiState.asStateFlow()

    fun setTheme(value: String) {
        _uiState.value = _uiState.value.copy(theme = value)
        preferences.edit().putString("theme", value).commit()
    }

    fun setLanguage(value: String) = update(_uiState.value.copy(language = value)) {
        putString("language", value)
    }

    fun setTimeFormat(value: String) = update(_uiState.value.copy(timeFormat = value)) {
        putString(TIME_FORMAT_KEY, value)
    }

    fun setHapticFeedback(value: Boolean) = update(_uiState.value.copy(hapticFeedback = value)) {
        putBoolean(HAPTICS_KEY, value)
    }

    fun setReduceMotion(value: Boolean) = update(_uiState.value.copy(reduceMotion = value)) {
        putBoolean(REDUCE_MOTION_KEY, value)
    }

    private fun update(
        state: AppSettingsUiState,
        write: android.content.SharedPreferences.Editor.() -> Unit,
    ) {
        _uiState.value = state
        preferences.edit().apply(write).apply()
    }
}
