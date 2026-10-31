package com.homenetwatch.agent

import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.abs
import kotlin.random.Random

/**
 * The live speed test, a port of monitor/speedtest.py. It talks the LibreSpeed protocol to the server's LibreSpeed
 * over the tunnel (download reads /garbage.php, upload posts to /empty.php, ping times /empty.php) and reports a
 * reading every half second. The device is on Wi-Fi only, so whichever link the dashboard asks for, it measures Wi-Fi.
 */
class SpeedTest(private val store: Store) {
    @Volatile var lastResult = ""
        private set

    /** Asks the server whether someone pressed Start for this device, and runs the test if so. */
    fun pollOnce() {
        val test = Api.get(store, "/api/v1/monitors/speedtest").optJSONObject("test") ?: return
        run(test)
    }

    private fun run(test: JSONObject) {
        val id = test.optString("id").ifEmpty { return }
        val prefix = "/api/v1/monitors/speedtest/$id"
        val downSeconds = test.optDouble("down_seconds", 15.0).coerceIn(1.0, 20.0)
        val upSeconds = test.optDouble("up_seconds", 15.0).coerceIn(1.0, 20.0)
        val cancelled = AtomicBoolean(false)
        val done = AtomicBoolean(false)
        val pending = mutableListOf<JSONObject>()
        val extra = JSONObject()
        val lock = Any()
        val poster = Thread({
            while (true) {
                val finished = done.get()
                Thread.sleep(500)
                val batch: List<JSONObject>
                val body: JSONObject
                synchronized(lock) {
                    batch = pending.take(20)
                    repeat(batch.size) { pending.removeAt(0) }
                    body = JSONObject(extra.toString())
                    extra.keys().asSequence().toList().forEach { extra.remove(it) }
                }
                if (batch.isNotEmpty() || body.length() > 0 || finished) {
                    try {
                        val reply = Api.post(store, "$prefix/samples", body.put("samples", JSONArray(batch)), 5_000)
                        if (reply.optBoolean("cancel")) cancelled.set(true)
                    } catch (_: Exception) {
                    }
                }
                val more = synchronized(lock) { pending.isNotEmpty() }
                if (finished && !more) return@Thread
            }
        }, "speedtest-post").apply { isDaemon = true; start() }

        fun emit(seq: Int, phase: String, tMs: Long, mbps: Double) {
            synchronized(lock) { pending += JSONObject().put("seq", seq).put("phase", phase).put("t_ms", tMs).put("mbps", mbps) }
        }

        var result = JSONObject().put("status", "failed").put("error", "")
        try {
            val (ping, jitter) = measurePing()
            if (ping == null) {
                result.put("error", "The speed test server could not be reached over the tunnel.")
                return
            }
            synchronized(lock) { extra.put("ping_ms", round2(ping)).put("jitter_ms", round2(jitter ?: 0.0)) }
            val (down, next) = phase("download", downSeconds, ::emit, cancelled, 0)
            var up: Double? = null
            if (!cancelled.get()) up = phase("upload", upSeconds, ::emit, cancelled, next).first
            if (cancelled.get()) return
            if (down == null && up == null) {
                result.put("error", "No data came back over Wi-Fi.")
                return
            }
            result = JSONObject()
                .put("status", "finished")
                .put("down_mbps", round2(down ?: 0.0))
                .put("up_mbps", round2(up ?: 0.0))
                .put("ping_ms", round2(ping))
                .put("jitter_ms", round2(jitter ?: 0.0))
                .put("error", "")
            lastResult = "Last speed test: ${round2(down ?: 0.0)} down, ${round2(up ?: 0.0)} up Mbps"
        } catch (failure: Exception) {
            result = JSONObject().put("status", "failed").put("error", "The speed test stopped: ${failure.javaClass.simpleName}")
        } finally {
            done.set(true)
            poster.join(8_000)
            if (!cancelled.get()) {
                try {
                    Api.post(store, "$prefix/finish", result, 8_000)
                } catch (_: Exception) {
                }
            }
        }
    }

    /** Round trip and jitter in ms over one kept-alive connection; the first request, which connects, is not counted. */
    private fun measurePing(count: Int = 8): Pair<Double?, Double?> {
        val times = mutableListOf<Double>()
        for (index in 0..count) {
            val started = System.nanoTime()
            try {
                val connection = open("/empty.php?r=$index", 5_000)
                connection.inputStream.use { it.readBytes() }
            } catch (_: Exception) {
                break
            }
            if (index > 0) times += (System.nanoTime() - started) / 1e6
        }
        if (times.isEmpty()) return null to null
        val gaps = times.zipWithNext { a, b -> abs(b - a) }
        return times.average() to (if (gaps.isEmpty()) 0.0 else gaps.average())
    }

