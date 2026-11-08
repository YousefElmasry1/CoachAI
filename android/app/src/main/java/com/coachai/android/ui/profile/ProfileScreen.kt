package com.coachai.android.ui.profile

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Badge
import androidx.compose.material.icons.outlined.Check
import androidx.compose.material.icons.outlined.Edit
import androidx.compose.material.icons.outlined.MailOutline
import androidx.compose.material.icons.outlined.Public
import androidx.compose.material.icons.outlined.CalendarMonth
import androidx.compose.material.icons.outlined.Lock
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.coachai.android.data.remote.ProfileData
import com.coachai.android.ui.theme.Accent
import com.coachai.android.ui.theme.AccentLine
import com.coachai.android.ui.theme.AccentSoft
import com.coachai.android.ui.theme.AccentText
import com.coachai.android.ui.theme.Background
import com.coachai.android.ui.theme.Border
import com.coachai.android.ui.theme.Card
import com.coachai.android.ui.theme.Raised
import com.coachai.android.ui.theme.TextMuted
import com.coachai.android.ui.theme.TextPrimary
import java.time.LocalDate
import java.time.format.DateTimeFormatter

private val timezoneChoices = listOf(
    "Africa/Cairo",
    "Asia/Riyadh",
    "Asia/Dubai",
    "Europe/London",
    "America/New_York",
)

@Composable
fun ProfileScreen(
    uiState: ProfileUiState,
    onBack: () -> Unit,
    onUpdateName: (String) -> Unit,
    onUpdateTimezone: (String) -> Unit,
    onRetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .fillMaxSize()
            .background(Background)
            .statusBarsPadding(),
    ) {
        ProfileTopBar(onBack)
        when (uiState) {
            ProfileUiState.Loading -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = Accent)
            }
            is ProfileUiState.Error -> ProfileError(uiState.message, onRetry)
            is ProfileUiState.Ready -> ProfileContent(
                state = uiState,
                onUpdateName = onUpdateName,
                onUpdateTimezone = onUpdateTimezone,
            )
        }
    }
}

