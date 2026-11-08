package com.coachai.android.ui.navigation

import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Assessment
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.ChatBubbleOutline
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.MoreHoriz
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.coachai.android.ui.theme.Accent
import com.coachai.android.ui.theme.AccentSoft
import com.coachai.android.ui.theme.AccentText
import com.coachai.android.ui.theme.NavigationSurface
import com.coachai.android.ui.theme.TextMuted

enum class AppDestination { Home, Schedule, FuturePlanning, Coach, Ask, More, Analytics, Profile, Notifications, Calendar, Settings }

private data class NavigationEntry(
    val destination: AppDestination,
    val label: String,
    val icon: ImageVector,
)

private val navigationEntries = listOf(
    NavigationEntry(AppDestination.Home, "Home", Icons.Outlined.Home),
    NavigationEntry(AppDestination.Schedule, "Schedule", Icons.Outlined.CalendarMonth),
    NavigationEntry(AppDestination.Ask, "Ask", Icons.Outlined.ChatBubbleOutline),
    NavigationEntry(AppDestination.Analytics, "Analytics", Icons.Outlined.Assessment),
    NavigationEntry(AppDestination.More, "More", Icons.Outlined.MoreHoriz),
)

@Composable
fun AppBottomNavigation(
    selected: AppDestination,
    onSelect: (AppDestination) -> Unit,
) {
    NavigationBar(
        modifier = Modifier.heightIn(min = 88.dp),
        containerColor = NavigationSurface,
    ) {
        navigationEntries.forEach { entry ->
            val active = selected == entry.destination ||
                (entry.destination == AppDestination.Schedule && selected == AppDestination.FuturePlanning) ||
                (entry.destination == AppDestination.More &&
                    (selected == AppDestination.Coach || selected == AppDestination.Profile ||
                        selected == AppDestination.Notifications || selected == AppDestination.Calendar ||
                        selected == AppDestination.Settings))
            NavigationBarItem(
                selected = active,
                onClick = { onSelect(entry.destination) },
                icon = {
                    Icon(
                        imageVector = entry.icon,
                        contentDescription = entry.label,
                        modifier = Modifier.size(28.dp),
                    )
                },
                label = {
                    Text(
                        text = entry.label,
                        fontSize = 12.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = Accent,
                    selectedTextColor = AccentText,
                    indicatorColor = AccentSoft,
                    unselectedIconColor = TextMuted,
                    unselectedTextColor = TextMuted,
                ),
            )
        }
    }
}
