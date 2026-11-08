package com.coachai.android.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalTime

class HomeModelsTest {
    @Test
    fun greetingBoundaries_areNaturalAndExact() {
        assertEquals("Welcome back", greetingFor(LocalTime.of(4, 59)))
        assertEquals("Good morning", greetingFor(LocalTime.of(5, 0)))
        assertEquals("Good morning", greetingFor(LocalTime.of(11, 59)))
        assertEquals("Good afternoon", greetingFor(LocalTime.of(12, 0)))
        assertEquals("Good afternoon", greetingFor(LocalTime.of(16, 59)))
        assertEquals("Good evening", greetingFor(LocalTime.of(17, 0)))
        assertEquals("Good evening", greetingFor(LocalTime.of(23, 59)))
        assertEquals("Welcome back", greetingFor(LocalTime.MIDNIGHT))
    }

    @Test
    fun missingAndEmptyPlans_doNotBecomeZeroProgress() {
        assertNull(progressFor(TodayPlanState.NoPlan))
        assertNull(progressFor(TodayPlanState.EmptyPlan))
        assertNull(progressFor(TodayPlanState.Unavailable(UnavailableReason.NETWORK_ERROR)))
    }

    @Test
    fun realTasksWithNoneCompleted_areARealZero() {
        val summary = progressFor(
            TodayPlanState.Available(
                listOf(
                    task(1, TaskStatus.PENDING),
                    task(2, TaskStatus.IN_PROGRESS),
                ),
            ),
        )!!

        assertEquals(0, summary.completionPercent)
        assertEquals(2, summary.total)
        assertEquals(1, summary.pending)
        assertEquals(1, summary.inProgress)
    }

    @Test
    fun partialProgress_usesSupportedTaskStates() {
        val summary = progressFor(
            TodayPlanState.Available(
                listOf(
                    task(1, TaskStatus.COMPLETED),
                    task(2, TaskStatus.COMPLETED),
                    task(3, TaskStatus.IN_PROGRESS),
                    task(4, TaskStatus.PENDING),
                    task(5, TaskStatus.FAILED),
                ),
            ),
        )!!

        assertEquals(40, summary.completionPercent)
        assertEquals(2, summary.completed)
        assertEquals(1, summary.inProgress)
        assertEquals(1, summary.pending)
        assertEquals(1, summary.failed)
    }

    private fun task(id: Long, status: TaskStatus) = HomeTask(
        id = id,
        title = "Task $id",
        scheduledStart = null,
        estimatedMinutes = 30,
        priority = 3,
        status = status,
        isFixedTime = false,
    )
}

