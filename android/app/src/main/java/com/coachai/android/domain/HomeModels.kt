package com.coachai.android.domain

import java.time.LocalDate
import java.time.LocalTime

enum class TaskStatus {
    PENDING,
    IN_PROGRESS,
    COMPLETED,
    FAILED,
}

data class HomeTask(
    val id: Long,
    val title: String,
    val scheduledStart: String?,
    val estimatedMinutes: Int,
    val priority: Int,
    val status: TaskStatus,
    val isFixedTime: Boolean,
    val planId: Long? = null,
    val version: Int = 0,
    val isBreak: Boolean = false,
)

sealed interface TodayPlanState {
    data object Loading : TodayPlanState
    data object NoPlan : TodayPlanState
    data object EmptyPlan : TodayPlanState
    data class Available(
        val tasks: List<HomeTask>,
        val progress: ProgressSummary? = null,
    ) : TodayPlanState
    data class Unavailable(val reason: UnavailableReason) : TodayPlanState
}

enum class UnavailableReason {
    API_NOT_CONFIGURED,
    AUTHENTICATION_REQUIRED,
    NETWORK_ERROR,
    INVALID_RESPONSE,
}

sealed interface InsightState {
    data class Available(val title: String, val evidence: String) : InsightState
    data class OnboardingSuggestion(val title: String, val detail: String) : InsightState
    data object Unavailable : InsightState
}

data class ProgressSummary(
    val completed: Int,
    val inProgress: Int,
    val pending: Int,
    val failed: Int,
    val total: Int,
) {
    val completionPercent: Int
        get() = if (total == 0) 0 else ((completed * 100.0) / total).toInt()
}

fun progressFor(planState: TodayPlanState): ProgressSummary? {
    val available = planState as? TodayPlanState.Available ?: return null
    available.progress?.let { return it }
    val tasks = available.tasks
    if (tasks.isEmpty()) return null
    return ProgressSummary(
        completed = tasks.count { it.status == TaskStatus.COMPLETED },
        inProgress = tasks.count { it.status == TaskStatus.IN_PROGRESS },
        pending = tasks.count { it.status == TaskStatus.PENDING },
        failed = tasks.count { it.status == TaskStatus.FAILED },
        total = tasks.size,
    )
}

fun greetingFor(time: LocalTime): String = when (time.hour) {
    in 5..11 -> "Good morning"
    in 12..16 -> "Good afternoon"
    in 17..23 -> "Good evening"
    else -> "Welcome back"
}

data class HomeSnapshot(
    val localDate: LocalDate,
    val displayName: String?,
    val planState: TodayPlanState,
    val streakDays: Int?,
    val insightState: InsightState,
    val planId: Long? = null,
)

