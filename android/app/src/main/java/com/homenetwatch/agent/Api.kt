package com.homenetwatch.agent

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * Calls to the server after approval. They go over the WireGuard tunnel to the server's tunnel address, as the
 * Pi's do, with the agent's token and its name. The tunnel itself is the encryption, so this is plain HTTP.
 */
object Api {
    /** The server's address inside the tunnel. An Android agent has one tunnel, in the wired slot. */
    const val SERVER_IP = "10.7.0.1"
    const val BASE = "http://$SERVER_IP:8847"

    fun get(store: Store, path: String, timeoutMs: Int = 10_000): JSONObject = call(store, "GET", path, null, timeoutMs)

    fun post(store: Store, path: String, body: JSONObject, timeoutMs: Int = 10_000): JSONObject =
        call(store, "POST", path, body, timeoutMs)

    /** The name header, encoded as the Pi's urllib.parse.quote does it: spaces become %20, not +. */
    fun nameHeader(name: String): String = URLEncoder.encode(name, "UTF-8").replace("+", "%20")

    private fun call(store: Store, method: String, path: String, body: JSONObject?, timeoutMs: Int): JSONObject {
        val connection = URL(BASE + path).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.setRequestProperty("Content-Type", "application/json")
            connection.setRequestProperty("Authorization", "Bearer ${store.token}")
            connection.setRequestProperty("X-Monitor-Name", nameHeader(store.name))
            if (body != null) {
                val bytes = body.toString().toByteArray(Charsets.UTF_8)
                connection.doOutput = true
                connection.setFixedLengthStreamingMode(bytes.size)
                connection.outputStream.use { it.write(bytes) }
            }
            val code = connection.responseCode
            val stream = if (code in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            if (code !in 200..299) {
                val detail = try {
                    JSONObject(text).optString("detail").ifEmpty { "HTTP $code" }
                } catch (_: Exception) {
                    "HTTP $code"
                }
                throw HttpFailure(code, detail)
            }
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }
}
