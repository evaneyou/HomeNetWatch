package com.homenetwatch.agent

import android.os.Build
import com.wireguard.crypto.KeyPair
import java.net.Inet4Address
import java.net.NetworkInterface
import org.json.JSONObject

/** The request bodies for check-in and poll. No Android classes here, so they can be tested on a PC. */
object Payloads {
    const val PLATFORM = "android"

    fun checkIn(seed: ByteArray, wgPublic: String, code: String, hostname: String, lan: String, nonce: String): JSONObject {
        val identity = Wire.publicKey(seed)
        // The same fields the Pi signs, in the same order, plus the platform. One tunnel, so no wireless key.
        val signed = listOf(identity, wgPublic, code, hostname, lan, PLATFORM)
        return JSONObject()
            .put("identity_public_key", identity)
            .put("wg_public_key", wgPublic)
            .put("platform", PLATFORM)
            .put("installer_code", code)
            .put("hostname", hostname)
            .put("lan_ip", lan)
            .put("nonce", nonce)
            .put("signature", Wire.sign(seed, Wire.signedMessage(Wire.CHECK_IN_PATH, nonce, signed)))
    }

    fun poll(seed: ByteArray, haveToken: Boolean, nonce: String): JSONObject {
        val identity = Wire.publicKey(seed)
        val flag = if (haveToken) "1" else "0"
        return JSONObject()
            .put("identity_public_key", identity)
            .put("have_token", haveToken)
            .put("nonce", nonce)
            .put("signature", Wire.sign(seed, Wire.signedMessage(Wire.POLL_PATH, nonce, listOf(identity, flag))))
    }
}

/** Registers this device with the server the way the Pi does: check in with the installer code, then wait for approval. */
class Enroller(private val store: Store) {

    /** One pass. Called every few seconds until the device is approved. */
    fun step() {
        if (store.enrolled) {
            store.state = "approved"
            return
        }
        if (store.serverUrl.isEmpty() || store.pins.isEmpty()) {
            store.state = "waiting_for_setup"
            return
        }
        try {
            val base = Wire.enrollmentBase(store.serverUrl)
            val pins = Wire.parsePins(store.pins)
            val seed = store.identitySeed()
            val wgPublic = wireGuardPublic()
            val code = store.ensureInstallerCode()
            val lan = localIpv4() ?: run {
                store.lastError = "No network address yet. Check that the device is connected."
                return
            }
            val check = Pinned.post(base + Wire.CHECK_IN_PATH, Payloads.checkIn(seed, wgPublic, code, hostname(), lan, nonce(base, seed, pins)), pins)
            store.state = "waiting_for_approval"
            store.lastError = if (check.optBoolean("conflict")) "More than one device used this code. Start over on the dashboard." else ""
            val poll = Pinned.post(base + Wire.POLL_PATH, Payloads.poll(seed, false, nonce(base, seed, pins)), pins)
            if (poll.optString("state") == "approved") remember(poll)
        } catch (failure: HttpFailure) {
            // 403 means the code is not registered on the dashboard yet. Keep waiting.
            store.state = "waiting_for_approval"
            store.lastError = if (failure.code == 403) "Waiting for the installer code to be registered on the dashboard." else failure.detail
        } catch (failure: Exception) {
            store.lastError = failure.message ?: failure.javaClass.simpleName
        }
    }

    private fun nonce(base: String, seed: ByteArray, pins: List<String>): String {
        val body = JSONObject().put("identity_public_key", Wire.publicKey(seed))
        return Pinned.post(base + Wire.NONCE_PATH, body, pins).getString("nonce")
    }

    private fun wireGuardPublic(): String {
        store.savedWireGuardPublic()?.let { return it }
        val pair = KeyPair()
        store.saveWireGuardKeys(pair.privateKey.toBase64(), pair.publicKey.toBase64())
        return pair.publicKey.toBase64()
    }

    private fun remember(poll: JSONObject) {
        val tunnels = poll.optJSONArray("tunnels")
        val first = if (tunnels != null && tunnels.length() > 0) tunnels.getJSONObject(0) else poll.optJSONObject("tunnel")
        poll.optString("token").takeIf { it.isNotEmpty() }?.let { store.token = it }
        poll.optString("name").takeIf { it.isNotEmpty() }?.let { store.name = it }
        if (first != null) {
            store.tunnelJson = first.toString()
            // The measurement traffic goes to the server's public address, outside the tunnel.
            val host = first.optString("endpoint").substringBefore(":")
            if (Regex("""\d+\.\d+\.\d+\.\d+""").matches(host) && !host.startsWith("10.7.")) store.measurementHost = host
        }
        store.state = "approved"
        store.lastError = ""
    }

    companion object {
        fun hostname(): String {
            val model = Build.MODEL.lowercase().replace(Regex("[^a-z0-9-]"), "").trim('-')
            return ("android-" + model).take(63).trim('-').ifEmpty { "android-agent" }
        }

        fun localIpv4(): String? = try {
            NetworkInterface.getNetworkInterfaces().toList()
                .filter { it.isUp && !it.isLoopback }
                .flatMap { it.inetAddresses.toList() }
                .filterIsInstance<Inet4Address>()
                .firstOrNull { it.isSiteLocalAddress }
                ?.hostAddress
        } catch (_: Exception) {
            null
        }
    }
}
