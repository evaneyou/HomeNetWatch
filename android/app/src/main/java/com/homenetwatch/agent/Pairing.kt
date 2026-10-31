package com.homenetwatch.agent

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.net.URLEncoder

/**
 * One-scan onboarding. The TV shows a QR code that opens the dashboard's onboarding page on a phone, with this
 * device's installer code and a pairing token. When the owner registers the code there, the server keeps the token,
 * and this device, asking every few seconds, collects its setup key and name from the server. The phone never has
 * to reach the device, so any browser and any network works.
 */
object Pairing {
    /** The address the QR code holds. */
    fun url(dashboard: String, code: String, token: String): String =
        dashboard.trimEnd('/') + "/settings/onboarding?" + listOf(
            "pair" to "android",
            "code" to code,
            "t" to token,
        ).joinToString("&") { (key, value) -> "$key=${encode(value)}" }

    /**
     * Asks the dashboard's server whether this device has been registered, and saves the setup key and name if so.
     * Plain HTTPS with the site's ordinary certificate: the enrollment port's pins are what the setup key brings.
     * Returns true once the key is saved.
     */
    fun claim(store: Store, dashboard: String): Boolean {
        val connection = URL(dashboard.trimEnd('/') + "/api/v1/agents/pairing").openConnection() as HttpURLConnection
        val answer = try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 10_000
            connection.readTimeout = 10_000
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(JSONObject().put("token", store.pairingToken()).toString().toByteArray()) }
            if (connection.responseCode != 200) return false
            JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
        } finally {
            connection.disconnect()
        }
        return apply(store, answer, dashboard)
    }

    /** Saves what the server answered. A key for any other server than this app's dashboard is refused. */
    fun apply(store: Store, answer: JSONObject, dashboard: String): Boolean {
        if (answer.optString("state") != "registered" || store.enrolled) return false
        val raw = answer.optString("setup_key")
        val key = Wire.parseSetupKey(raw)
        require(sameHost(key.serverUrl, dashboard)) { "That setup key is for another server." }
        answer.optString("name").takeIf { it.isNotBlank() }?.let { Setup.name(store, it) }
        Setup.key(store, raw)
        return true
    }

    private fun sameHost(a: String, b: String): Boolean =
        try {
            URI(a).host.equals(URI(b).host, ignoreCase = true)
        } catch (_: Exception) {
            false
        }

    private fun encode(value: String): String = URLEncoder.encode(value, "UTF-8").replace("+", "%20")
}
