package com.homenetwatch.agent

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.security.SecureRandom

/**
 * The setup page, like the one the Pi serves on its own address. Typing on a TV remote is slow, so the owner opens
 * this page from a phone on the same network, names the device and pastes the setup key there.
 *
 * Anyone on the network can reach this page, so it only accepts changes before the device is approved (starting over
 * takes the Menu button on the TV remote), and each form carries a token from the page itself, so another web page
 * open on a phone cannot post to it.
 */
class SetupServer(private val store: Store, private val port: Int = PORT) {
    @Volatile private var socket: ServerSocket? = null
    @Volatile private var running = false
    private val formToken = Wire.b64(ByteArray(16).also { SecureRandom().nextBytes(it) })

    fun start() {
        if (running) return
        running = true
        Thread({ serve() }, "setup-page").apply { isDaemon = true }.start()
    }

    fun stop() {
        running = false
        try {
            socket?.close()
        } catch (_: Exception) {
        }
    }

    private fun serve() {
        try {
            val server = ServerSocket(port, 4, InetAddress.getByName("0.0.0.0"))
            socket = server
            while (running) {
                val client = server.accept()
                Thread({ handle(client) }, "setup-request").apply { isDaemon = true }.start()
            }
        } catch (_: Exception) {
            // The socket was closed on stop, or the port is busy. The screen still shows the installer code.
        }
    }

    /**
     * One browser request. Phones open spare connections that never send anything and drop them, so a timeout or a
     * reset is normal here. Any failure must stay inside this thread: an uncaught exception in an Android app kills
     * the whole process, and the setup page and the agent with it.
     */
    private fun handle(client: Socket) {
        try {
            handleRequest(client)
        } catch (_: Exception) {
        }
    }

    private fun handleRequest(client: Socket) {
        client.use {
            it.soTimeout = 5000
            val reader = BufferedReader(InputStreamReader(it.getInputStream(), Charsets.UTF_8))
            val request = reader.readLine() ?: return
            var length = 0
            while (true) {
                val line = reader.readLine() ?: break
                if (line.isEmpty()) break
                if (line.startsWith("content-length:", ignoreCase = true)) length = line.substringAfter(":").trim().toIntOrNull() ?: 0
            }
            val method = request.substringBefore(' ')
            val path = request.substringAfter(' ').substringBefore(' ')
            val out = it.getOutputStream()
            if (method == "POST" && (path == "/save-name" || path == "/save-key") && length in 1..4096) {
                val chars = CharArray(length)
                var read = 0
                while (read < length) {
                    val n = reader.read(chars, read, length - read)
                    if (n < 0) break
                    read += n
                }
                respond(out, page(save(path, String(chars, 0, read))))
            } else if (method == "GET" && (path == "/" || path.startsWith("/?"))) {
                respond(out, page(""))
            } else {
                respond(out, "Not found", 404)
            }
        }
    }

    private fun save(path: String, body: String): String {
        val form = body.split("&").mapNotNull {
            val key = it.substringBefore("=", "")
            if (key.isEmpty()) null else key to URLDecoder.decode(it.substringAfter("=", ""), "UTF-8")
        }.toMap()
        if (form["t"] != formToken) return "Not saved: this page is out of date. Reload it and try again."
        if (store.enrolled) return "Not saved: this device is already approved. To set it up again, press Menu on the TV remote."
        return try {
            if (path == "/save-name") saveName(form["device_name"].orEmpty()) else saveKey(form["setup_key"].orEmpty())
        } catch (failure: IllegalArgumentException) {
            "Not saved: " + (failure.message ?: "check what you entered")
        }
    }

    private fun saveName(raw: String): String {
        val name = raw.trim().replace(Regex("\\s+"), " ")
        require(name.isNotEmpty()) { "Give this device a name, for example Living Room Shield." }
        require(name.length <= 128) { "The name can be up to 128 characters." }
        store.deviceName = name
        return "Saved the name: $name."
    }

