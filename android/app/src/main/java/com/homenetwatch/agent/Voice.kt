package com.homenetwatch.agent

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant

/**
 * The synthetic traffic runner, a port of monitor/synthetic.py: it asks the server which flows to run, starts one
 * rtnp-send per flow, and relays each interval's figures to the server. The sender ships inside the app as
 * librtnp_send.so and runs from the app's native library folder.
 *
 * Packets go to the server's public address on UDP 8998, over Wi-Fi and outside the tunnel (the tunnel only routes
 * the server's tunnel address). The control channel goes to the server's tunnel address. An Android agent has one
 * tunnel, so both of its links use the same control address.
 */
class Voice(private val context: Context, private val store: Store) {
    private class Flow(val args: List<String>, val process: Process, val mode: String, val restartToken: Int)

    private val running = mutableMapOf<Pair<String, String>, Flow>()
    private val lock = Any()
    @Volatile var lastFailure = ""
        private set

    private val keyFile get() = File(context.filesDir, "rtnp.key")
    private val srtpFile get() = File(context.filesDir, "rtnp-srtp.key")
    private val binary get() = File(context.applicationInfo.nativeLibraryDir, "librtnp_send.so")

    /** One pass: fetch the assignment and make the running flows match it. */
    fun sync() {
        val body = Api.get(store, "/api/v1/synthetic/assignment")
        body.optString("key").takeIf { it.isNotEmpty() }?.let { writePrivate(keyFile, it + "\n") }
        body.optString("srtp_key").takeIf { it.length == 60 }?.let { writePrivate(srtpFile, it.lowercase() + "\n") }
        val wanted = wantedJobs(body.optJSONArray("jobs") ?: JSONArray())
        val stale = synchronized(lock) { running.keys.filter { it !in wanted } }
        stale.forEach { stop(it) }
        val target = body.optString("target_host").ifEmpty { store.measurementHost }
        for ((item, job) in wanted) ensure(item, job, target)
    }

    /** What the screen shows: how many flows are running, or why the last one stopped. */
    fun summary(): String {
        val count = synchronized(lock) { running.values.count { it.process.isAlive } }
        return when {
            count > 0 -> if (count == 1) "Voice stream running" else "$count flows running"
            lastFailure.isNotEmpty() -> "Voice stream stopped: $lastFailure"
            else -> "No voice stream assigned yet"
        }
    }

    fun stopAll() {
        synchronized(lock) { running.keys.toList() }.forEach { stop(it) }
    }

    private fun ensure(item: Pair<String, String>, job: JSONObject, target: String) {
        val (runId, link) = item
        val mode = job.optString("mode")
        val args = try {
            sendArgs(
                binary.absolutePath,
                job.getJSONObject("options"),
                target,
                keyFile.absolutePath,
                srtpFile.absolutePath,
                forever = mode == "continuous",
                durationSeconds = job.optInt("duration_seconds", 0),
            )
        } catch (failure: Exception) {
            finish(runId, link, "failed", failure.message ?: "Bad flow settings")
            return
        }
        val token = job.optInt("restart_token", 0)
        synchronized(lock) {
            val current = running[item]
            if (current != null && current.args == args && current.process.isAlive && token <= current.restartToken) return
        }
        stop(item)
        if (!binary.isFile) {
            finish(runId, link, "failed", "The traffic sender is missing from this build of the app")
            return
        }
        val process = ProcessBuilder(args).directory(context.filesDir).start()
        val flow = Flow(args, process, mode, token)
        synchronized(lock) { running[item] = flow }
        Thread({ relay(item, flow) }, "voice-$link").apply { isDaemon = true }.start()
    }

