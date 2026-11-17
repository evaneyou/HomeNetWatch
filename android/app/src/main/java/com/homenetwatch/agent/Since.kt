package com.homenetwatch.agent

import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * How long the agent has been in a state. The TV's wall clock is often wrong at boot and then jumps when the
 * time syncs, so a duration is the gap between two [android.os.SystemClock.elapsedRealtime] readings. The
 * "since" time is that duration counted back from the wall clock as it is now, so it follows a correction.
 */
object Since {
    /** Milliseconds from a mark to now. A mark of 0, or a clock that stepped backward, is no time at all. */
    fun durationMs(markElapsed: Long, nowElapsed: Long): Long =
        if (markElapsed <= 0L) 0L else (nowElapsed - markElapsed).coerceAtLeast(0L)

    /** "3 h 12 m", "4 m 05 s", "1 d 2 h": short enough to read across a room. */
    fun span(durationMs: Long): String {
        val seconds = durationMs.coerceAtLeast(0L) / 1000
        val hours = seconds / 3600
        val minutes = (seconds % 3600) / 60
        return when {
            hours >= 24 -> "${hours / 24} d ${hours % 24} h"
            hours > 0 -> "$hours h $minutes m"
            minutes > 0 -> "$minutes m ${"%02d".format(seconds % 60)} s"
            else -> "$seconds s"
        }
    }

    /** "9 h 2 m (since 11:24 PM)", or with the date when that moment was another day. */
    fun running(
        markElapsed: Long,
        nowElapsed: Long,
        nowWall: Long,
        zone: ZoneId,
        locale: Locale = Locale.getDefault(),
    ): String {
        val duration = durationMs(markElapsed, nowElapsed)
        return "${span(duration)} (since ${clock(nowWall - duration, nowWall, zone, locale)})"
    }

    /** Time of day, and the date when the moment is not today on this clock. */
    fun clock(thenWall: Long, nowWall: Long, zone: ZoneId, locale: Locale = Locale.getDefault()): String {
        val then = Instant.ofEpochMilli(thenWall).atZone(zone)
        val now = Instant.ofEpochMilli(nowWall).atZone(zone)
        val pattern = when {
            then.toLocalDate() == now.toLocalDate() -> "h:mm a"
            then.year != now.year -> "MMM d, yyyy, h:mm a"
            else -> "MMM d, h:mm a"
        }
        return then.format(DateTimeFormatter.ofPattern(pattern, locale))
    }
}
