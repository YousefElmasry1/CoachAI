package com.coachai.android.ui.schedule

import java.time.LocalDate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ScheduleDatePolicyTest {
    private val today = LocalDate.of(2026, 11, 8)

    @Test
    fun pastDaysCannotBeSelectedForPlanning() {
        assertFalse(scheduleDateSelectable(today.minusDays(1), today))
        assertTrue(scheduleDateSelectable(today, today))
        assertTrue(scheduleDateSelectable(today.plusDays(1), today))
    }

    @Test
    fun weekNavigationNeverMovesBeforeCurrentWeek() {
        assertEquals(0, shiftedWeekOffset(0, -1))
        assertEquals(1, shiftedWeekOffset(0, 1))
        assertEquals(12, shiftedWeekOffset(12, 1))
    }

    @Test
    fun schedulerCopyReflectsTheSelectedStartTime() {
        assertEquals("✦ Build schedule from 9:00 AM", schedulerActionLabel("09:00"))
        assertEquals("Day starts at 2:30 PM", dayStartLabel("14:30"))
    }

    @Test
    fun serverCreateReplacesTheCorrectTemporaryTask() {
        val tasks = listOf(
            ScheduleTask(id = 41, title = "Existing", durationMinutes = 20),
            ScheduleTask(id = 42, title = "New task", durationMinutes = 30),
        )
        assertEquals(1, temporaryTaskIndex(tasks, 42))
        assertEquals(-1, temporaryTaskIndex(tasks, 9001))
    }
}
