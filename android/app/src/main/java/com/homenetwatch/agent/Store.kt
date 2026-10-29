package com.homenetwatch.agent

import android.content.Context
import android.content.SharedPreferences
import java.util.Base64

/** Everything the app remembers. It lives in the app's private storage, like /etc/monitor on the Pi. */
class Store(context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("agent", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString("server_url", "").orEmpty()
        set(value) = prefs.edit().putString("server_url", value).apply()

    /** Space-separated SHA-256 pins of the server's TLS key. */
    var pins: String
        get() = prefs.getString("pins", "").orEmpty()
        set(value) = prefs.edit().putString("pins", value).apply()

    var installerCode: String
        get() = prefs.getString("installer_code", "").orEmpty()
        set(value) = prefs.edit().putString("installer_code", value).apply()

    var token: String
        get() = prefs.getString("token", "").orEmpty()
        set(value) = prefs.edit().putString("token", value).apply()

    var name: String
        get() = prefs.getString("name", "").orEmpty()
        set(value) = prefs.edit().putString("name", value).apply()

    /** The tunnel the server issued, as the JSON it sent. */
    var tunnelJson: String
        get() = prefs.getString("tunnel", "").orEmpty()
        set(value) = prefs.edit().putString("tunnel", value).apply()

    var measurementHost: String
        get() = prefs.getString("measurement_host", "").orEmpty()
        set(value) = prefs.edit().putString("measurement_host", value).apply()

    /** What the screen shows: waiting_for_setup, waiting_for_approval, approved, or an error text in lastError. */
    var state: String
        get() = prefs.getString("state", "waiting_for_setup").orEmpty()
        set(value) = prefs.edit().putString("state", value).apply()

    var lastError: String
        get() = prefs.getString("last_error", "").orEmpty()
        set(value) = prefs.edit().putString("last_error", value).apply()

    val enrolled: Boolean get() = token.isNotEmpty()

    fun identitySeed(): ByteArray {
        val stored = prefs.getString("identity_seed", null)
        if (stored != null) return Base64.getDecoder().decode(stored)
        val seed = Wire.newSeed()
        prefs.edit().putString("identity_seed", Wire.b64(seed)).apply()
        return seed
    }

    fun ensureInstallerCode(): String {
        if (installerCode.isEmpty()) installerCode = Wire.newInstallerCode()
        return installerCode
    }

    /** Wipe the enrollment and start over, with a new identity and a new installer code. */
    fun reset() {
        prefs.edit().clear().apply()
    }

    fun savedWireGuardPrivate(): String? = prefs.getString("wg_private", null)
    fun saveWireGuardKeys(private: String, public: String) {
        prefs.edit().putString("wg_private", private).putString("wg_public", public).apply()
    }
    fun savedWireGuardPublic(): String? = prefs.getString("wg_public", null)
}
