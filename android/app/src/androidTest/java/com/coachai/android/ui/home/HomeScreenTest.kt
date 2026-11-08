package com.coachai.android.ui.home

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.junit4.v2.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performScrollTo
import com.coachai.android.data.quote.DailyQuote
import com.coachai.android.data.quote.QuoteCategory
import com.coachai.android.domain.HomeSnapshot
import com.coachai.android.domain.HomeTask
import com.coachai.android.domain.InsightState
import com.coachai.android.domain.TaskStatus
import com.coachai.android.domain.TodayPlanState
import com.coachai.android.domain.UnavailableReason
import com.coachai.android.ui.theme.CoachAiTheme
import org.junit.Rule
import org.junit.Test
import java.time.LocalDate
import java.time.LocalTime

class HomeScreenTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun unavailableDataIsNotRenderedAsZero() {
        composeRule.setContent {
            CoachAiTheme {
                HomeScreen(
                    uiState = HomeUiState.Ready(
                        snapshot = HomeSnapshot(
                            localDate = LocalDate.of(2026, 10, 15),
                            displayName = null,
                            planState = TodayPlanState.Unavailable(UnavailableReason.INVALID_RESPONSE),
                            streakDays = null,
                            insightState = InsightState.Unavailable,
                        ),
                        quote = DailyQuote("test", "A local quote.", QuoteCategory.FOCUS),
                        localTime = LocalTime.NOON,
                    ),
                    onRetry = {},
                )
            }
        }
        composeRule.waitForIdle()

        composeRule.onNodeWithText("Progress unavailable").assertIsDisplayed()
        composeRule.onAllNodesWithText("0%").assertCountEquals(0)
        composeRule.onNodeWithContentDescription("Plan today unavailable").assertIsNotEnabled()
    }

    @Test
    fun supportedDataRendersProgressScheduleAndInsight() {
        val tasks = listOf(
            task(1, "Focused planning", "09:00", TaskStatus.COMPLETED),
            task(2, "Review priorities", "10:00", TaskStatus.COMPLETED),
            task(3, "Prepare notes", "11:00", TaskStatus.COMPLETED),
            task(4, "Deep work", "13:00", TaskStatus.IN_PROGRESS),
            task(5, "Daily review", "17:00", TaskStatus.PENDING),
        )

        composeRule.setContent {
            CoachAiTheme {
                HomeScreen(
                    uiState = HomeUiState.Ready(
                        snapshot = HomeSnapshot(
                            localDate = LocalDate.of(2026, 10, 15),
                            displayName = "Maya",
                            planState = TodayPlanState.Available(tasks),
                            streakDays = 5,
                            insightState = InsightState.Available(
                                title = "Your strongest focus window starts around 10 AM.",
                                evidence = "Based on supported analytics evidence.",
                            ),
                        ),
                        quote = DailyQuote("test", "A local quote.", QuoteCategory.FOCUS),
                        localTime = LocalTime.NOON,
                    ),
                    onRetry = {},
                )
            }
        }
        composeRule.waitForIdle()

        composeRule.onNodeWithText("Good afternoon, Maya.").assertIsDisplayed()
        composeRule.onNodeWithText("60%").assertIsDisplayed()
        composeRule.onNodeWithText("3 of 5 done").assertIsDisplayed()
        composeRule.onNodeWithText("Deep work").assertIsDisplayed()
        composeRule.onNodeWithText("Your strongest focus window starts around 10 AM.").performScrollTo().assertIsDisplayed()
        composeRule.onAllNodesWithContentDescription("Schedule unavailable").assertCountEquals(2)
    }

    private fun task(id: Long, title: String, time: String, status: TaskStatus) = HomeTask(
        id = id,
        title = title,
        scheduledStart = time,
        estimatedMinutes = 30,
        priority = 2,
        status = status,
        isFixedTime = true,
    )
}
