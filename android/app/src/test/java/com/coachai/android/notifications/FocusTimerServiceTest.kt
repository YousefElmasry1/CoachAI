package com.coachai.android.notifications

import org.junit.Assert.assertEquals
import org.junit.Test

class FocusTimerServiceTest {
    @Test
    fun remainingTimeIsFormattedForShortAndLongSessions() {
        assertEquals("00:00", FocusTimerService.formatRemaining(0))
        assertEquals("29:59", FocusTimerService.formatRemaining(1_799))
        assertEquals("1:01:01", FocusTimerService.formatRemaining(3_661))
    }
}
