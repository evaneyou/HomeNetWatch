package com.homenetwatch.agent

import org.junit.Assert.assertEquals
import org.junit.Test
import java.time.Instant
import java.time.ZoneOffset
import java.util.Locale

class SinceTest {
    private val zone = ZoneOffset.UTC
    private val locale = Locale.US

    @Test
    fun aWallClockJumpDoesNotCountAsUptime() {
        // The agent has been up 8 h 6 m. The TV clock then jumps forward by about 290 days.
        val mark = 5_000L
        val elapsed = mark + (8 * 3600 + 6 * 60) * 1000L
        val wall = Instant.parse("2026-11-17T12:30:00Z").toEpochMilli()
        assertEquals("8 h 6 m (since 4:24 AM)", Since.running(mark, elapsed, wall, zone, locale))
        assertEquals("8 h 6 m", Since.span(Since.durationMs(mark, elapsed)))
    }

    @Test
    fun aLongRunShowsTheDateItStarted() {
        val mark = 1L
        val elapsed = mark + (290L * 24 + 8) * 3600 * 1000
        val wall = Instant.parse("2026-11-17T12:24:00Z").toEpochMilli()
        assertEquals("290 d 8 h (since Jan 31, 4:24 AM)", Since.running(mark, elapsed, wall, zone, locale))
    }

    @Test
    fun todayShowsOnlyTheTime() {
        val mark = 10_000L
        val elapsed = mark + 125_000L
        val wall = Instant.parse("2026-11-17T16:24:00Z").toEpochMilli()
        assertEquals("2 m 05 s (since 4:21 PM)", Since.running(mark, elapsed, wall, zone, locale))
    }

    @Test
    fun aMissingOrBackwardMarkIsNoTime() {
        assertEquals(0L, Since.durationMs(0L, 50_000L))
        assertEquals(0L, Since.durationMs(80_000L, 10_000L))
        assertEquals("0 s", Since.span(0L))
    }
}
