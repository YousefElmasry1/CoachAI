package com.coachai.android.notifications

import com.coachai.android.data.NotificationPresentationSettings
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Test

class NotificationChannelPolicyTest {
    @Test
    fun `each sound and vibration combination has a stable distinct channel`() {
        val soundAndVibration = notificationChannelPolicy(NotificationPresentationSettings(true, true))
        val soundOnly = notificationChannelPolicy(NotificationPresentationSettings(true, false))
        val vibrationOnly = notificationChannelPolicy(NotificationPresentationSettings(false, true))
        val silent = notificationChannelPolicy(NotificationPresentationSettings(false, false))

        assertEquals("coachai_alerts_sound_vibration", soundAndVibration.id)
        assertEquals("coachai_alerts_sound", soundOnly.id)
        assertEquals("coachai_alerts_vibration", vibrationOnly.id)
        assertEquals("coachai_alerts_silent", silent.id)
        assertNotEquals(soundAndVibration.id, silent.id)
    }
}

