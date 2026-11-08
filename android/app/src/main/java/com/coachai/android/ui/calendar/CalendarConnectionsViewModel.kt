package com.coachai.android.ui.calendar

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.coachai.android.data.CalendarFailureReason
import com.coachai.android.data.CalendarRepository
import com.coachai.android.data.CalendarRepositoryException
import com.coachai.android.data.remote.CalendarRemoteConnection
import com.coachai.android.data.remote.CalendarRemoteItem
import java.time.LocalDate
import java.util.UUID
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch

data class ConnectedCalendar(
    val calendarId: String,
    val name: String,
    val color: Long,
    val isPrimary: Boolean,
    val selected: Boolean,
)

data class CalendarConnection(
    val connected: Boolean = false,
    val selectedCalendars: List<ConnectedCalendar> = emptyList(),
    val lastSyncedAt: String? = null,
)

data class CalendarConnectionsUiState(
    val connection: CalendarConnection = CalendarConnection(),
    val availableCalendars: List<ConnectedCalendar> = emptyList(),
    val choosingCalendars: Boolean = false,
    val loading: Boolean = true,
    val working: Boolean = false,
    val failure: CalendarFailureReason? = null,
    val authorizationUrl: String? = null,
    val message: String? = null,
)

class CalendarConnectionsViewModel(
    private val repository: CalendarRepository,
) : ViewModel() {
    private var connectionJob: Job? = null
    private val _uiState = MutableStateFlow(CalendarConnectionsUiState())
    val uiState: StateFlow<CalendarConnectionsUiState> = _uiState.asStateFlow()

    init { refresh() }

    fun refresh() {
        connectionJob?.cancel()
        connectionJob = viewModelScope.launch {
            _uiState.value = _uiState.value.copy(loading = true, failure = null, message = null)
            repository.connection()
                .onSuccess { remote ->
                    val connection = remote.toUi()
                    _uiState.value = _uiState.value.copy(
                        connection = connection,
                        loading = false,
                        choosingCalendars = false,
                        authorizationUrl = null,
                    )
                    if (connection.connected && connection.selectedCalendars.isEmpty()) loadCalendars()
                }
                .onFailure { _uiState.value = _uiState.value.copy(loading = false, failure = it.reason()) }
        }
    }

    fun connect() {
        if (_uiState.value.working) return
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(working = true, failure = null, message = null)
            repository.beginAuthorization()
                .onSuccess { url ->
                    _uiState.value = _uiState.value.copy(working = false, authorizationUrl = url)
                }
                .onFailure { _uiState.value = _uiState.value.copy(working = false, failure = it.reason()) }
        }
    }

    fun authorizationOpened() {
        _uiState.value = _uiState.value.copy(authorizationUrl = null)
    }

    fun completeAuthorization(code: String?, state: String?, error: String?) {
        if (error != null || code.isNullOrBlank() || state.isNullOrBlank()) {
            _uiState.value = _uiState.value.copy(
                working = false,
                loading = false,
                failure = CalendarFailureReason.INVALID_RESPONSE,
                authorizationUrl = null,
            )
            return
        }
        connectionJob?.cancel()
        connectionJob = viewModelScope.launch {
            _uiState.value = _uiState.value.copy(working = true, loading = false, failure = null)
            repository.completeAuthorization(code, state)
                .onSuccess { remote ->
                    val connection = remote.toUi()
                    _uiState.value = _uiState.value.copy(
                        connection = connection,
                        working = false,
                        choosingCalendars = false,
                        message = "Google Calendar connected",
                    )
                    if (connection.selectedCalendars.isEmpty()) loadCalendars()
                }
                .onFailure {
                    _uiState.value = _uiState.value.copy(working = false, failure = it.reason())
                }
        }
    }

    fun changeCalendars() = loadCalendars()

    private fun loadCalendars() {
        if (_uiState.value.working) return
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(working = true, failure = null, message = null)
            repository.calendars()
                .onSuccess { calendars ->
                    _uiState.value = _uiState.value.copy(
                        availableCalendars = calendars.map(CalendarRemoteItem::toUi),
                        choosingCalendars = true,
                        working = false,
                    )
                }
                .onFailure { _uiState.value = _uiState.value.copy(working = false, failure = it.reason()) }
        }
    }

    fun toggleCalendar(calendarId: String) {
        if (_uiState.value.working) return
        _uiState.value = _uiState.value.copy(
            availableCalendars = _uiState.value.availableCalendars.map {
                if (it.calendarId == calendarId) it.copy(selected = !it.selected) else it
            },
            message = null,
        )
    }

    fun saveSelection() {
        val selected = _uiState.value.availableCalendars.filter { it.selected }
        if (selected.isEmpty()) {
            _uiState.value = _uiState.value.copy(message = "Choose at least one calendar")
            return
        }
        mutate {
            repository.select(selected.map(ConnectedCalendar::toRemote), UUID.randomUUID().toString())
                .map { remote -> remote.toUi() }
        }
    }

    fun syncNow() {
        if (_uiState.value.working) return
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(working = true, failure = null, message = null)
            val today = LocalDate.now()
            repository.sync(today, today.plusDays(29), UUID.randomUUID().toString())
                .onSuccess {
                    _uiState.value = _uiState.value.copy(
                        working = false,
                        connection = _uiState.value.connection.copy(lastSyncedAt = "queued now"),
                        message = "Calendar sync queued",
                    )
                }
                .onFailure { _uiState.value = _uiState.value.copy(working = false, failure = it.reason()) }
        }
    }

    fun disconnect() = mutate {
        repository.disconnect(UUID.randomUUID().toString()).map { it.toUi() }
    }

    private fun mutate(action: suspend () -> Result<CalendarConnection>) {
        if (_uiState.value.working) return
        viewModelScope.launch {
            _uiState.value = _uiState.value.copy(working = true, failure = null, message = null)
            action()
                .onSuccess { connection ->
                    _uiState.value = CalendarConnectionsUiState(
                        connection = connection,
                        loading = false,
                        message = if (connection.connected) "Calendars selected" else "Google Calendar disconnected",
                    )
                }
                .onFailure { _uiState.value = _uiState.value.copy(working = false, failure = it.reason()) }
        }
    }

    private fun Throwable.reason() =
        (this as? CalendarRepositoryException)?.reason ?: CalendarFailureReason.INVALID_RESPONSE

    class Factory(private val repository: CalendarRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            CalendarConnectionsViewModel(repository) as T
    }
}

private fun CalendarRemoteConnection.toUi() = CalendarConnection(
    connected = connected,
    selectedCalendars = selectedCalendars.map(CalendarRemoteItem::toUi),
)

private fun CalendarRemoteItem.toUi() = ConnectedCalendar(
    calendarId = calendarId,
    name = name,
    color = runCatching { color.removePrefix("#").toLong(16) or 0xFF000000 }.getOrDefault(0xFF4285F4),
    isPrimary = isPrimary,
    selected = selected,
)

private fun ConnectedCalendar.toRemote() = CalendarRemoteItem(
    calendarId = calendarId,
    name = name,
    color = "#%06X".format(color and 0xFFFFFF),
    isPrimary = isPrimary,
    selected = selected,
)
