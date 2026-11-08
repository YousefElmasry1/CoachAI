package com.coachai.android.ui.notifications

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.coachai.android.data.NotificationFailureReason
import com.coachai.android.data.NotificationPreferencesRepository
import com.coachai.android.data.NotificationRepositoryException
import com.coachai.android.data.remote.NotificationPreferences
import java.util.UUID
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed interface NotificationPreferencesUiState {
    data object Loading : NotificationPreferencesUiState
    data class Error(val reason: NotificationFailureReason) : NotificationPreferencesUiState
    data class Ready(
        val preferences: NotificationPreferences,
        val saving: Boolean = false,
        val saveError: NotificationFailureReason? = null,
    ) : NotificationPreferencesUiState
}

class NotificationPreferencesViewModel(
    private val repository: NotificationPreferencesRepository,
) : ViewModel() {
    private val _uiState = MutableStateFlow<NotificationPreferencesUiState>(NotificationPreferencesUiState.Loading)
    val uiState: StateFlow<NotificationPreferencesUiState> = _uiState.asStateFlow()

    init { refresh() }

    fun refresh() {
        viewModelScope.launch {
            _uiState.value = NotificationPreferencesUiState.Loading
            repository.load()
                .onSuccess { _uiState.value = NotificationPreferencesUiState.Ready(it) }
                .onFailure {
                    _uiState.value = NotificationPreferencesUiState.Error(it.reason())
                }
        }
    }

    fun setDailySummary(enabled: Boolean) = change(
        mapOf("daily_summary_enabled" to enabled),
    ) { it.copy(dailySummaryEnabled = enabled) }

    fun setCoachCheckins(enabled: Boolean) = change(
        mapOf("coach_checkins_enabled" to enabled),
    ) { it.copy(coachCheckinsEnabled = enabled) }

    fun setSound(enabled: Boolean) = change(
        mapOf("sound_enabled" to enabled),
    ) { it.copy(soundEnabled = enabled) }

    fun setVibration(enabled: Boolean) = change(
        mapOf("vibration_enabled" to enabled),
    ) { it.copy(vibrationEnabled = enabled) }

    fun setReminderLead(minutes: Int?) = change(
        mapOf("default_reminder_lead_minutes" to minutes),
    ) { it.copy(defaultReminderLeadMinutes = minutes) }

    fun setQuietHours(start: String?, end: String?) = change(
        mapOf(
            "quiet_hours_start" to start,
            "quiet_hours_end" to end,
        ),
    ) { it.copy(quietHoursStart = start, quietHoursEnd = end) }

    private fun change(
        changes: Map<String, Any?>,
        localUpdate: (NotificationPreferences) -> NotificationPreferences,
    ) {
        val current = _uiState.value as? NotificationPreferencesUiState.Ready ?: return
        if (current.saving) return
        val optimistic = localUpdate(current.preferences)
        viewModelScope.launch {
            _uiState.value = current.copy(preferences = optimistic, saving = true, saveError = null)
            repository.update(changes, UUID.randomUUID().toString())
                .onSuccess { _uiState.value = current.copy(preferences = it, saving = false, saveError = null) }
                .onFailure {
                    _uiState.value = current.copy(saving = false, saveError = it.reason())
                }
        }
    }

    private fun Throwable.reason(): NotificationFailureReason =
        (this as? NotificationRepositoryException)?.reason
            ?: NotificationFailureReason.INVALID_RESPONSE

    class Factory(private val repository: NotificationPreferencesRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            NotificationPreferencesViewModel(repository) as T
    }
}
