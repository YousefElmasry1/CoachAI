package com.coachai.android.ui.settings

import org.junit.Assert.assertEquals
import org.junit.Test

class AppDisplayPreferencesTest {
    @Test
    fun `formats backend time in twelve hour mode`() {
        assertEquals("4:30 PM", formatAppTime("16:30", use24Hour = false))
    }

    @Test
    fun `formats backend time in twenty four hour mode`() {
        assertEquals("16:30", formatAppTime("16:30", use24Hour = true))
    }

    @Test
    fun `keeps malformed server time visible instead of crashing`() {
        assertEquals("unknown", formatAppTime("unknown", use24Hour = false))
    }
}

