package com.coachai.android.ui.navigation

fun navigateToStack(current: List<String>, target: AppDestination): List<String> {
    require(current.isNotEmpty())
    if (current.last() == target.name) return current
    val roots = setOf(
        AppDestination.Home,
        AppDestination.Schedule,
        AppDestination.Ask,
        AppDestination.Analytics,
        AppDestination.More,
    )
    return if (target in roots) listOf(target.name) else current + target.name
}

fun navigateBackStack(current: List<String>): List<String> {
    require(current.isNotEmpty())
    return if (current.size > 1) current.dropLast(1) else current
}