    private fun saveKey(raw: String): String {
        val key = Wire.parseSetupKey(raw)
        store.serverUrl = key.serverUrl
        store.pins = key.pins.joinToString(" ")
        store.state = "waiting_for_approval"
        store.lastError = ""
        return if (store.deviceName.isEmpty()) "Saved the setup key. Now name the device in step 1 so it can check in."
        else "Saved the setup key. Waiting for the dashboard to approve this device."
    }

    private fun page(message: String): String {
        val code = store.ensureInstallerCode()
        val keyStatus = if (store.serverUrl.isEmpty()) "" else "<p class=note>Setup key saved for ${escape(store.serverUrl)}.</p>"
        val note = if (message.isEmpty()) "" else "<p class=note>${escape(message)}</p>"
        val status = when (store.state) {
            "approved" -> "Approved. This device is on the dashboard as ${escape(store.name)}."
            "waiting_for_approval" -> "Waiting for approval on the dashboard."
            else -> "Not set up yet."
        }
        val token = "<input type=hidden name=t value=\"$formToken\">"
        val steps = if (store.enrolled) "<p><small>To set this device up again, press the Menu button on the TV remote.</small></p>" else """
<p>Installer code<br><span class=code>${escape(code)}</span></p>
<h2>1. Name this device</h2>
<p><small>This is what it is called on the dashboard.</small></p>
<form method="post" action="/save-name">$token
<input name="device_name" value="${escape(store.deviceName)}" placeholder="Living Room Shield" maxlength="128" required>
<button type="submit">Save name</button></form>
<h2>2. Register the installer code</h2>
<p><small>In the dashboard, open <b>Remote Agent Onboarding</b>, enter the installer code above exactly as shown and register it.</small></p>
<h2>3. Paste the setup key</h2>
<p><small>The dashboard shows the <b>Setup key</b> on the registered code's card. Copy it and paste it here. Then choose <b>Approve</b> on the dashboard.</small></p>
$keyStatus
<form method="post" action="/save-key">$token
<textarea name="setup_key" rows="4" placeholder="hnw1...." autocapitalize="off" spellcheck="false" required></textarea>
<button type="submit">Save setup key</button></form>"""
        return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HomeNetWatch Android agent</title>
<style>body{font:16px system-ui;margin:0;padding:20px;background:#0f1424;color:#e8ecf8;max-width:560px}
h1{font-size:22px}h2{font-size:17px;margin:22px 0 4px}label{display:block;margin:14px 0 6px;font-weight:700}input,textarea{width:100%;box-sizing:border-box;font-family:ui-monospace,monospace;padding:12px;border-radius:10px;border:1px solid #2a3556;background:#161d2f;color:#e8ecf8;font-size:16px}
button{margin-top:18px;padding:12px 20px;border:0;border-radius:10px;background:#4fd1b5;color:#06201b;font-weight:800;font-size:16px}
.code{font:700 26px ui-monospace,monospace;letter-spacing:2px;background:#161d2f;padding:12px;border-radius:10px;display:inline-block}
.note{background:#1d2a2b;padding:10px;border-radius:10px}small{color:#9aa6c4}</style></head><body>
<h1>HomeNetWatch Android agent</h1>
<p>This device: <b>${escape(Enroller.deviceLabel())}</b></p>
<p>$status</p>$note
$steps</body></html>"""
    }

    private fun escape(text: String): String =
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")

    private fun respond(out: java.io.OutputStream, body: String, status: Int = 200) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        val head = "HTTP/1.1 $status ${if (status == 200) "OK" else "Not Found"}\r\nContent-Type: text/html; charset=utf-8\r\n" +
            "Content-Length: ${bytes.size}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
        out.write(head.toByteArray(Charsets.US_ASCII))
        out.write(bytes)
        out.flush()
    }

    companion object {
        const val PORT = 8080
    }
}
