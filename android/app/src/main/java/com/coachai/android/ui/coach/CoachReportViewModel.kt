package com.coachai.android.ui.coach

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.coachai.android.data.CoachFailureReason
import com.coachai.android.data.CoachRepository
import com.coachai.android.data.CoachRepositoryException
import com.coachai.android.data.remote.CoachRecommendation
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

sealed interface CoachReportUiState {
    data object Idle : CoachReportUiState
    data object Loading : CoachReportUiState
    data class Ready(val report: CoachRecommendation) : CoachReportUiState
    data class Error(val reason: CoachFailureReason) : CoachReportUiState
}

class CoachReportViewModel(private val repository: CoachRepository) : ViewModel() {
    private val _uiState = MutableStateFlow<CoachReportUiState>(CoachReportUiState.Idle)
    val uiState: StateFlow<CoachReportUiState> = _uiState.asStateFlow()

    fun generate() {
        if (_uiState.value == CoachReportUiState.Loading) return
        viewModelScope.launch {
            _uiState.value = CoachReportUiState.Loading
            repository.recommendations()
                .onSuccess { _uiState.value = CoachReportUiState.Ready(it) }
                .onFailure {
                    _uiState.value = CoachReportUiState.Error(
                        (it as? CoachRepositoryException)?.reason ?: CoachFailureReason.INVALID_RESPONSE,
                    )
                }
        }
    }

    fun reset() { _uiState.value = CoachReportUiState.Idle }

    class Factory(private val repository: CoachRepository) : ViewModelProvider.Factory {
        @Suppress("UNCHECKED_CAST")
        override fun <T : ViewModel> create(modelClass: Class<T>): T = CoachReportViewModel(repository) as T
    }
}
