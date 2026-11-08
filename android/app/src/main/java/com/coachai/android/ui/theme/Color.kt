package com.coachai.android.ui.theme

import androidx.compose.ui.graphics.Color

private data class CoachPalette(
    val background: Color,
    val navigationSurface: Color,
    val surface: Color,
    val card: Color,
    val raised: Color,
    val border: Color,
    val borderSoft: Color,
    val accentSoft: Color,
    val accentLine: Color,
    val accentText: Color,
    val heroStart: Color,
    val heroEnd: Color,
    val textPrimary: Color,
    val textMuted: Color,
    val textSubtle: Color,
    val success: Color,
    val danger: Color,
    val amber: Color,
)

private val LightPalette = CoachPalette(
    background = Color(0xFFFAF9F7),
    navigationSurface = Color(0xFFFFFFFF),
    surface = Color(0xFFFFFFFF),
    card = Color(0xFFFFFFFF),
    raised = Color(0xFFF4F1EE),
    border = Color(0xFFE2DDD8),
    borderSoft = Color(0xFFEDE9E5),
    accentSoft = Color(0xFFFFEEE6),
    accentLine = Color(0xFFFFC2A6),
    accentText = Color(0xFFC94F1F),
    heroStart = Color(0xFFFFF3ED),
    heroEnd = Color(0xFFFFFFFF),
    textPrimary = Color(0xFF241E1B),
    textMuted = Color(0xFF706863),
    textSubtle = Color(0xFF7E756F),
    success = Color(0xFF168554),
    danger = Color(0xFFD94848),
    amber = Color(0xFFA66A16),
)

private val DarkPalette = CoachPalette(
    background = Color(0xFF08080A),
    navigationSurface = Color(0xFF0D0D0F),
    surface = Color(0xFF111113),
    card = Color(0xFF18181B),
    raised = Color(0xFF1E1E22),
    border = Color(0xFF303036),
    borderSoft = Color(0xFF252529),
    accentSoft = Color(0xFF3A2418),
    accentLine = Color(0xFF754229),
    accentText = Color(0xFFFF9E75),
    heroStart = Color(0xFF211814),
    heroEnd = Color(0xFF141416),
    textPrimary = Color(0xFFEDEDEF),
    textMuted = Color(0xFF9B9BA3),
    textSubtle = Color(0xFF6F6F78),
    success = Color(0xFF3DD68C),
    danger = Color(0xFFFF6B6B),
    amber = Color(0xFFD4A25A),
)

private var activePalette = LightPalette

internal fun useLightPalette(enabled: Boolean) {
    activePalette = if (enabled) LightPalette else DarkPalette
}

val Background get() = activePalette.background
val NavigationSurface get() = activePalette.navigationSurface
val Surface get() = activePalette.surface
val Card get() = activePalette.card
val Raised get() = activePalette.raised
val Border get() = activePalette.border
val BorderSoft get() = activePalette.borderSoft
val Accent = Color(0xFFFF7A45)
val AccentSoft get() = activePalette.accentSoft
val AccentLine get() = activePalette.accentLine
val AccentText get() = activePalette.accentText
val HeroStart get() = activePalette.heroStart
val HeroEnd get() = activePalette.heroEnd
val TextPrimary get() = activePalette.textPrimary
val TextMuted get() = activePalette.textMuted
val TextSubtle get() = activePalette.textSubtle
val Success get() = activePalette.success
val Danger get() = activePalette.danger
val Amber get() = activePalette.amber
