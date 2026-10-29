package com.homenetwatch.agent

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder

/**
 * The setup page, like the one the Pi serves on its own address. Typing on a TV remote is slow, so the owner opens
 * this page from a phone on the same network and enters the server address and its TLS key pin there.
 */
class SetupServer(private val store: Store, private val port: Int = PORT) {
    @Volatile private var socket: ServerSocket? = null
    @Volatile private var running = false

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

    private fun handle(client: Socket) {
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
            if (method == "POST" && path == "/save" && length in 1..4096) {
                val chars = CharArray(length)
                var read = 0
                while (read < length) {
                    val n = reader.read(chars, read, length - read)
                    if (n < 0) break
                    read += n
                }
                respond(out, page(save(String(chars, 0, read))))
            } else if (method == "GET" && (path == "/" || path.startsWith("/?"))) {
                respond(out, page(""))
            } else {
                respond(out, "Not found", 404)
            }
        }
    }

    private fun save(body: String): String {
        val form = body.split("&").mapNotNull {
            val key = it.substringBefore("=", "")
            if (key.isEmpty()) null else key to URLDecoder.decode(it.substringAfter("=", ""), "UTF-8")
        }.toMap()
        return try {
            val url = form["server_url"].orEmpty().trim()
            Wire.enrollmentBase(url)
            val pins = Wire.parsePins(form["pins"].orEmpty())
            store.serverUrl = url
            store.pins = pins.joinToString(" ")
            store.state = "waiting_for_approval"
            store.lastError = ""
            "Saved. Now enter the installer code shown on the screen into the dashboard's Enrollment page."
        } catch (failure: IllegalArgumentException) {
            "Not saved: " + (failure.message ?: "check the values")
        }
    }

    private fun page(message: String): String {
        val code = store.ensureInstallerCode()
        val note = if (message.isEmpty()) "" else "<p class=note>${escape(message)}</p>"
        val status = when (store.state) {
            "approved" -> "Approved. This device is on the dashboard as ${escape(store.name)}."
            "waiting_for_approval" -> "Waiting for approval on the dashboard."
            else -> "Not set up yet."
        }
        return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>HomeNetWatch Android agent</title>
<style>body{font:16px system-ui;margin:0;padding:20px;background:#0f1424;color:#e8ecf8;max-width:560px}
h1{font-size:22px}label{display:block;margin:14px 0 6px;font-weight:700}input{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #2a3556;background:#161d2f;color:#e8ecf8;font-size:16px}
button{margin-top:18px;padding:12px 20px;border:0;border-radius:10px;background:#4fd1b5;color:#06201b;font-weight:800;font-size:16px}
.code{font:700 26px ui-monospace,monospace;letter-spacing:2px;background:#161d2f;padding:12px;border-radius:10px;display:inline-block}
.note{background:#1d2a2b;padding:10px;border-radius:10px}small{color:#9aa6c4}</style></head><body>
<h1>HomeNetWatch Android agent</h1>
<p>$status</p>$note
<p>Installer code<br><span class=code>${escape(code)}</span></p>
<form method="post" action="/save">
<label>Server address</label><input name="server_url" value="${escape(store.serverUrl)}" placeholder="https://live.example.com" autocapitalize="off">
<label>Server TLS key pin</label><input name="pins" value="${escape(store.pins)}" placeholder="sha256/..." autocapitalize="off">
<small>The pin is printed by the server, the same one the Pi setup page asks for.</small><br>
<button type="submit">Save</button></form></body></html>"""
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
