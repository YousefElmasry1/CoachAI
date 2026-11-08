package com.coachai.android.ui.more

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Assessment
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.NotificationsNone
import androidx.compose.material.icons.outlined.PersonOutline
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.coachai.android.ui.theme.Accent
import com.coachai.android.ui.theme.AccentSoft
import com.coachai.android.ui.theme.Background
import com.coachai.android.ui.theme.Border
import com.coachai.android.ui.theme.Card as CardColor
import com.coachai.android.ui.theme.TextMuted
import com.coachai.android.ui.theme.TextPrimary

@Composable
fun MoreScreen(
    onOpenReport: () -> Unit,
    onOpenProfile: () -> Unit,
    onOpenNotifications: () -> Unit,
    onOpenCalendar: () -> Unit,
    onOpenSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(Background)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 26.dp),
    ) {
        Text("Your space", color = TextPrimary, fontSize = 38.sp, fontWeight = FontWeight.Bold)
        Text(
            "Your progress, account and app preferences in one place.",
            color = TextMuted,
            fontSize = 16.sp,
            lineHeight = 23.sp,
            modifier = Modifier.padding(top = 8.dp, bottom = 24.dp),
        )

        MoreEntry(
            title = "Coach Report",
            detail = "Review today's plan, strengths and recommendations.",
            icon = Icons.Outlined.Assessment,
            highlighted = true,
            onClick = onOpenReport,
        )
        MoreEntry(
            "Account & profile",
            "Manage your Zen Maestro account details.",
            Icons.Outlined.PersonOutline,
            onClick = onOpenProfile,
        )
        MoreEntry(
            "Notifications",
            "Control reminders and important updates.",
            Icons.Outlined.NotificationsNone,
            onClick = onOpenNotifications,
        )
        MoreEntry(
            "Calendar connections",
            "Plan around meetings and existing commitments.",
            Icons.Outlined.CalendarMonth,
            onClick = onOpenCalendar,
        )
        MoreEntry(
            "App settings",
            "Language, time and interaction preferences.",
            Icons.Outlined.Settings,
            onClick = onOpenSettings,
        )
        Spacer(Modifier.height(20.dp))
    }
}

@Composable
private fun MoreEntry(
    title: String,
    detail: String,
    icon: ImageVector,
    highlighted: Boolean = false,
    onClick: (() -> Unit)? = null,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 12.dp)
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier),
        shape = RoundedCornerShape(22.dp),
        colors = CardDefaults.cardColors(containerColor = if (highlighted) AccentSoft else CardColor),
        border = BorderStroke(1.dp, if (highlighted) Accent.copy(alpha = .34f) else Border),
    ) {
        Row(
            modifier = Modifier.fillMaxWidth().padding(17.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Icon(icon, title, tint = if (highlighted) Accent else TextMuted, modifier = Modifier.size(30.dp))
            Column(Modifier.weight(1f)) {
                Text(title, color = TextPrimary, fontSize = 17.sp, fontWeight = FontWeight.Bold)
                Text(detail, color = TextMuted, fontSize = 13.sp, lineHeight = 18.sp, modifier = Modifier.padding(top = 4.dp))
            }
            if (onClick != null) Icon(Icons.Outlined.ChevronRight, null, tint = Accent)
        }
    }
}
