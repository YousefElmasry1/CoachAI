package com.coachai.android.ui.home

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.AddTask
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.outlined.Insights
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.coachai.android.data.quote.DailyQuote
import com.coachai.android.domain.HomeSnapshot
import com.coachai.android.domain.HomeTask
import com.coachai.android.domain.InsightState
import com.coachai.android.domain.ProgressSummary
import com.coachai.android.domain.TaskStatus
import com.coachai.android.domain.TodayPlanState
import com.coachai.android.domain.UnavailableReason
import com.coachai.android.domain.greetingFor
import com.coachai.android.domain.progressFor
import com.coachai.android.ui.theme.Accent
import com.coachai.android.ui.theme.AccentLine
import com.coachai.android.ui.theme.AccentSoft
import com.coachai.android.ui.theme.Background
import com.coachai.android.ui.theme.Border
import com.coachai.android.ui.theme.BorderSoft
import com.coachai.android.ui.theme.Card
import com.coachai.android.ui.theme.HeroEnd
import com.coachai.android.ui.theme.HeroStart
import com.coachai.android.ui.theme.NavigationSurface
import com.coachai.android.ui.theme.Raised
import com.coachai.android.ui.theme.Success
import com.coachai.android.ui.theme.TextMuted
import com.coachai.android.ui.theme.TextPrimary
import com.coachai.android.ui.theme.TextSubtle
import java.time.LocalTime
import java.time.format.DateTimeFormatter

@Composable
fun HomeScreen(
    uiState: HomeUiState,
    onRetry: () -> Unit,
    onNavigateToSchedule: () -> Unit = {},
) {
    val ready = uiState as? HomeUiState.Ready
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.radialGradient(
                    colors = listOf(Accent.copy(alpha = .10f), Background, Background),
                    center = Offset(1150f, -180f),
                    radius = 900f,
                ),
            ),
    ) {
        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = Color.Transparent,
            contentWindowInsets = WindowInsets(0, 0, 0, 0),
            topBar = { CoachTopBar(displayName = ready?.snapshot?.displayName) },
            bottomBar = { CoachBottomBar(onNavigateToSchedule) },
        ) { innerPadding ->
            when (uiState) {
                HomeUiState.Loading -> LoadingHome(Modifier.padding(innerPadding))
                is HomeUiState.Ready -> ReadyHome(
                    state = uiState,
                    onRetry = onRetry,
                    modifier = Modifier.padding(innerPadding),
                )
            }
        }
    }
}

@Composable
private fun CoachTopBar(displayName: String?) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(Background.copy(alpha = .97f))
            .statusBarsPadding()
            .height(56.dp)
            .padding(horizontal = 18.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(
            modifier = Modifier
                .size(32.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(Accent),
            contentAlignment = Alignment.Center,
        ) {
            Text("C", color = Color(0xFF1C110C), fontWeight = FontWeight.ExtraBold, fontSize = 18.sp)
        }
        Spacer(Modifier.width(10.dp))
        Text("Zen Maestro", style = MaterialTheme.typography.titleLarge)
        Box(
            Modifier
                .padding(start = 3.dp, top = 9.dp)
                .size(6.dp)
                .clip(RoundedCornerShape(2.dp))
                .background(Accent),
        )
        Spacer(Modifier.weight(1f))
        Box(
            modifier = Modifier
                .size(34.dp)
                .clip(CircleShape)
                .background(AccentSoft)
                .border(1.dp, AccentLine, CircleShape)
                .semantics { contentDescription = "Profile unavailable" },
            contentAlignment = Alignment.Center,
        ) {
            if (displayName.isNullOrBlank()) {
                Icon(Icons.Default.Person, null, tint = Accent, modifier = Modifier.size(19.dp))
            } else {
                Text(
                    displayName.split(' ').mapNotNull { it.firstOrNull()?.uppercase() }.take(2).joinToString(""),
                    color = Accent,
                    style = MaterialTheme.typography.labelMedium,
                )
            }
        }
    }
}

