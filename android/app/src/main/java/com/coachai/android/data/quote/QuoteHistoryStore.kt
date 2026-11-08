package com.coachai.android.data.quote

import android.content.Context
import androidx.core.content.edit
import java.time.LocalDate

data class QuoteHistory(
    val selectedDate: LocalDate? = null,
    val selectedQuoteId: String? = null,
    val recentQuoteIds: List<String> = emptyList(),
)

interface QuoteHistoryStore {
    fun read(): QuoteHistory
    fun write(history: QuoteHistory)
}

class SharedPreferencesQuoteHistoryStore(context: Context) : QuoteHistoryStore {
    private val preferences = context.getSharedPreferences("daily_quote_history", Context.MODE_PRIVATE)

    override fun read(): QuoteHistory {
        val date = preferences.getString(KEY_DATE, null)?.let {
            runCatching { LocalDate.parse(it) }.getOrNull()
        }
        return QuoteHistory(
            selectedDate = date,
            selectedQuoteId = preferences.getString(KEY_QUOTE_ID, null),
            recentQuoteIds = preferences.getString(KEY_RECENT_IDS, "")
                .orEmpty()
                .split(',')
                .filter(String::isNotBlank),
        )
    }

    override fun write(history: QuoteHistory) {
        preferences.edit {
            putString(KEY_DATE, history.selectedDate?.toString())
            putString(KEY_QUOTE_ID, history.selectedQuoteId)
            putString(KEY_RECENT_IDS, history.recentQuoteIds.joinToString(","))
        }
    }

    private companion object {
        const val KEY_DATE = "selected_date"
        const val KEY_QUOTE_ID = "selected_quote_id"
        const val KEY_RECENT_IDS = "recent_quote_ids"
    }
}
