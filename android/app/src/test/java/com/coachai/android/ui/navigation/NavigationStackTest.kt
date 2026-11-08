package com.coachai.android.ui.navigation

import org.junit.Assert.assertEquals
import org.junit.Test

class NavigationStackTest {
    @Test
    fun notificationsOpensFromMoreAndBackReturnsToMore() {
        val fromMore = listOf(AppDestination.Home.name, AppDestination.More.name)

        val notifications = navigateToStack(fromMore, AppDestination.Notifications)
        val returned = navigateBackStack(notifications)

        assertEquals(AppDestination.Notifications.name, notifications.last())
        assertEquals(AppDestination.More.name, returned.last())
    }

    @Test
    fun selectingCurrentDestinationDoesNotDuplicateIt() {
        val current = listOf(AppDestination.Home.name, AppDestination.More.name)
        assertEquals(current, navigateToStack(current, AppDestination.More))
    }

    @Test
    fun calendarOpensFromMoreAndBackReturnsToMore() {
        val fromMore = listOf(AppDestination.Home.name, AppDestination.More.name)

        val calendar = navigateToStack(fromMore, AppDestination.Calendar)
        val returned = navigateBackStack(calendar)

        assertEquals(AppDestination.Calendar.name, calendar.last())
        assertEquals(AppDestination.More.name, returned.last())
    }

    @Test
    fun bottomTabsReplaceTheRootInsteadOfBuildingHistory() {
        val schedule = navigateToStack(listOf(AppDestination.Home.name), AppDestination.Schedule)
        val ask = navigateToStack(schedule, AppDestination.Ask)

        assertEquals(listOf(AppDestination.Ask.name), ask)
        assertEquals(ask, navigateBackStack(ask))
    }

    @Test
    fun futurePlanningIsAChildOfSchedule() {
        val future = navigateToStack(listOf(AppDestination.Schedule.name), AppDestination.FuturePlanning)
        assertEquals(AppDestination.Schedule.name, navigateBackStack(future).last())
    }
}
