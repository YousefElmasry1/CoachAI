package com.coachai.android.ui.profile

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.coachai.android.data.ProfileFailureReason
import com.coachai.android.data.ProfileRepository
import com.coachai.android.data.ProfileRepositoryException
import com.coachai.android.data.remote.ProfileData
import java.util.UUID
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed interface ProfileUiState {
    data object Loading : ProfileUiState
    data class Error(
        val reason: ProfileFailureReason,
        val message: String,
    ) : ProfileUiState
    data class Ready(
        val profile: ProfileData,
        val saving: Boolean = false,
        val message: String? = null,
    ) : ProfileUiState
}

class ProfileViewModel(private val repository: ProfileRepository) : ViewModel() {
    private val _uiState = MutableStateFlow<ProfileUiState>(ProfileUiState.Loading)
    val uiState: StateFlow<ProfileUiState> = _uiState.asStateFlow()
    private var refreshJob: Job? = null

    init { refresh() }

    fun refresh() {
        refreshJob?.cancel()
        _uiState.value = ProfileUiState.Loading
        refreshJob = viewModelScope.launch {
            repository.load()
                .onSuccess { _uiState.value = ProfileUiState.Ready(it) }
                .onFailure {
                    val reason = (it as? ProfileRepositoryException)?.reason
                        ?: ProfileFailureReason.NETWORK_ERROR
                    _uiState.value = ProfileUiState.Error(reason, reason.userMessage())
                }
        }
    }

    fun updateDisplayName(displayName: String) = mutate { current ->
        repository.updateDisplayName(displayName, UUID.randomUUID().toString())
            .map { current.copy(profile = it, saving = false, message = "Name updated") }
    }

    fun updateTimezone(timezone: String) = mutate { current ->
        repository.updateTimezone(timezone, UUID.randomUUID().toString())
            .map { confirmedTimezone ->
                current.copy(
                    profile = current.profile.copy(timezone = confirmedTimezone),
                    saving = false,
                    message = "Timezone updated",
                )
            }
    }

    private fun mutate(block: suspend (ProfileUiState.Ready) -> Result<ProfileUiState.Ready>) {
        val current = _uiState.value as? ProfileUiState.Ready ?: return
        if (current.saving) return
        viewModelScope.launch {
            _uiState.value = current.copy(saving = true, message = null)
            _uiState.value = block(current).getOrElse {
                current.copy(saving = false, message = "Could not save changes")
            }
        }
    }

    private fun ProfileFailureReason.userMessage(): String = when (this) {
        ProfileFailureReason.AUTHENTICATION_REQUIRED ->
            "Sign in is required to load your private profile."
        ProfileFailureReason.NETWORK_ERROR ->
            "Zen Maestro could not reach the service. Check your connection and try again."
        ProfileFailureReason.INVALID_RESPONSE ->
            "The service returned profile data that could not be safely displayed."
    }

    class Factory(private val repository: ProfileRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T =
            ProfileViewModel(repository) as T
    }
}