    /** Reads the sender's JSON lines and posts each interval, restarting a flow that has gone quiet. */
    private fun relay(item: Pair<String, String>, flow: Flow) {
        val (runId, link) = item
        val stderr = StringBuilder()
        Thread({
            try {
                flow.process.errorStream.bufferedReader().forEachLine { synchronized(stderr) { stderr.setLength(0); stderr.append(it) } }
            } catch (_: Exception) {
            }
        }, "voice-err").apply { isDaemon = true }.start()
        var quiet = 0
        try {
            flow.process.inputStream.bufferedReader().forEachLine { line ->
                val payload = jsonLine(line) ?: return@forEachLine
                if (payload.optString("type", "interval") != "interval" || !payload.has("flows")) return@forEachLine
                val state = flowState(payload.optJSONArray("flows")?.optJSONObject(0) ?: JSONObject())
                if (state.isNotEmpty()) {
                    quiet += 1
                    val limit = if (state == "missing") MISSING_INTERVALS else SILENT_INTERVALS
                    if (flow.mode == "continuous" && quiet >= limit) {
                        // Same rule as the Pi: a flow that reports nothing, or loses every packet, starts afresh.
                        synchronized(lock) { if (running[item] === flow) running.remove(item) }
                        flow.process.destroy()
                        return@forEachLine
                    }
                } else {
                    quiet = 0
                    if (lastFailure.startsWith("$link:")) lastFailure = ""
                }
                if (tooFast(payload)) {
                    flow.process.destroy()
                    finish(runId, link, "failed", "Packet rate is too high")
                    return@forEachLine
                }
                val unixMs = payload.optLong("unix_ms", 0)
                val recorded = if (unixMs > 0) Instant.ofEpochMilli(unixMs) else Instant.now()
                try {
                    Api.post(
                        store,
                        "/api/v1/synthetic/samples",
                        JSONObject()
                            .put("run_id", runId)
                            .put("link", link)
                            .put("interval_index", payload.optInt("index", 0))
                            .put("recorded_at", recorded.toString())
                            .put("metrics", payload),
                    )
                } catch (_: Exception) {
                }
            }
        } catch (_: Exception) {
        }
        val code = try {
            flow.process.waitFor()
        } catch (_: InterruptedException) {
            -1
        }
        val ours = synchronized(lock) {
            val same = running[item] === flow
            if (same) running.remove(item)
            same
        }
        val note = synchronized(stderr) { stderr.toString().trim().take(200) }
        if (flow.mode == "manual") {
            finish(runId, link, if (code == 0) "finished" else "failed", if (code == 0) null else note.ifEmpty { "rtnp-send exited with code $code" })
        } else if (ours && code != 0) {
            // A continuous flow that stops by itself is started again on the next pass.
            lastFailure = "$link: ${note.ifEmpty { "rtnp-send exited with code $code" }}"
        }
    }

    private fun stop(item: Pair<String, String>) {
        val flow = synchronized(lock) { running.remove(item) } ?: return
        flow.process.destroy()
    }

    private fun finish(runId: String, link: String, status: String, message: String?) {
        try {
            Api.post(
                store,
                "/api/v1/synthetic/runs/$runId/finish",
                JSONObject().put("link", link).put("status", status).put("message", message ?: JSONObject.NULL),
            )
        } catch (_: Exception) {
        }
    }

    private fun writePrivate(file: File, content: String) {
        if (file.isFile && file.readText() == content) return
        file.writeText(content)
        file.setReadable(false, false)
        file.setReadable(true, true)
    }

