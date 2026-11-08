package com.coachai.android.data.quote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

class DailyQuoteSelectorTest {
    @Test
    fun libraryHasThirtyQuotesInEveryCategory() {
        assertEquals(240, QuoteLibrary.all.size)
        assertEquals(240, QuoteLibrary.all.map { it.id }.distinct().size)
        QuoteCategory.entries.forEach { category ->
            assertEquals(category.name, 30, QuoteLibrary.all.count { it.category == category })
        }
    }

    @Test
    fun editorialEntriesDoNotClaimUnverifiedAttribution() {
        assertTrue(QuoteLibrary.all.all { it.attribution == null })
        assertTrue(QuoteLibrary.all.all { it.text.isNotBlank() })
    }

    @Test
    fun selectionIsStableThroughoutTheSameLocalDate() {
        val store = MemoryQuoteHistoryStore()
        val selector = DailyQuoteSelector(QuoteLibrary.all, store)
        val date = LocalDate.of(2026, 10, 15)

        val first = selector.select(date)
        val second = selector.select(date)

        assertEquals(first, second)
        assertEquals(date, store.history.selectedDate)
    }

    @Test
    fun subsequentDaysRotateAndAvoidTheRecentWindow() {
        val store = MemoryQuoteHistoryStore()
        val selector = DailyQuoteSelector(QuoteLibrary.all, store, recentWindow = 21)
        val start = LocalDate.of(2026, 10, 1)
        val selected = (0L until 22L).map { selector.select(start.plusDays(it)).id }

        assertEquals(22, selected.distinct().size)
        assertNotEquals(selected.first(), selected.last())
    }

    @Test
    fun categoriesAreBalancedByDay() {
        val store = MemoryQuoteHistoryStore()
        val selector = DailyQuoteSelector(QuoteLibrary.all, store)
        val start = LocalDate.ofEpochDay(0)
        val categories = (0L until QuoteCategory.entries.size.toLong())
            .map { selector.select(start.plusDays(it)).category }

        assertEquals(QuoteCategory.entries.toSet(), categories.toSet())
    }

    private class MemoryQuoteHistoryStore : QuoteHistoryStore {
        var history = QuoteHistory()
        override fun read(): QuoteHistory = history
        override fun write(history: QuoteHistory) {
            this.history = history
        }
    }
}

