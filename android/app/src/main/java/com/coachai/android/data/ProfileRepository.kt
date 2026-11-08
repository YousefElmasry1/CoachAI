package com.coachai.android.data

import com.coachai.android.data.remote.AuthTokenProvider
import com.coachai.android.data.remote.ProfileData
import com.coachai.android.data.remote.ProfileRemoteDataSource
import com.coachai.android.data.remote.ProfileRemoteResult
import kotlinx.coroutines.CancellationException

enum class ProfileFailureReason {
    AUTHENTICATION_REQUIRED,
    NETWORK_ERROR,
    INVALID_RESPONSE,
}

class ProfileRepositoryException(
    val reason: ProfileFailureReason,
) : Exception(reason.name)

interface ProfileRepository {
    suspend fun load(): Result<ProfileData>
    suspend fun updateDisplayName(displayName: String, idempotencyKey: String): Result<ProfileData>
    suspend fun updateTimezone(timezone: String, idempotencyKey: String): Result<String>
}

class RealProfileRepository(
    private val authTokenProvider: AuthTokenProvider,
    private val profileApi: ProfileRemoteDataSource,
) : ProfileRepository {
    override suspend fun load(): Result<ProfileData> = withToken(profileApi::getProfile)

    override suspend fun updateDisplayName(
        displayName: String,
        idempotencyKey: String,
    ): Result<ProfileData> = withToken { token ->
        profileApi.updateDisplayName(token, displayName, idempotencyKey)
    }

    override suspend fun updateTimezone(
        timezone: String,
        idempotencyKey: String,
    ): Result<String> = withToken { token ->
        profileApi.updateTimezone(token, timezone, idempotencyKey)
    }

    private suspend fun <T> withToken(
        block: suspend (String) -> ProfileRemoteResult<T>,
    ): Result<T> {
        val token = try {
            authTokenProvider.firebaseIdToken()
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            return failure(ProfileFailureReason.NETWORK_ERROR)
        }
        if (token.isNullOrBlank()) {
            return failure(ProfileFailureReason.AUTHENTICATION_REQUIRED)
        }
        return when (val result = block(token)) {
            is ProfileRemoteResult.Success -> Result.success(result.value)
            ProfileRemoteResult.Unauthorized -> failure(ProfileFailureReason.AUTHENTICATION_REQUIRED)
            ProfileRemoteResult.NetworkError -> failure(ProfileFailureReason.NETWORK_ERROR)
            ProfileRemoteResult.InvalidResponse -> failure(ProfileFailureReason.INVALID_RESPONSE)
        }
    }

    private fun <T> failure(reason: ProfileFailureReason): Result<T> =
        Result.failure(ProfileRepositoryException(reason))
}
