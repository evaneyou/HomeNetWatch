package com.homenetwatch.agent

import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer

/** What the server checks on every enrollment request. Mirrors monitor/enrollment.py and server/app/enrollment.py. */
object Wire {
    const val CONTEXT = "hns-agent-v1"
    const val NONCE_PATH = "/api/v1/agents/nonce"
    const val CHECK_IN_PATH = "/api/v1/agents/check-in"
    const val POLL_PATH = "/api/v1/agents/poll"
    const val ENROLL_PORT = 8848

    // Installer codes use Crockford base32: no I, L, O or U.
    private const val CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    private const val CODE_LENGTH = 12

    fun signedMessage(path: String, nonce: String, fields: List<String>): ByteArray {
        val parts = listOf(CONTEXT, path, nonce) + fields
        require(parts.none { "\n" in it }) { "Signed fields cannot contain newlines" }
        return parts.joinToString("\n").toByteArray(Charsets.UTF_8)
    }

    fun b64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)

    fun newSeed(): ByteArray = ByteArray(32).also { SecureRandom().nextBytes(it) }

    fun publicKey(seed: ByteArray): String = b64(Ed25519PrivateKeyParameters(seed, 0).generatePublicKey().encoded)

    fun sign(seed: ByteArray, message: ByteArray): String {
        val signer = Ed25519Signer()
        signer.init(true, Ed25519PrivateKeyParameters(seed, 0))
        signer.update(message, 0, message.size)
        return b64(signer.generateSignature())
    }

    /** Twelve letters and numbers shown as K7QF-2MXD-9PLA. */
    fun newInstallerCode(): String {
        val random = SecureRandom()
        val raw = (1..CODE_LENGTH).map { CROCKFORD[random.nextInt(CROCKFORD.length)] }.joinToString("")
        return raw.chunked(4).joinToString("-")
    }

    fun spkiPin(spkiDer: ByteArray): String = b64(MessageDigest.getInstance("SHA-256").digest(spkiDer))

    /** Accepts `sha256/<base64>` or the bare value, one or several, split by spaces or commas. */
    fun parsePins(raw: String): List<String> {
        val pins = raw.trim().split(Regex("[\\s,]+")).filter { it.isNotEmpty() }.map { it.removePrefix("sha256/") }
        require(pins.isNotEmpty()) { "Enter the server TLS key pin" }
        for (pin in pins) {
            val digest = try {
                Base64.getDecoder().decode(pin)
            } catch (_: IllegalArgumentException) {
                throw IllegalArgumentException("TLS pins look like sha256/ and a SHA-256 value")
            }
            require(digest.size == 32) { "TLS pins are SHA-256" }
        }
        return pins
    }

    /** The server address and the TLS key pins, as the dashboard hands them over in one setup key. */
    class SetupKey(val serverUrl: String, val pins: List<String>)

    fun parseSetupKey(raw: String): SetupKey {
        val text = raw.trim().replace(Regex("\\s+"), "")
        require(text.startsWith("hnw1.")) { "That is not a setup key. Copy it from the dashboard's Remote Agent Onboarding page." }
        val json = try {
            org.json.JSONObject(String(Base64.getUrlDecoder().decode(text.removePrefix("hnw1.")), Charsets.UTF_8))
        } catch (_: Exception) {
            throw IllegalArgumentException("The setup key is damaged. Copy it again from the dashboard.")
        }
        val url = json.optString("u")
        enrollmentBase(url)
        val list = json.optJSONArray("p") ?: throw IllegalArgumentException("The setup key has no server key in it.")
        val pins = parsePins((0 until list.length()).joinToString(" ") { list.getString(it) })
        return SetupKey(url, pins)
    }

    /** The enrollment address, which is the server's HTTPS port 8848 on the host the owner typed. */
    fun enrollmentBase(url: String): String {
        val uri = try {
            java.net.URI(url.trim())
        } catch (_: Exception) {
            throw IllegalArgumentException("Enrollment uses HTTPS")
        }
        val host = uri.host
        require(uri.scheme == "https" && !host.isNullOrEmpty()) { "Enrollment uses HTTPS" }
        val shown = if (":" in host!!) "[$host]" else host
        return "https://$shown:$ENROLL_PORT"
    }
}
