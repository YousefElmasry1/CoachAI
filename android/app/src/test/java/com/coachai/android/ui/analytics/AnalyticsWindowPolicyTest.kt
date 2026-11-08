package com.coachai.android.ui.analytics

import org.junit.Assert.assertEquals
import org.junit.Test

class AnalyticsWindowPolicyTest {
    @Test
    fun supportedDashboardWindowsRemainDistinct() {
        assertEquals(7, normalizeAnalyticsWindow(7))
        assertEquals(30, normalizeAnalyticsWindow(30))
        assertEquals(90, normalizeAnalyticsWindow(90))
    }

    @Test
    fun unexpectedBridgeValuesFallBackSafely() {
        assertEquals(30, normalizeAnalyticsWindow(0))
        assertEquals(30, normalizeAnalyticsWindow(365))
    }
}
