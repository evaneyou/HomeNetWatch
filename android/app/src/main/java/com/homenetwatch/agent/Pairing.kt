package com.homenetwatch.agent

import java.net.URLDecoder
import java.net.URLEncoder

/**
 * One-scan onboarding. The TV shows a QR code that opens the dashboard's onboarding page on a phone, with this
 * device's installer code and where to reach it filled in. After the owner registers the code there, the dashboard
 * sends the phone back to this device's /receive address with the setup key and the name, and then on to the
 * dashboard to approve. Nothing is typed or copied.
 */
object Pairing {
    /** The address the QR code holds. */
    fun url(dashboard: String, code: String, deviceBase: String, token: String): String =
        dashboard.trimEnd('/') + "/settings/onboarding?" + listOf(
            "pair" to "android",
            "code" to code,
            "device" to deviceBase,
            "t" to token,
        ).joinToString("&") { (key, value) -> "$key=${encode(value)}" }

    /** Where the phone reaches this device's setup page, for example http://192.168.1.50:8080. */
    fun deviceBase(address: String, port: Int): String = "http://$address:$port"

    fun query(target: String): Map<String, String> =
        target.substringAfter('?', "").split("&").mapNotNull {
            val key = it.substringBefore("=", "")
            if (key.isEmpty()) null else key to URLDecoder.decode(it.substringAfter("=", ""), "UTF-8")
        }.toMap()

    /** Only send the phone back to the dashboard this app belongs to, never to an address from the link. */
    fun safeReturn(back: String?, dashboard: String): String? {
        val base = dashboard.trimEnd('/') + "/"
        return back?.takeIf { it.startsWith(base) && '\r' !in it && '\n' !in it }
    }

    private fun encode(value: String): String = URLEncoder.encode(value, "UTF-8").replace("+", "%20")
}
