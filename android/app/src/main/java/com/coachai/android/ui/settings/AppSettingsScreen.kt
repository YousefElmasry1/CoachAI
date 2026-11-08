package com.coachai.android.ui.settings

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.AccessTime
import androidx.compose.material.icons.outlined.ChevronRight
import androidx.compose.material.icons.outlined.Language
import androidx.compose.material.icons.outlined.LightMode
import androidx.compose.material.icons.outlined.MotionPhotosOff
import androidx.compose.material.icons.outlined.TouchApp
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.view.HapticFeedbackConstants
import com.coachai.android.BuildConfig
import com.coachai.android.ui.theme.Accent
import com.coachai.android.ui.theme.AccentSoft
import com.coachai.android.ui.theme.Background
import com.coachai.android.ui.theme.Border
import com.coachai.android.ui.theme.Card
import com.coachai.android.ui.theme.TextMuted
import com.coachai.android.ui.theme.TextPrimary

private data class ChoiceDialogState(
    val title: String,
    val options: List<String>,
    val selected: String,
    val onSelect: (String) -> Unit,
)

@Composable
fun AppSettingsScreen(
    state: AppSettingsUiState,
    onBack: () -> Unit,
    onTheme: (String) -> Unit,
    onLanguage: (String) -> Unit,
    onTimeFormat: (String) -> Unit,
    onHaptics: (Boolean) -> Unit,
    onReduceMotion: (Boolean) -> Unit,
    onSignOut: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var dialog by remember { mutableStateOf<ChoiceDialogState?>(null) }
    val view = LocalView.current
    fun hapticIfEnabled() {
        if (state.hapticFeedback) view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
    }
    Column(modifier.fillMaxSize().background(Background).statusBarsPadding()) {
        Header(onBack)
        Column(
            Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Surface(
                shape = RoundedCornerShape(18.dp),
                color = AccentSoft,
                border = BorderStroke(1.dp, Accent.copy(alpha = .35f)),
            ) {
                Text(
                    "Choose the theme, time display and interaction comfort on this phone.",
                    color = TextPrimary,
                    fontSize = 14.sp,
                    lineHeight = 20.sp,
                    modifier = Modifier.fillMaxWidth().padding(16.dp),
                )
            }

            SectionLabel("APPEARANCE")
            SettingsGroup {
                ValueRow(
                    Icons.Outlined.LightMode,
                    "Theme",
                    "The app's current color style",
                    state.theme,
                ) {
                    dialog = ChoiceDialogState("Theme", listOf("Light", "Dark"), state.theme) {
                        hapticIfEnabled()
                        onTheme(it)
                    }
                }
            }

            SectionLabel("LANGUAGE & TIME")
            SettingsGroup {
                ValueRow(
                    Icons.Outlined.Language,
                    "App language",
                    "More languages are coming after full translation",
                    "English",
                )
                DividerLine()
                ValueRow(
                    Icons.Outlined.AccessTime,
                    "Time display",
                    "Show times like 4:30 PM or 16:30",
                    state.timeFormat,
                ) {
                    dialog = ChoiceDialogState("Time format", listOf("12-hour", "24-hour"), state.timeFormat) {
                        hapticIfEnabled()
                        onTimeFormat(it)
                    }
                }
            }

            SectionLabel("TOUCH & MOTION")
            SettingsGroup {
                ToggleRow(
                    Icons.Outlined.TouchApp,
                    "Settings vibration",
                    "A small vibration when you change supported settings",
                    state.hapticFeedback,
                ) {
                    onHaptics(it)
                    if (it) view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
                }
                DividerLine()
                ToggleRow(
                    Icons.Outlined.MotionPhotosOff,
                    "Fewer animations",
                    "Replaces optional loading motion in supported screens",
                    state.reduceMotion,
                ) {
                    hapticIfEnabled()
                    onReduceMotion(it)
                }
            }

            Text(
                "Zen Maestro for Android · Version ${BuildConfig.VERSION_NAME}",
                color = TextMuted,
                fontSize = 12.sp,
                modifier = Modifier.fillMaxWidth().padding(top = 10.dp, bottom = 28.dp),
            )
            TextButton(onClick = onSignOut, modifier = Modifier.fillMaxWidth()) {
                Text("Sign out", color = com.coachai.android.ui.theme.Danger)
            }
        }
    }

    dialog?.let { current ->
        AlertDialog(
            onDismissRequest = { dialog = null },
            containerColor = Card,
            title = { Text(current.title, color = TextPrimary) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    current.options.forEach { option ->
                        Row(
                            Modifier.fillMaxWidth().clickable {
                                current.onSelect(option)
                                dialog = null
                            }.padding(vertical = 12.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(option, color = if (option == current.selected) Accent else TextPrimary, modifier = Modifier.weight(1f))
                            if (option == current.selected) Text("✓", color = Accent, fontWeight = FontWeight.Bold)
                        }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { dialog = null }) { Text("Done", color = Accent) } },
        )
    }
}

@Composable
private fun Header(onBack: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onBack) { Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Back", tint = TextPrimary) }
        Column(Modifier.padding(start = 4.dp)) {
            Text("Settings", color = TextPrimary, style = MaterialTheme.typography.titleLarge)
            Text("Display and interaction preferences", color = TextMuted, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun SettingsGroup(content: @Composable ColumnScope.() -> Unit) {
    Surface(
        shape = RoundedCornerShape(22.dp),
        color = Card,
        border = BorderStroke(1.dp, Border),
    ) { Column(content = content) }
}

@Composable
private fun ValueRow(
    icon: ImageVector,
    title: String,
    detail: String,
    value: String,
    onClick: (() -> Unit)? = null,
) {
    Row(
        Modifier.fillMaxWidth()
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .padding(horizontal = 17.dp, vertical = 16.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, tint = Accent, modifier = Modifier.size(24.dp))
        Column(Modifier.weight(1f).padding(start = 14.dp, end = 8.dp)) {
            Text(title, color = TextPrimary, fontWeight = FontWeight.SemiBold)
            Text(detail, color = TextMuted, fontSize = 12.sp, lineHeight = 17.sp, modifier = Modifier.padding(top = 3.dp))
        }
        Text(value, color = TextMuted, fontSize = 14.sp)
        if (onClick != null) {
            Icon(Icons.Outlined.ChevronRight, null, tint = TextMuted, modifier = Modifier.padding(start = 6.dp).size(20.dp))
        }
    }
}

@Composable
private fun ToggleRow(
    icon: ImageVector,
    title: String,
    detail: String,
    checked: Boolean,
    onChecked: (Boolean) -> Unit,
) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 17.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
        Icon(icon, null, tint = Accent, modifier = Modifier.size(24.dp))
        Column(Modifier.weight(1f).padding(horizontal = 14.dp)) {
            Text(title, color = TextPrimary, fontWeight = FontWeight.SemiBold)
            Text(detail, color = TextMuted, fontSize = 12.sp, lineHeight = 17.sp, modifier = Modifier.padding(top = 3.dp))
        }
        Switch(
            checked = checked,
            onCheckedChange = onChecked,
            colors = SwitchDefaults.colors(
                checkedThumbColor = TextPrimary,
                checkedTrackColor = Accent,
                uncheckedThumbColor = TextMuted,
                uncheckedTrackColor = AccentSoft,
            ),
        )
    }
}

@Composable
private fun DividerLine() = Box(Modifier.fillMaxWidth().padding(horizontal = 18.dp).height(1.dp).background(Border))

@Composable
private fun SectionLabel(text: String) = Text(
    text,
    color = TextMuted,
    fontSize = 12.sp,
    fontWeight = FontWeight.Bold,
    letterSpacing = 1.2.sp,
    modifier = Modifier.padding(top = 4.dp, start = 4.dp),
)
