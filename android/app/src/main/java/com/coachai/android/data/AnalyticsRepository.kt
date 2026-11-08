package com.coachai.android.data

import com.coachai.android.data.remote.AnalyticsRemoteDataSource
import com.coachai.android.data.remote.AnalyticsRemoteResult
import com.coachai.android.data.remote.AuthTokenProvider
import kotlinx.coroutines.CancellationException

enum class AnalyticsFailureReason {
    AUTHENTICATION_REQUIRED,
    NETWORK_ERROR,
    INVALID_RESPONSE,
}

class AnalyticsRepositoryException(
    val reason: AnalyticsFailureReason,
) : Exception(reason.name)

interface AnalyticsRepository {
    suspend fun loadProfile(windowDays: Int, language: String = "en"): Result<String>
}

class RealAnalyticsRepository(
    private val authTokenProvider: AuthTokenProvider,
    private val analyticsApi: AnalyticsRemoteDataSource,
) : AnalyticsRepository {
    override suspend fun loadProfile(windowDays: Int, language: String): Result<String> {
        val token = try {
            authTokenProvider.firebaseIdToken()
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            return failure(AnalyticsFailureReason.NETWORK_ERROR)
        }
        if (token.isNullOrBlank()) {
            return failure(AnalyticsFailureReason.AUTHENTICATION_REQUIRED)
        }
        return when (val result = analyticsApi.profile(token, windowDays, language)) {
            is AnalyticsRemoteResult.Success -> Result.success(result.payload)
            AnalyticsRemoteResult.Unauthorized -> failure(AnalyticsFailureReason.AUTHENTICATION_REQUIRED)
            AnalyticsRemoteResult.NetworkError -> failure(AnalyticsFailureReason.NETWORK_ERROR)
            AnalyticsRemoteResult.InvalidResponse -> failure(AnalyticsFailureReason.INVALID_RESPONSE)
        }
    }

    private fun failure(reason: AnalyticsFailureReason): Result<String> =
        Result.failure(AnalyticsRepositoryException(reason))
}
