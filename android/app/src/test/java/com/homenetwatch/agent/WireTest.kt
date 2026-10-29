package com.homenetwatch.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class WireTest {
    // Computed with the server's own Ed25519 code (cryptography) for the seed 0, 1, 2 ... 31.
    private val seed = ByteArray(32) { it.toByte() }
    private val publicKey = "A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg="
    private val signature = "NUZVmkkXmXkfTtXqDOu4OpGnwv0QewuI6n4vb+7bXcBcwc+GVfoL9u7S/d7o8nvCCgQxr8/i6rWxQwrz4UAOAg=="

    @Test
    fun publicKeyMatchesTheServersLibrary() {
        assertEquals(publicKey, Wire.publicKey(seed))
    }

    @Test
    fun checkInSignatureMatchesWhatTheServerVerifies() {
        val message = Wire.signedMessage(
            Wire.CHECK_IN_PATH,
            "nonce-value",
            listOf(publicKey, "WGKEY", "CODE-1234-ABCD", "android-shield", "192.168.1.50", "android"),
        )
        // Ed25519 is deterministic, so the same key and message give the same signature as the server's library.
        assertEquals(signature, Wire.sign(seed, message))
    }

    @Test
    fun checkInBodyListsTheSignedFieldsInServerOrder() {
        val body = Payloads.checkIn(seed, "WGKEY", "CODE-1234-ABCD", "android-shield", "192.168.1.50", "nonce-value")
        assertEquals(signature, body.getString("signature"))
        assertEquals("android", body.getString("platform"))
        assertTrue(!body.has("wg_wireless_public_key"))
    }

    @Test
    fun signedFieldsCannotHoldNewlines() {
        try {
            Wire.signedMessage(Wire.POLL_PATH, "n", listOf("a\nb"))
            fail("expected a refusal")
        } catch (_: IllegalArgumentException) {
        }
    }

    @Test
    fun installerCodesLookLikeThePis() {
        repeat(50) {
            val code = Wire.newInstallerCode()
            assertTrue(Regex("[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}").matches(code))
        }
    }

    @Test
    fun pinsAcceptThePrefixAndRejectBadValues() {
        val pin = Wire.b64(ByteArray(32) { 7 })
        assertEquals(listOf(pin), Wire.parsePins("sha256/$pin"))
        assertEquals(listOf(pin, pin), Wire.parsePins("$pin, sha256/$pin"))
        try {
            Wire.parsePins("sha256/short")
            fail("expected a refusal")
        } catch (_: IllegalArgumentException) {
        }
    }

    @Test
    fun enrollmentUsesHttpsOnPort8848() {
        assertEquals("https://live.example.com:8848", Wire.enrollmentBase("https://live.example.com/some/path"))
        try {
            Wire.enrollmentBase("http://live.example.com")
            fail("expected a refusal")
        } catch (_: IllegalArgumentException) {
        }
    }
}
