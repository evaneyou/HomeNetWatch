package com.homenetwatch.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class UpdaterTest {
    private val sha = "ab".repeat(32)

    // The file CI publishes (android/publish-update.sh).
    private fun latest(apk: String = "https://github.com/o/r/releases/download/android-v0.5.1/agent.apk", hash: String = sha) =
        """{"versionCode":7,"versionName":"0.5.1","apk":"$apk","sha256":"$hash"}"""

    @Test
    fun readsTheReleaseCiPublishes() {
        val release = Updater.parse(latest(hash = sha.uppercase()))
        assertEquals(7, release.versionCode)
        assertEquals("0.5.1", release.versionName)
        assertEquals("https://github.com/o/r/releases/download/android-v0.5.1/agent.apk", release.apkUrl)
        assertEquals(sha, release.sha256)
    }

    @Test
    fun onlyAHigherVersionCodeIsAnUpdate() {
        val release = Updater.parse(latest())
        assertTrue(Updater.isNewer(release, 6))
        assertFalse(Updater.isNewer(release, 7))
        assertFalse(Updater.isNewer(release, 8))
    }

    @Test
    fun refusesAPlainHttpLinkOrAMissingHash() {
        for (text in listOf(latest(apk = "http://example.com/agent.apk"), latest(hash = "abc"), """{"versionCode":7}""")) {
            try {
                Updater.parse(text)
                fail("accepted $text")
            } catch (_: Exception) {
            }
        }
    }
}