    private fun phase(
        phase: String,
        seconds: Double,
        emit: (Int, String, Long, Double) -> Unit,
        cancelled: AtomicBoolean,
        seqStart: Int,
    ): Pair<Double?, Int> {
        val streams = if (phase == "download") DOWN_STREAMS else UP_STREAMS
        val total = AtomicLong(0)
        val stop = AtomicBoolean(false)
        val workers = List(streams) {
            Thread({ if (phase == "download") download(total, stop) else upload(total, stop) }, "speedtest-$phase").apply {
                isDaemon = true
                start()
            }
        }
        val started = System.nanoTime()
        var seq = seqStart
        val readings = mutableListOf<Pair<Long, Double>>()
        var lastTotal = 0L
        var lastAt = started
        var previous = 0.0
        try {
            var nextTick = started + TICK_NS
            while (!cancelled.get() && System.nanoTime() - started < seconds * 1e9) {
                val wait = (nextTick - System.nanoTime()) / 1_000_000
                if (wait > 0) Thread.sleep(minOf(wait, 250))
                val now = System.nanoTime()
                if (now < nextTick) continue
                val sum = total.get()
                val rate = (sum - lastTotal) * 8 / 1e6 / maxOf((now - lastAt) / 1e9, 1e-6)
                // A reading covers the last second, so each bar is steady but still shows a ramp.
                val shown = if (seq == seqStart) rate else (rate + previous) / 2
                previous = rate
                lastTotal = sum
                lastAt = now
                val tMs = (now - started) / 1_000_000
                emit(seq, phase, tMs, round2(shown))
                readings += tMs to shown
                seq += 1
                nextTick += TICK_NS
            }
        } finally {
            stop.set(true)
            workers.forEach { it.join(2_000) }
        }
        val steady = readings.filter { it.first >= WARM_UP_MS }.map { it.second }.ifEmpty { readings.map { it.second } }
        return (if (steady.isEmpty()) null else steady.average()) to seq
    }

    private fun download(total: AtomicLong, stop: AtomicBoolean) {
        val buffer = ByteArray(READ_BUFFER)
        while (!stop.get()) {
            try {
                val connection = open("/garbage.php?ckSize=200&r=${System.nanoTime()}", 8_000)
                connection.inputStream.use { input ->
                    while (!stop.get()) {
                        val got = input.read(buffer)
                        if (got < 0) break
                        total.addAndGet(got.toLong())
                    }
                }
                connection.disconnect()
            } catch (_: Exception) {
                if (!stop.get()) Thread.sleep(200)
            }
        }
    }

    private fun upload(total: AtomicLong, stop: AtomicBoolean) {
        while (!stop.get()) {
            try {
                val connection = open("/empty.php?r=${System.nanoTime()}", 8_000)
                connection.requestMethod = "POST"
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/octet-stream")
                connection.setFixedLengthStreamingMode(UP_BODY_BYTES)
                var sent = 0L
                connection.outputStream.use { output ->
                    while (sent < UP_BODY_BYTES && !stop.get()) {
                        output.write(UP_CHUNK)
                        sent += UP_CHUNK.size
                        total.addAndGet(UP_CHUNK.size.toLong())
                    }
                }
                if (sent >= UP_BODY_BYTES) connection.inputStream.use { it.readBytes() }
                connection.disconnect()
            } catch (_: Exception) {
                if (!stop.get()) Thread.sleep(200)
            }
        }
    }

    private fun open(path: String, timeoutMs: Int): HttpURLConnection =
        (URL("http://${Api.SERVER_IP}:$PORT$path").openConnection() as HttpURLConnection).apply {
            connectTimeout = timeoutMs
            readTimeout = timeoutMs
            useCaches = false
        }

    companion object {
        const val PORT = 8080
        const val DOWN_STREAMS = 4
        const val UP_STREAMS = 3
        const val TICK_NS = 500_000_000L
        const val READ_BUFFER = 256 * 1024
        const val UP_BODY_BYTES = 24L * 1024 * 1024
        const val WARM_UP_MS = 3000L
        val UP_CHUNK: ByteArray = Random.nextBytes(256 * 1024)

        fun round2(value: Double): Double = Math.round(value * 100) / 100.0
    }
}
