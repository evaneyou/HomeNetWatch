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

    /**
     * The whole setup as a checklist: finished steps collapse to a tick and a summary, the current step is the only
     * one open, and later steps are greyed out until their turn.
     */
    private fun page(step: Int, error: String): String {
        val code = escape(store.ensureInstallerCode())
        val token = "<input type=hidden name=t value=\"$formToken\">"
        val alert = if (error.isEmpty()) "" else "<p class=error>${escape(error)}</p>"
        val hasKey = store.serverUrl.isNotEmpty()
        fun done(item: Int) = when (item) {
            Wizard.NAME -> store.deviceName.isNotEmpty()
            Wizard.REGISTER -> hasKey || step >= Wizard.KEY
            Wizard.KEY -> hasKey
            else -> store.enrolled
        }
        fun summary(item: Int) = when (item) {
            Wizard.NAME -> escape(store.deviceName)
            Wizard.REGISTER -> "Code <span class=nowrap>$code</span>"
            Wizard.KEY -> "Saved for ${escape(store.serverUrl.removePrefix("https://"))}"
            else -> "On the dashboard as ${escape(store.name.ifEmpty { store.deviceName })}"
        }
        fun open(item: Int) = when (item) {
            Wizard.NAME -> """
<p class=hint>Pick a name you will recognise on the dashboard.</p>
<form method="post" action="/save-name">$token
<input name="device_name" value="${escape(store.deviceName)}" placeholder="Living Room Shield" maxlength="128" required autofocus>
$alert<button class=btn type="submit">Save name</button></form>"""
            Wizard.REGISTER -> """
<p class=hint>On your computer, open the dashboard, go to <b>Remote Agent Onboarding</b> and register this code, typed exactly as shown. The name you enter there does not matter: this device keeps <b>${escape(store.deviceName)}</b>.</p>
<div class=code><span id=code>$code</span><button type=button class="btn small" id=copy onclick="copyCode()">Copy</button></div>
<a class=btn href="/?step=3">I registered it</a>"""
            Wizard.KEY -> """
<p class=hint>After you register the code, its card on the dashboard shows a <b>Setup key</b>. Copy it and paste it here.</p>
<form method="post" action="/save-key">$token
<textarea name="setup_key" rows="4" placeholder="hnw1.eyJ1Ijoi…" autocapitalize="off" spellcheck="false" required autofocus></textarea>
$alert<button class=btn type="submit">Save setup key</button></form>"""
            else -> """
<p class=hint>On the dashboard, choose <b>Approve</b> on the card for code <span class=nowrap>$code</span>. This page updates by itself.</p>
<p class="wait${if (problem()) " warn" else ""}" id=detail>${escape(approveDetail())}</p>"""
        }
        val rows = (1..Wizard.COUNT).joinToString("\n") { item ->
            when {
                item == step -> "<div class=\"row open\"><span class=\"dot now\">$item</span><div class=grow><b>${Wizard.title(item)}</b>${open(item)}</div></div>"
                done(item) -> {
                    val edit = if (store.enrolled) "" else "<a class=edit href=\"/?step=$item\">Edit</a>"
                    "<div class=row><span class=\"dot ok\">&#10003;</span><div class=grow><b>${Wizard.title(item)}</b><p class=sum>${summary(item)}</p></div>$edit</div>"
                }
                else -> "<div class=\"row later\"><span class=dot>$item</span><div class=grow><b>${Wizard.title(item)}</b></div></div>"
            }
        }
        val next = if (step == Wizard.DONE) "<p class=\"banner ok\">Setup complete. You can close this page. To set it up again, press Menu on the TV remote.</p>"
        else "<p class=banner>Next: ${Wizard.title(step).replaceFirstChar { it.lowercase() }}</p>"
        return """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Set up HomeNetWatch agent</title>
<style>body{font:16px/1.5 system-ui;margin:0 auto;padding:24px 20px;background:#0f1424;color:#e8ecf8;max-width:520px}
h1{font-size:22px;margin:0 0 12px}.hint{color:#9aa6c4;font-size:14px;margin:6px 0 10px}
.banner{background:#16304a;color:#9fd0ff;padding:10px 12px;border-radius:10px;margin:0 0 8px}.banner.ok{background:#173a2e;color:#8fe3c6}
.row{display:flex;gap:12px;align-items:flex-start;padding:14px 0;border-top:1px solid #222c47}.row b{font-weight:700}
.row.later{color:#5f6a88}.grow{flex:1;min-width:0}.sum{margin:2px 0 0;color:#9aa6c4;font-size:14px}
.dot{flex:none;width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font:700 14px system-ui;background:#1a2138;color:#5f6a88}
.dot.now{background:#4fd1b5;color:#06201b}.dot.ok{background:#173a2e;color:#4fd1b5}
.edit{color:#9fd0ff;font-size:14px;text-decoration:none;padding-top:2px}
input,textarea{width:100%;box-sizing:border-box;font:16px ui-monospace,monospace;padding:12px;border-radius:10px;border:1px solid #2a3556;background:#161d2f;color:#e8ecf8}
.btn{display:block;box-sizing:border-box;width:100%;text-align:center;text-decoration:none;margin-top:12px;padding:12px 20px;border:0;border-radius:10px;background:#4fd1b5;color:#06201b;font:800 16px system-ui;cursor:pointer}
.btn.small{width:auto;display:inline-block;margin:0;padding:8px 14px;font-size:14px}
.code{display:flex;justify-content:space-between;align-items:center;gap:12px;font:700 20px ui-monospace,monospace;letter-spacing:1px;background:#161d2f;padding:10px 12px;border-radius:10px;white-space:nowrap}
.error{background:#3a1d24;color:#ffb4b4;padding:10px 12px;border-radius:10px}.wait{background:#1d2a2b;padding:12px;border-radius:10px;margin:0}
.wait.warn{background:#3a2f14;color:#ffd98a}.nowrap{white-space:nowrap;font-family:ui-monospace,monospace}
.foot{margin-top:28px;color:#6f7a99;font-size:12px}</style></head><body>
<h1>Set up this device</h1>
$next
$rows
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
