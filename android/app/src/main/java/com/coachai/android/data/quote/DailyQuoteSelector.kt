package com.coachai.android.data.quote

import java.nio.ByteBuffer
import java.security.MessageDigest
import java.time.LocalDate
import kotlin.math.absoluteValue

class DailyQuoteSelector(
    private val quotes: List<DailyQuote>,
    private val historyStore: QuoteHistoryStore,
    private val recentWindow: Int = 21,
) {
    init {
        require(quotes.isNotEmpty()) { "The daily quote library cannot be empty." }
        require(quotes.map { it.id }.distinct().size == quotes.size) { "Quote IDs must be unique." }
    }

    fun select(date: LocalDate): DailyQuote {
        val history = historyStore.read()
        if (history.selectedDate == date) {
            quotes.firstOrNull { it.id == history.selectedQuoteId }?.let { return it }
        }

        val categories = QuoteCategory.entries
        val preferredCategory = categories[Math.floorMod(date.toEpochDay(), categories.size.toLong()).toInt()]
        val recent = history.recentQuoteIds.takeLast(recentWindow).toSet()
        val preferred = quotes.filter { it.category == preferredCategory && it.id !in recent }
        val nonRecent = quotes.filter { it.id !in recent }
        val candidates = preferred.ifEmpty { nonRecent }.ifEmpty { quotes }
        val selected = candidates[stableIndex(date, preferredCategory, candidates.size)]
        val updatedRecent = (history.recentQuoteIds + selected.id).takeLast(recentWindow)
        historyStore.write(
            QuoteHistory(
                selectedDate = date,
                selectedQuoteId = selected.id,
                recentQuoteIds = updatedRecent,
            ),
        )
        return selected
    }

    private fun stableIndex(date: LocalDate, category: QuoteCategory, size: Int): Int {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest("coachai|$date|${category.name}".toByteArray())
        return (ByteBuffer.wrap(digest.copyOfRange(0, 4)).int.toLong().absoluteValue % size).toInt()
    }
}

