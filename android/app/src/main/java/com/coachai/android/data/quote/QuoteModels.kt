package com.coachai.android.data.quote

enum class QuoteCategory(val label: String) {
    GETTING_STARTED("Getting started"),
    FOCUS("Focus"),
    CONSISTENCY("Consistency"),
    RESILIENCE("Resilience"),
    LEARNING("Learning"),
    REST_BALANCE("Rest and balance"),
    PLANNING("Planning"),
    PATIENCE("Long-term progress"),
}

data class DailyQuote(
    val id: String,
    val text: String,
    val category: QuoteCategory,
    val attribution: String? = null,
)

