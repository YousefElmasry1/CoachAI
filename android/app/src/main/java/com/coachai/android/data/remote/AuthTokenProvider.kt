package com.coachai.android.data.remote

fun interface AuthTokenProvider {
    suspend fun firebaseIdToken(): String?
}

object UnconfiguredAuthTokenProvider : AuthTokenProvider {
    override suspend fun firebaseIdToken(): String? = null
}
