package com.homenetwatch.agent

import android.content.Context
import android.net.VpnService
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import org.json.JSONObject

/**
 * The WireGuard tunnel to the server, built from what the server sent at approval. It only routes the server's
 * tunnel address, so health reports, the voice stream's control channel and the speed test go through it, while
 * the voice packets themselves go to the server's public address over Wi-Fi, outside the tunnel, like the Pi's.
 */
class TunnelManager(context: Context, private val store: Store) {
    private val app = context.applicationContext
    private val backend by lazy { GoBackend(app) }
    private val tunnel = object : Tunnel {
        override fun getName() = "homenetwatch"
        override fun onStateChange(newState: Tunnel.State) {}
    }

    /** True until the owner has allowed the app to make a VPN connection, which Android asks once on screen. */
    fun needsConsent(): Boolean = VpnService.prepare(app) != null

    fun isUp(): Boolean = try {
        backend.getState(tunnel) == Tunnel.State.UP
    } catch (_: Exception) {
        false
    }

    /** Brings the tunnel up if it is not. Returns why it cannot, or null when it is up. */
    fun ensureUp(): String? {
        if (store.tunnelJson.isEmpty()) return "The server has not sent a tunnel yet."
        if (needsConsent()) return "Allow the VPN connection: open HomeNetWatch on the TV and choose OK."
        if (isUp()) return null
        val private = store.savedWireGuardPrivate() ?: return "This device lost its tunnel key. Press Menu on the TV to set it up again."
        val text = configText(JSONObject(store.tunnelJson), private)
        backend.setState(tunnel, Tunnel.State.UP, Config.parse(text.byteInputStream()))
        return null
    }

    fun down() {
        try {
            backend.setState(tunnel, Tunnel.State.DOWN, null)
        } catch (_: Exception) {
        }
    }

    companion object {
        /** The wg-quick text for the tunnel the server issued. */
        fun configText(tunnel: JSONObject, privateKey: String): String {
            val address = tunnel.getString("address")
            val preshared = tunnel.optString("preshared_key").takeIf { it.isNotEmpty() && it != "null" }
            return buildString {
                append("[Interface]\n")
                append("PrivateKey = $privateKey\n")
                append("Address = $address/32\n")
                append("\n[Peer]\n")
                append("PublicKey = ${tunnel.getString("server_public_key")}\n")
                if (preshared != null) append("PresharedKey = $preshared\n")
                append("Endpoint = ${tunnel.getString("endpoint")}\n")
                append("AllowedIPs = ${tunnel.optString("allowed_ips").ifEmpty { "${Api.SERVER_IP}/32" }}\n")
                append("PersistentKeepalive = ${tunnel.optInt("persistent_keepalive", 25)}\n")
            }
        }
    }
}
