package com.homenetwatch.agent

import java.io.IOException
import java.net.URL
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager
import org.json.JSONObject

class HttpFailure(val code: Int, val detail: String) : IOException("HTTP $code: $detail")

/**
 * HTTPS to the server's enrollment port, trusting only a certificate whose public key matches a pin the owner typed.
 * The server uses its own certificate, so the normal certificate checks would refuse it.
 */
object Pinned {
    fun post(url: String, payload: JSONObject, pins: Collection<String>, timeoutMs: Int = 10_000): JSONObject {
        val target = URL(url)
        require(target.protocol == "https") { "Enrollment uses HTTPS" }
        val trust = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) = Unit
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {
                val leaf = chain.firstOrNull() ?: throw CertificateException("The server sent no certificate")
                if (Wire.spkiPin(leaf.publicKey.encoded) !in pins) throw CertificateException("Server key is not pinned")
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
        val context = SSLContext.getInstance("TLS")
        context.init(null, arrayOf(trust), null)
        val connection = target.openConnection() as HttpsURLConnection
        try {
            connection.sslSocketFactory = context.socketFactory
            connection.hostnameVerifier = HostnameVerifier { _, _ -> true }
            connection.connectTimeout = timeoutMs
            connection.readTimeout = timeoutMs
            connection.requestMethod = "POST"
            connection.doOutput = true
            connection.setRequestProperty("Content-Type", "application/json")
            connection.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
            val code = connection.responseCode
            val stream = if (code >= 400) connection.errorStream else connection.inputStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code >= 400) throw HttpFailure(code, detailOf(text))
            return if (text.isBlank()) JSONObject() else JSONObject(text)
        } finally {
            connection.disconnect()
        }
    }

    private fun detailOf(text: String): String = try {
        JSONObject(text).opt("detail")?.toString() ?: text
    } catch (_: Exception) {
        text
    }.take(200)
}