@Composable
private fun ReadyHome(
    state: HomeUiState.Ready,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    BoxWithConstraints(modifier.fillMaxSize()) {
        val expanded = maxWidth >= 600.dp
        val horizontalPadding = if (expanded) 24.dp else 14.dp
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(
                start = horizontalPadding,
                end = horizontalPadding,
                top = 8.dp,
                bottom = 24.dp,
            ),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            item { WelcomeCard(state.snapshot, state.quote, state.localTime) }
            if (expanded) {
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        ProgressCard(state.snapshot, onRetry, Modifier.weight(0.9f).fillMaxHeight())
                        ScheduleCard(state.snapshot.planState, onRetry, Modifier.weight(1.25f))
                    }
                }
                item {
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        InsightCard(state.snapshot.insightState, Modifier.weight(1.25f).fillMaxHeight())
                        QuickActions(Modifier.weight(1f))
                    }
                }
            } else {
                item { ProgressCard(state.snapshot, onRetry) }
                item { ScheduleCard(state.snapshot.planState, onRetry) }
                item { InsightCard(state.snapshot.insightState) }
                item { QuickActions() }
            }
        }
    }
}

@Composable
private fun LoadingHome(modifier: Modifier = Modifier) {
    Column(
        modifier = modifier.fillMaxSize().padding(14.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        CircularProgressIndicator(color = Accent, strokeWidth = 3.dp, modifier = Modifier.size(34.dp))
        Spacer(Modifier.height(14.dp))
        Text("Loading your day…", color = TextMuted, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun WelcomeCard(snapshot: HomeSnapshot, quote: DailyQuote, localTime: LocalTime) {
    val locale = LocalConfiguration.current.locales[0]
    val shape = RoundedCornerShape(20.dp)
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .background(
                Brush.linearGradient(
                    colors = listOf(HeroStart, Card, HeroEnd),
                    start = Offset.Zero,
                    end = Offset(950f, 600f),
                ),
            )
            .border(1.dp, Border, shape)
            .padding(18.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(4.dp).clip(CircleShape).background(Accent))
            Spacer(Modifier.width(6.dp))
            Text(
                snapshot.localDate.format(DateTimeFormatter.ofPattern("EEEE, MMMM d", locale)),
                color = TextMuted,
                style = MaterialTheme.typography.labelMedium,
            )
        }
        val greeting = greetingFor(localTime)
        val title = snapshot.displayName?.takeIf(String::isNotBlank)?.let { "$greeting, $it." } ?: "$greeting."
        Text(title, style = MaterialTheme.typography.displaySmall, modifier = Modifier.padding(top = 7.dp))
        Text(
            if (snapshot.planState is TodayPlanState.Available) {
                "Keep the next step small and focused."
            } else {
                "Start with one important task, then build around it."
            },
            color = TextMuted,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.padding(top = 3.dp),
        )
        HorizontalDivider(Modifier.padding(vertical = 13.dp), color = BorderSoft)
        Row(verticalAlignment = Alignment.Top) {
            Text("“", color = Accent, fontSize = 28.sp, lineHeight = 24.sp, fontWeight = FontWeight.ExtraBold)
            Spacer(Modifier.width(8.dp))
            Column {
                Text(quote.text, color = TextPrimary, style = MaterialTheme.typography.bodySmall)
                Text(
                    quote.attribution?.let { "— $it" } ?: quote.category.label,
                    color = TextSubtle,
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun ProgressCard(
    snapshot: HomeSnapshot,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    DashboardCard(modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            SectionHeading("Today", "Your progress", Modifier.weight(1f))
            snapshot.streakDays?.let {
                Text(
                    "↗ $it-day streak",
                    color = Accent,
                    style = MaterialTheme.typography.labelSmall,
                    modifier = Modifier
                        .clip(CircleShape)
                        .background(AccentSoft)
                        .border(1.dp, AccentLine, CircleShape)
                        .padding(horizontal = 9.dp, vertical = 6.dp),
                )
            }
        }
        Spacer(Modifier.height(13.dp))
        val summary = progressFor(snapshot.planState)
        if (summary != null) {
            ProgressAvailable(summary)
        } else {
            ProgressUnavailable(snapshot.planState, onRetry)
        }
    }
}

@Composable
private fun ProgressAvailable(summary: ProgressSummary) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        ProgressRing(summary.completionPercent)
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            Text("${summary.completed} of ${summary.total} done", style = MaterialTheme.typography.titleLarge)
            Text(
                when (val open = summary.inProgress + summary.pending) {
                    0 -> "Everything in today's plan is complete."
                    1 -> "One task is still open today."
                    else -> "$open tasks are still open today."
                },
                color = TextMuted,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.padding(top = 3.dp),
            )
            Row(
                modifier = Modifier.padding(top = 8.dp),
                horizontalArrangement = Arrangement.spacedBy(5.dp),
            ) {
                if (summary.inProgress > 0) ProgressStatusChip("${summary.inProgress} active", Accent)
                if (summary.pending > 0) ProgressStatusChip("${summary.pending} pending", TextMuted)
                if (summary.failed > 0) ProgressStatusChip("${summary.failed} failed", MaterialTheme.colorScheme.error)
            }
        }
    }
}

@Composable
private fun ProgressStatusChip(label: String, tint: Color) {
    Text(
        label,
        color = tint,
        style = MaterialTheme.typography.labelSmall,
        modifier = Modifier
            .clip(CircleShape)
            .background(Raised)
            .padding(horizontal = 7.dp, vertical = 4.dp),
    )
}

@Composable
private fun ProgressUnavailable(planState: TodayPlanState, onRetry: () -> Unit) {
    val (title, detail) = when (planState) {
        TodayPlanState.Loading -> "Loading progress" to "Waiting for today's plan."
        TodayPlanState.NoPlan -> "Your day is open" to "Add your first task when you're ready."
        TodayPlanState.EmptyPlan -> "Ready for your first task" to "Add something important and Zen Maestro will help you make room for it."
        is TodayPlanState.Unavailable -> "Progress unavailable" to planState.reason.userMessage()
        is TodayPlanState.Available -> "No progress yet" to "Complete your first task to start today's progress."
    }
    UnavailableBlock(title, detail, retry = planState is TodayPlanState.Unavailable && planState.reason == UnavailableReason.NETWORK_ERROR, onRetry)
}

@Composable
private fun ProgressRing(percent: Int) {
    Box(Modifier.size(76.dp), contentAlignment = Alignment.Center) {
        Canvas(Modifier.fillMaxSize()) {
            val stroke = 9.dp.toPx()
            val inset = stroke / 2
            drawArc(
                color = Border,
                startAngle = -90f,
                sweepAngle = 360f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - stroke, size.height - stroke),
                style = Stroke(stroke, cap = StrokeCap.Round),
            )
            drawArc(
                color = Accent,
                startAngle = -90f,
                sweepAngle = 360f * percent.coerceIn(0, 100) / 100f,
                useCenter = false,
                topLeft = Offset(inset, inset),
                size = Size(size.width - stroke, size.height - stroke),
                style = Stroke(stroke, cap = StrokeCap.Round),
            )
        }
        Text("$percent%", style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun ScheduleCard(
    planState: TodayPlanState,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    DashboardCard(modifier) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            SectionHeading("Next up", "Today's schedule", Modifier.weight(1f))
            Text(
                "See all",
                color = Accent,
                style = MaterialTheme.typography.labelMedium,
                modifier = Modifier.semantics {
                    contentDescription = "See all schedules unavailable"
                    disabled()
                },
            )
            Icon(Icons.AutoMirrored.Filled.ArrowForward, null, tint = Accent, modifier = Modifier.size(15.dp))
        }
        Spacer(Modifier.height(8.dp))
        when (planState) {
            is TodayPlanState.Available -> {
                val tasks = planState.tasks
                    .sortedWith(compareBy<HomeTask> { it.status == TaskStatus.COMPLETED }.thenBy { it.scheduledStart ?: "99:99" })
                    .take(3)
                if (tasks.isEmpty()) {
                    UnavailableBlock("No scheduled tasks", "This plan does not contain any tasks.", false, onRetry)
                } else {
                    tasks.forEachIndexed { index, task ->
                        if (index > 0) HorizontalDivider(color = BorderSoft)
                        TaskRow(task)
                    }
                }
            }
            TodayPlanState.NoPlan -> UnavailableBlock("Nothing planned yet", "Add a task or plan ahead from Schedule.", false, onRetry)
            TodayPlanState.EmptyPlan -> UnavailableBlock("Your schedule is clear", "Add a task when you're ready to shape the day.", false, onRetry)
            TodayPlanState.Loading -> UnavailableBlock("Loading schedule", "Waiting for today's plan.", false, onRetry)
            is TodayPlanState.Unavailable -> UnavailableBlock("Schedule unavailable", planState.reason.userMessage(), planState.reason == UnavailableReason.NETWORK_ERROR, onRetry)
        }
    }
}

@Composable
private fun TaskRow(task: HomeTask) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(task.scheduledStart?.take(5) ?: "—", color = TextMuted, style = MaterialTheme.typography.labelMedium, modifier = Modifier.width(62.dp))
        Column(Modifier.weight(1f)) {
            Text(task.title, maxLines = 1, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.titleSmall)
            Text("${task.estimatedMinutes} min · Priority ${task.priority}", color = TextMuted, style = MaterialTheme.typography.bodySmall)
        }
        TaskStatusChip(task.status)
    }
}

@Composable
private fun TaskStatusChip(status: TaskStatus) {
    val (label, color, background) = when (status) {
        TaskStatus.COMPLETED -> Triple("Done", Success, Success.copy(alpha = .09f))
        TaskStatus.IN_PROGRESS -> Triple("Active", Accent, AccentSoft)
        TaskStatus.PENDING -> Triple("Pending", TextMuted, Raised)
        TaskStatus.FAILED -> Triple("Failed", MaterialTheme.colorScheme.error, MaterialTheme.colorScheme.error.copy(alpha = .09f))
    }
    Text(
        label,
        color = color,
        style = MaterialTheme.typography.labelSmall,
        modifier = Modifier.clip(CircleShape).background(background).border(1.dp, color.copy(alpha = .35f), CircleShape).padding(horizontal = 7.dp, vertical = 5.dp),
    )
}

@Composable
private fun InsightCard(state: InsightState, modifier: Modifier = Modifier) {
    val (title, detail) = when (state) {
        is InsightState.Available -> state.title to state.evidence
        is InsightState.OnboardingSuggestion -> state.title to state.detail
        InsightState.Unavailable -> "Smart insights aren't available yet" to "Complete a few scheduled tasks to unlock a reliable personal insight."
    }
    val shape = RoundedCornerShape(17.dp)
    Row(
        modifier = modifier
            .fillMaxWidth()
            .clip(shape)
            .background(Brush.horizontalGradient(listOf(AccentSoft.copy(alpha = .54f), Card, Card)))
            .border(1.dp, Border, shape)
            .padding(horizontal = 15.dp, vertical = 13.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Box(
            Modifier.size(32.dp).clip(RoundedCornerShape(10.dp)).background(AccentSoft).border(1.dp, AccentLine, RoundedCornerShape(10.dp)),
            contentAlignment = Alignment.Center,
        ) { Icon(Icons.Outlined.Insights, null, tint = Accent, modifier = Modifier.size(18.dp)) }
        Spacer(Modifier.width(11.dp))
        Column {
            Eyebrow("One smart insight")
            Text(title, style = MaterialTheme.typography.titleSmall)
            Text(detail, color = TextMuted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 3.dp))
        }
    }
}

@Composable
private fun QuickActions(modifier: Modifier = Modifier) {
    DashboardCard(modifier) {
        Text("Quick actions", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(10.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
            QuickAction("Plan today", Icons.Default.AddTask, Modifier.weight(1f), emphasized = true)
            QuickAction("Schedule", Icons.Default.Schedule, Modifier.weight(1f))
            QuickAction("AI Coach", Icons.Default.AutoAwesome, Modifier.weight(1f))
            QuickAction("Future plan", Icons.Default.CalendarMonth, Modifier.weight(1f))
        }
        Text(
            "Open Schedule to add work, plan ahead, or adjust your day.",
            color = TextSubtle,
            style = MaterialTheme.typography.bodySmall,
            modifier = Modifier.padding(top = 8.dp),
        )
    }
}

@Composable
private fun QuickAction(
    label: String,
    icon: ImageVector,
    modifier: Modifier = Modifier,
    emphasized: Boolean = false,
) {
    val background = if (emphasized) Accent.copy(alpha = .62f) else Raised
    val borderColor = if (emphasized) Accent else Border
    val contentColor = if (emphasized) Color(0xFF1B110D) else TextSubtle
    Column(
        modifier = modifier
            .height(64.dp)
            .clip(RoundedCornerShape(12.dp))
            .background(background)
            .border(1.dp, borderColor, RoundedCornerShape(12.dp))
            .semantics {
                contentDescription = "$label unavailable"
                role = Role.Button
                disabled()
            },
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(icon, null, tint = contentColor, modifier = Modifier.size(19.dp))
        Text(label, color = contentColor, style = MaterialTheme.typography.labelSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
private fun DashboardCard(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        color = Card,
        shape = RoundedCornerShape(17.dp),
        border = androidx.compose.foundation.BorderStroke(1.dp, Border),
    ) {
        Column(Modifier.padding(15.dp)) { content() }
    }
}

@Composable
private fun SectionHeading(eyebrow: String, title: String, modifier: Modifier = Modifier) {
    Column(modifier) { Eyebrow(eyebrow); Text(title, style = MaterialTheme.typography.titleMedium) }
}

@Composable
private fun Eyebrow(text: String) {
    val locale = LocalConfiguration.current.locales[0]
    Text(text.uppercase(locale), color = TextMuted, style = MaterialTheme.typography.labelSmall)
}

@Composable
private fun UnavailableBlock(
    title: String,
    detail: String,
    retry: Boolean,
    onRetry: () -> Unit,
) {
    Column(Modifier.fillMaxWidth().padding(vertical = 7.dp)) {
        Text(title, style = MaterialTheme.typography.titleSmall)
        Text(detail, color = TextMuted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 3.dp))
        if (retry) {
            Button(
                onClick = onRetry,
                colors = ButtonDefaults.buttonColors(containerColor = Accent, contentColor = Background),
                contentPadding = PaddingValues(horizontal = 13.dp, vertical = 7.dp),
                modifier = Modifier.padding(top = 9.dp),
            ) {
                Icon(Icons.Default.Refresh, null, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(6.dp))
                Text("Retry")
            }
        }
    }
}

private fun UnavailableReason.userMessage(): String = when (this) {
    UnavailableReason.API_NOT_CONFIGURED -> "Today's plan isn't available right now. Try again shortly."
    UnavailableReason.AUTHENTICATION_REQUIRED -> "Your session has expired. Sign in again to see your day."
    UnavailableReason.NETWORK_ERROR -> "Couldn't refresh your day. Check your connection and try again."
    UnavailableReason.INVALID_RESPONSE -> "We couldn't display today's plan. Try refreshing it."
}

@Composable
private fun CoachBottomBar(onNavigateToSchedule: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(NavigationSurface.copy(alpha = .97f))
            .navigationBarsPadding(),
    ) {
        HorizontalDivider(color = BorderSoft)
        Row(
            modifier = Modifier.fillMaxWidth().height(72.dp).padding(horizontal = 9.dp, vertical = 7.dp),
        ) {
            BottomDestination("Home", Icons.Default.Home, true)
            BottomDestination("Schedule", Icons.Default.Schedule, false, onNavigateToSchedule)
            BottomDestination("Coach", Icons.Default.AutoAwesome, false)
            BottomDestination("More", Icons.Default.MoreHoriz, false)
        }
    }
}

@Composable
private fun RowScope.BottomDestination(
    label: String,
    icon: ImageVector,
    selected: Boolean,
    onClick: (() -> Unit)? = null,
) {
    val tint = if (selected) Accent else TextSubtle
    Column(
        modifier = Modifier
            .weight(1f)
            .fillMaxHeight()
            .clickable(enabled = onClick != null, role = Role.Button) { onClick?.invoke() }
            .semantics {
                contentDescription = if (selected || onClick != null) label else "$label unavailable"
                role = Role.Button
                if (!selected && onClick == null) disabled()
            },
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(
            modifier = Modifier
                .height(25.dp)
                .width(if (selected) 48.dp else 28.dp)
                .clip(CircleShape)
                .background(if (selected) AccentSoft else Color.Transparent),
            contentAlignment = Alignment.Center,
        ) {
            Icon(icon, null, tint = tint, modifier = Modifier.size(18.dp))
        }
        Spacer(Modifier.height(3.dp))
        Text(label, color = tint, style = MaterialTheme.typography.labelSmall)
    }
}