@Composable
private fun ProfileError(message: String, onRetry: () -> Unit) {
    Box(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Surface(
            color = Card,
            shape = RoundedCornerShape(22.dp),
            border = BorderStroke(1.dp, Border),
        ) {
            Column(
                modifier = Modifier.fillMaxWidth().padding(20.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                Text("Profile unavailable", color = TextPrimary, style = MaterialTheme.typography.titleMedium)
                Text(message, color = TextMuted, style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = onRetry) { Text("Try again", color = Accent) }
            }
        }
    }
}

@Composable
private fun ProfileTopBar(onBack: () -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back", tint = TextPrimary)
        }
        Column(Modifier.padding(start = 4.dp)) {
            Text("Profile", color = TextPrimary, style = MaterialTheme.typography.titleLarge)
            Text(
                "Account & planning preferences",
                color = TextMuted,
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
}

@Composable
private fun ProfileContent(
    state: ProfileUiState.Ready,
    onUpdateName: (String) -> Unit,
    onUpdateTimezone: (String) -> Unit,
) {
    var editName by remember { mutableStateOf(false) }
    var editTimezone by remember { mutableStateOf(false) }
    val profile = state.profile
    val scrollState = rememberScrollState()
    LaunchedEffect(Unit) { scrollState.scrollTo(0) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(scrollState)
            .padding(horizontal = 18.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        HeroProfileCard(
            profile = profile,
            onEdit = { editName = true },
        )

        if (state.message != null) {
            Surface(
                color = AccentSoft,
                shape = RoundedCornerShape(14.dp),
                border = BorderStroke(1.dp, AccentLine),
            ) {
                Text(
                    state.message,
                    color = AccentText,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.fillMaxWidth().padding(12.dp),
                )
            }
        }

        Text(
            "ACCOUNT",
            color = TextMuted,
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.padding(top = 8.dp, start = 4.dp),
        )
        Surface(
            color = Card,
            shape = RoundedCornerShape(22.dp),
            border = BorderStroke(1.dp, Border),
        ) {
            Column {
                ProfileRow(
                    icon = Icons.Outlined.Badge,
                    title = "Display name",
                    value = profile.displayName,
                    actionIcon = Icons.Outlined.Edit,
                    onClick = { editName = true },
                )
                DividerLine()
                ProfileRow(
                    icon = Icons.Outlined.MailOutline,
                    title = "Email",
                    value = profile.email,
                    supporting = "Used for your Zen Maestro account",
                )
                DividerLine()
                ProfileRow(
                    icon = Icons.Outlined.CalendarMonth,
                    title = "Member since",
                    value = runCatching {
                        LocalDate.parse(profile.createdAt.take(10))
                            .format(DateTimeFormatter.ofPattern("MMM d, yyyy"))
                    }.getOrDefault("Account active"),
                    supporting = "Your planning history stays with this account",
                )
                DividerLine()
                ProfileRow(
                    icon = Icons.Outlined.Lock,
                    title = "Account ID",
                    value = "•••• ${profile.userId.takeLast(6)}",
                    supporting = "Secure account ID",
                )
            }
        }

        Text(
            "PLANNING",
            color = TextMuted,
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier.padding(top = 8.dp, start = 4.dp),
        )
        Surface(
            modifier = Modifier.fillMaxWidth().clickable { editTimezone = true },
            color = Card,
            shape = RoundedCornerShape(22.dp),
            border = BorderStroke(1.dp, Border),
        ) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                IconTile(Icons.Outlined.Public)
                Column(Modifier.weight(1f).padding(horizontal = 13.dp)) {
                    Text("Home timezone", color = TextMuted, style = MaterialTheme.typography.bodySmall)
                    Text(
                        profile.timezone.replace('_', ' '),
                        color = TextPrimary,
                        style = MaterialTheme.typography.titleSmall,
                        modifier = Modifier.padding(top = 2.dp),
                    )
                }
                Icon(Icons.Outlined.Edit, "Edit timezone", tint = Accent, modifier = Modifier.size(20.dp))
            }
        }
        Spacer(Modifier.height(20.dp))
    }

    if (editName) {
        NameDialog(
            current = profile.displayName,
            saving = state.saving,
            onDismiss = { editName = false },
            onSave = {
                editName = false
                onUpdateName(it)
            },
        )
    }
    if (editTimezone) {
        TimezoneDialog(
            selected = profile.timezone,
            onDismiss = { editTimezone = false },
            onSelect = {
                editTimezone = false
                onUpdateTimezone(it)
            },
        )
    }
}

@Composable
private fun HeroProfileCard(
    profile: ProfileData,
    onEdit: () -> Unit,
) {
    val initials = profile.displayName
        .split(' ')
        .filter(String::isNotBlank)
        .take(2)
        .joinToString("") { it.first().uppercase() }
        .ifBlank { "ZM" }
    Surface(
        shape = RoundedCornerShape(24.dp),
        border = BorderStroke(1.dp, Border),
        color = Card,
    ) {
        Row(
            Modifier.fillMaxWidth().padding(18.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(
                modifier = Modifier
                    .size(68.dp)
                    .clip(CircleShape)
                    .background(Brush.linearGradient(listOf(Color(0xFFFF9D73), Color(0xFFFF6F3C)))),
                contentAlignment = Alignment.Center,
            ) {
                Text(initials, color = Color(0xFF2B160E), style = MaterialTheme.typography.titleLarge)
            }
            Column(Modifier.weight(1f).padding(start = 15.dp)) {
                Text(
                    profile.displayName,
                    color = TextPrimary,
                    style = MaterialTheme.typography.titleLarge,
                    fontWeight = FontWeight.Bold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    profile.email,
                    color = TextMuted,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.padding(top = 3.dp),
                )
            }
            IconButton(onClick = onEdit) {
                Icon(Icons.Outlined.Edit, "Edit name", tint = Accent)
            }
        }
    }
}

@Composable
private fun ProfileRow(
    icon: ImageVector,
    title: String,
    value: String,
    supporting: String? = null,
    actionIcon: ImageVector? = null,
    onClick: (() -> Unit)? = null,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconTile(icon)
        Column(Modifier.weight(1f).padding(horizontal = 13.dp)) {
            Text(title, color = TextMuted, style = MaterialTheme.typography.bodySmall)
            Text(value, color = TextPrimary, style = MaterialTheme.typography.titleSmall, modifier = Modifier.padding(top = 2.dp))
            supporting?.let { Text(it, color = TextMuted, style = MaterialTheme.typography.bodySmall, modifier = Modifier.padding(top = 2.dp)) }
        }
        actionIcon?.let { Icon(it, "Edit", tint = Accent, modifier = Modifier.size(20.dp)) }
    }
}

@Composable
private fun IconTile(icon: ImageVector) {
    Box(
        Modifier.size(44.dp).clip(RoundedCornerShape(14.dp)).background(Raised),
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, null, tint = Accent, modifier = Modifier.size(22.dp))
    }
}

@Composable
private fun DividerLine() {
    Box(Modifier.fillMaxWidth().padding(horizontal = 16.dp).height(1.dp).background(Border))
}

@Composable
private fun NameDialog(
    current: String,
    saving: Boolean,
    onDismiss: () -> Unit,
    onSave: (String) -> Unit,
) {
    var value by remember(current) { mutableStateOf(current) }
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Raised,
        title = { Text("Display name", color = TextPrimary) },
        text = {
            OutlinedTextField(
                value = value,
                onValueChange = { if (it.length <= 100) value = it },
                label = { Text("Your name") },
                singleLine = true,
            )
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel", color = TextMuted) } },
        confirmButton = {
            TextButton(
                enabled = value.isNotBlank() && !saving,
                onClick = { onSave(value.trim()) },
            ) { Text("Save", color = Accent) }
        },
    )
}

@Composable
private fun TimezoneDialog(
    selected: String,
    onDismiss: () -> Unit,
    onSelect: (String) -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        containerColor = Raised,
        title = { Text("Home timezone", color = TextPrimary) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                timezoneChoices.forEach { timezone ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(12.dp))
                            .clickable { onSelect(timezone) }
                            .background(if (timezone == selected) AccentSoft else Color.Transparent)
                            .padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text(timezone.replace('_', ' '), color = TextPrimary, modifier = Modifier.weight(1f))
                        if (timezone == selected) Icon(Icons.Outlined.Check, null, tint = Accent, modifier = Modifier.size(20.dp))
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close", color = Accent) } },
    )
}
