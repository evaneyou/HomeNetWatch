package com.homenetwatch.agent

import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.security.SecureRandom
import org.json.JSONObject

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
            val target = request.substringAfter(' ').substringBefore(' ')
            val path = target.substringBefore('?')
            val out = it.getOutputStream()
            if (method == "POST" && (path == "/save-name" || path == "/save-key") && length in 1..4096) {
                val chars = CharArray(length)
                var read = 0
                while (read < length) {
                    val n = reader.read(chars, read, length - read)
                    if (n < 0) break
                    read += n
                }
                val failure = save(path, String(chars, 0, read))
                // A saved step moves on (post, then redirect, so a reload does not post again). A refusal stays put.
                if (failure == null) redirect(out, "/") else respond(out, page(if (path == "/save-name") Wizard.NAME else Wizard.KEY, failure))
            } else if (method == "GET" && path == "/") {
                val requested = target.substringAfter("step=", "").take(1).toIntOrNull()
                respond(out, page(Wizard.show(store, requested), ""))
            } else if (method == "GET" && path == "/status") {
                val status = JSONObject().put("step", Wizard.show(store, Wizard.APPROVE)).put("detail", approveDetail()).put("problem", problem())
                respond(out, status.toString(), type = "application/json")
            } else {
                respond(out, "Not found", 404)
            }
        }
    }

    /** Saves one form. Returns null when it was saved, or the reason it was not. */
    private fun save(path: String, body: String): String? {
        val form = body.split("&").mapNotNull {
            val key = it.substringBefore("=", "")
            if (key.isEmpty()) null else key to URLDecoder.decode(it.substringAfter("=", ""), "UTF-8")
        }.toMap()
        if (form["t"] != formToken) return "This page was out of date, so nothing was saved. Try again."
        if (store.enrolled) return "This device is already approved. To set it up again, press Menu on the TV remote."
        return try {
            if (path == "/save-name") saveName(form["device_name"].orEmpty()) else saveKey(form["setup_key"].orEmpty())
            null
        } catch (failure: IllegalArgumentException) {
            failure.message ?: "Check what you entered and try again."
        }
    }

    private fun saveName(raw: String) {
        val name = raw.trim().replace(Regex("\\s+"), " ")
        require(name.isNotEmpty()) { "Give this device a name, for example Living Room Shield." }
        require(name.length <= 128) { "The name can be up to 128 characters." }
        store.deviceName = name
    }

    private fun saveKey(raw: String) {
        val key = Wire.parseSetupKey(raw)
        store.serverUrl = key.serverUrl
        store.pins = key.pins.joinToString(" ")
        store.state = "waiting_for_approval"
        store.checkedIn = false
        store.lastError = ""
    }

    /** True when step 4 is stuck on something the owner has to fix, such as a code the server does not know. */
    private fun problem(): Boolean = !store.enrolled && store.lastError.isNotEmpty()

    /** What step 4 says while it waits, refreshed live from /status. */
    private fun approveDetail(): String = when {
        store.enrolled -> "Approved."
        store.lastError.isNotEmpty() -> store.lastError
        store.checkedIn -> "This device has checked in. On the dashboard, choose Approve on its card."
        else -> "Contacting the server…"
    }

    private fun page(step: Int, error: String): String {
        val code = escape(store.ensureInstallerCode())
        val token = "<input type=hidden name=t value=\"$formToken\">"
        val alert = if (error.isEmpty()) "" else "<p class=error>${escape(error)}</p>"
        fun back(to: Int) = "<a class=\"btn ghost\" href=\"/?step=$to\">Back</a>"
        val body = when (step) {
            Wizard.NAME -> """
<p class=lead>Pick a name you will recognise on the dashboard.</p>
<form method="post" action="/save-name">$token
<input name="device_name" value="${escape(store.deviceName)}" placeholder="Living Room Shield" maxlength="128" required autofocus>
$alert<button class=btn type="submit">Save and continue</button></form>"""
            Wizard.REGISTER -> """
<p class=lead>On your computer, open the HomeNetWatch dashboard, go to <b>Remote Agent Onboarding</b> and register this installer code.</p>
<div class=code><span id=code>$code</span><button type=button class="btn small" id=copy onclick="copyCode()">Copy</button></div>
<p class=hint>Type it exactly as shown. The name you enter there does not matter: this device keeps the name <b>${escape(store.deviceName)}</b>.</p>
<p class=hint>After you register it, the code's card on the dashboard shows a <b>Setup key</b>.</p>
<a class=btn href="/?step=3">I registered it, next</a>${back(Wizard.NAME)}"""
            Wizard.KEY -> """
<p class=lead>On the dashboard, copy the <b>Setup key</b> from the card of the code you registered, and paste it here.</p>
<form method="post" action="/save-key">$token
<textarea name="setup_key" rows="4" placeholder="hnw1.eyJ1Ijoi…" autocapitalize="off" spellcheck="false" required autofocus></textarea>
$alert<button class=btn type="submit">Save and continue</button></form>${back(Wizard.REGISTER)}"""
            Wizard.APPROVE -> """
<p class=lead>Go back to the dashboard and choose <b>Approve</b> on the card for code <b class=nowrap>$code</b>.</p>
<p class="wait${if (problem()) " warn" else ""}" id=detail>${escape(approveDetail())}</p>
<p class=hint>This page updates by itself. Approve becomes available on the dashboard a few seconds after this device checks in.</p>
${back(Wizard.KEY)}"""
            else -> """
<p class=lead>This device is on the dashboard as <b>${escape(store.name.ifEmpty { store.deviceName })}</b>. You can close this page.</p>
<p class=hint>To set it up again, press the Menu button on the TV remote.</p>"""
        }
        val progress = (1..Wizard.COUNT).joinToString("") { "<span class=\"${if (it <= step) "on" else ""}\"></span>" }
        val counter = if (step == Wizard.DONE) "Setup complete" else "Step $step of ${Wizard.COUNT}"
        return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Set up HomeNetWatch agent</title>
<style>body{font:16px/1.5 system-ui;margin:0 auto;padding:24px 20px;background:#0f1424;color:#e8ecf8;max-width:520px}
h1{font-size:22px;margin:4px 0 10px}.top{font-size:14px;color:#9aa6c4;margin:0}
.bar{display:flex;gap:6px;margin:8px 0 22px}.bar span{flex:1;height:5px;border-radius:3px;background:#2a3556}.bar span.on{background:#4fd1b5}
.lead{font-size:17px}.hint{color:#9aa6c4;font-size:14px}
input,textarea{width:100%;box-sizing:border-box;font:16px ui-monospace,monospace;padding:12px;border-radius:10px;border:1px solid #2a3556;background:#161d2f;color:#e8ecf8}
.btn{display:block;box-sizing:border-box;width:100%;text-align:center;text-decoration:none;margin-top:16px;padding:13px 20px;border:0;border-radius:10px;background:#4fd1b5;color:#06201b;font:800 16px system-ui;cursor:pointer}
.btn.ghost{background:transparent;color:#c3cce6;border:1px solid #2a3556;margin-top:10px}.btn.small{width:auto;display:inline-block;margin:0;padding:8px 14px;font-size:14px}
.code{display:flex;justify-content:space-between;align-items:center;gap:12px;font:700 24px ui-monospace,monospace;letter-spacing:2px;background:#161d2f;padding:12px 14px;border-radius:10px}
.error{background:#3a1d24;color:#ffb4b4;padding:10px 12px;border-radius:10px}.wait{background:#1d2a2b;padding:12px;border-radius:10px}.wait.warn{background:#3a2f14;color:#ffd98a}.nowrap{white-space:nowrap}
.foot{margin-top:32px;color:#6f7a99;font-size:12px}</style></head><body>
<p class=top>$counter</p><div class=bar>$progress</div>
<h1>${Wizard.title(step)}</h1>
$body
<p class=foot>HomeNetWatch Android agent · ${escape(Enroller.deviceLabel())}</p>
<script>
function copyCode(){var t=document.getElementById('code').textContent,b=document.getElementById('copy');
function done(){b.textContent='Copied';}
if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(t).then(done);return;}
var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();document.execCommand('copy');a.remove();done();}
var detail=document.getElementById('detail');
if(detail)setInterval(function(){fetch('/status').then(function(r){return r.json();}).then(function(s){
if(s.step!==4){location.href='/';}else{detail.textContent=s.detail;detail.className=s.problem?'wait warn':'wait';}}).catch(function(){});},3000);
</script></body></html>"""
    }

    private fun escape(text: String): String =
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;")

    private fun redirect(out: java.io.OutputStream, location: String) {
        out.write("HTTP/1.1 303 See Other\r\nLocation: $location\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".toByteArray(Charsets.US_ASCII))
        out.flush()
    }

    private fun respond(out: java.io.OutputStream, body: String, status: Int = 200, type: String = "text/html") {
        val bytes = body.toByteArray(Charsets.UTF_8)
        val head = "HTTP/1.1 $status ${if (status == 200) "OK" else "Not Found"}\r\nContent-Type: $type; charset=utf-8\r\n" +
            "Content-Length: ${bytes.size}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
        out.write(head.toByteArray(Charsets.US_ASCII))
        out.write(bytes)
        out.flush()
    }

    companion object {
        const val PORT = 8080
    }
}
