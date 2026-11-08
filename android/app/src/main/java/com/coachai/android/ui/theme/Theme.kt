package com.coachai.android.ui.theme

import android.app.Activity
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val ColorOnAccent = androidx.compose.ui.graphics.Color(0xFF1B110D)

@Composable
fun CoachAiTheme(lightTheme: Boolean, content: @Composable () -> Unit) {
    useLightPalette(lightTheme)
    val coachColorScheme = if (lightTheme) lightColorScheme(
        primary = Accent,
        onPrimary = ColorOnAccent,
        primaryContainer = AccentSoft,
        onPrimaryContainer = TextPrimary,
        secondary = Amber,
        background = Background,
        onBackground = TextPrimary,
        surface = Surface,
        onSurface = TextPrimary,
        surfaceVariant = Card,
        onSurfaceVariant = TextMuted,
        outline = Border,
        error = Danger,
    ) else darkColorScheme(
        primary = Accent,
        onPrimary = ColorOnAccent,
        primaryContainer = AccentSoft,
        onPrimaryContainer = TextPrimary,
        secondary = Amber,
        background = Background,
        onBackground = TextPrimary,
        surface = Surface,
        onSurface = TextPrimary,
        surfaceVariant = Card,
        onSurfaceVariant = TextMuted,
        outline = Border,
        error = Danger,
    )
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = Background.toArgb()
            window.navigationBarColor = NavigationSurface.toArgb()
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = lightTheme
                isAppearanceLightNavigationBars = lightTheme
            }
        }
    }
    MaterialTheme(
        colorScheme = coachColorScheme,
        typography = CoachTypography,
        content = content,
    )
}
