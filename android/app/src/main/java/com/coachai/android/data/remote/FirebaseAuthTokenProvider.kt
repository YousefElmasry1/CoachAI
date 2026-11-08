package com.coachai.android.data.remote

import com.google.firebase.auth.FirebaseAuth
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Supplies the backend with the current Firebase user's ID token.
 * Firebase refreshes an expired token when forceRefresh is false.
 */
class FirebaseAuthTokenProvider(
    private val firebaseAuth: FirebaseAuth = FirebaseAuth.getInstance(),
) : AuthTokenProvider {
    private val refreshMutex = Mutex()
    private var refreshedUid: String? = null

    override suspend fun firebaseIdToken(): String? = refreshMutex.withLock {
        val currentUser = firebaseAuth.currentUser ?: run {
            refreshedUid = null
            return@withLock null
        }
        val forceRefresh = refreshedUid != currentUser.uid

        val token = suspendCancellableCoroutine { continuation ->
            currentUser.getIdToken(forceRefresh).addOnCompleteListener { task ->
                if (!continuation.isActive) return@addOnCompleteListener

                val error = task.exception
                when {
                    task.isSuccessful -> continuation.resume(task.result?.token)
                    error != null -> continuation.resumeWithException(error)
                    else -> continuation.resumeWithException(
                        IllegalStateException("Firebase did not return an ID token"),
                    )
                }
            }
        }
        if (!token.isNullOrBlank()) refreshedUid = currentUser.uid
        token
    }
}