    companion object {
        const val UDP_PORT = 8998
        const val CONTROL_PORT = 8999
        const val MAX_PPS = 200
        const val MAX_DURATION = 3600
        const val MISSING_INTERVALS = 3
        const val SILENT_INTERVALS = 6
        val PRESETS = setOf(
            "voip-g711a", "voip-g729", "voip-g723", "video-conference", "fortnite", "call-of-duty", "remote-desktop", "market-data",
        )
        val VOICE_PRESETS = setOf("voip-g711a", "voip-g729", "voip-g723")

        /** One flow per run and link. Stress tests need the Pi's wider limits and are not run here. */
        fun wantedJobs(jobs: JSONArray): Map<Pair<String, String>, JSONObject> {
            val chosen = linkedMapOf<Pair<String, String>, JSONObject>()
            for (index in 0 until jobs.length()) {
                val job = jobs.optJSONObject(index) ?: continue
                val link = job.optString("link")
                val runId = job.optString("run_id")
                if (link in setOf("wired", "wireless") && runId.isNotEmpty() && job.optString("mode") != "stress") {
                    chosen[runId to link] = job
                }
            }
            return chosen
        }

        fun isTunnelTarget(host: String): Boolean = host.startsWith("10.7.0.") || host.startsWith("10.7.1.")

        /** The sender's command line, with the same checks as the Pi's send_args. */
        fun sendArgs(
            binary: String,
            options: JSONObject,
            dataHost: String,
            keyFile: String,
            srtpKeyFile: String,
            forever: Boolean,
            durationSeconds: Int = 0,
        ): List<String> {
            require(dataHost.isNotEmpty() && !isTunnelTarget(dataHost) && ":" !in dataHost) { "Synthetic traffic must use the public IPv4 address" }
            val preset = options.optString("preset")
            require(preset in PRESETS) { "Unknown traffic preset" }
            require(!options.has("meter") && !options.has("agent") && !options.has("sip")) { "That option is not allowed" }
            val payload = options.optString("payload")
            require(!payload.startsWith("file:") && !payload.startsWith("wav:")) { "File payloads are not allowed" }
            if (options.has("rate_pps")) require(options.getDouble("rate_pps") <= MAX_PPS) { "Packet rate is too high" }
            val args = mutableListOf(
                binary, "flow",
                "-t", "$dataHost:$UDP_PORT",
                "-c", "${Api.SERVER_IP}:$CONTROL_PORT",
                "--preset", preset,
                "--json",
                "-i", "${options.getInt("report_interval_s")}s",
                "--key-file", keyFile,
                "--max-rtt", options.getInt("max_rtt_ms").toString(),
                "--max-jitter", options.getInt("max_jitter_ms").toString(),
                "--max-loss", number(options.get("max_loss_pct")),
            )
            if (options.optString("wire") == "rtp") args += listOf("--wire", "rtp", "--srtp-key-file", srtpKeyFile)
            options.optString("rtp").takeIf { it.isNotEmpty() }?.let { args += listOf("--rtp", it) }
            if (options.optInt("ptime_ms", 0) > 0) args += listOf("--ptime", options.getInt("ptime_ms").toString())
            if (options.optBoolean("video")) args += "--video"
            if (options.optBoolean("bidir")) {
                require(preset in VOICE_PRESETS) { "Two-way traffic is only for the voice profiles" }
                args += "--bidir"
            }
            if (options.has("rtcp_interval_s") && !options.isNull("rtcp_interval_s")) {
                args += listOf("--rtcp-interval", "${options.getInt("rtcp_interval_s")}s")
            }
            if (forever) {
                args += "--forever"
            } else {
                require(durationSeconds in 1..MAX_DURATION) { "A timed run needs a duration of at most 3600 seconds" }
                args += listOf("-d", "${durationSeconds}s")
            }
            return args
        }

        /** "missing" when an interval has no receive figures, "lost" when every packet was lost, else "". */
        fun flowState(flow: JSONObject): String {
            val noRx = !flow.has("rx") || flow.isNull("rx")
            val noLoss = !flow.has("loss_pct") || flow.isNull("loss_pct")
            if (noRx && noLoss) return "missing"
            if (!noLoss && flow.optDouble("loss_pct", 0.0) >= 99.9) return "lost"
            return ""
        }

        fun jsonLine(line: String): JSONObject? {
            val text = line.trim()
            if (!text.startsWith("{")) return null
            return try {
                JSONObject(text)
            } catch (_: Exception) {
                null
            }
        }

        /** Python prints 1 as "1" and 0.5 as "0.5"; keep the same text for the sender. */
        private fun number(value: Any): String {
            val asDouble = (value as? Number)?.toDouble() ?: value.toString().toDouble()
            return if (asDouble == Math.floor(asDouble) && !asDouble.isInfinite()) asDouble.toLong().toString() else asDouble.toString()
        }

        private fun tooFast(payload: JSONObject): Boolean {
            val interval = payload.optDouble("interval_s", 0.0)
            if (interval <= 0) return false
            var sent = 0L
            val flows = payload.optJSONArray("flows") ?: return false
            for (index in 0 until flows.length()) sent += flows.optJSONObject(index)?.optLong("sent", 0) ?: 0
            return sent / interval > MAX_PPS
        }
    }
}
