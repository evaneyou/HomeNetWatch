package com.homenetwatch.agent

import android.app.ActivityManager
import android.content.Context
import android.os.StatFs
import android.os.SystemClock
import org.json.JSONObject

/**
 * The health report the Pi sends every minute (monitor/health.py), with what Android lets an app read. CPU load and
 * temperature are not readable by an app on Android 8 and later, so they are sent as zero and empty; the dashboard
 * does not show them for an Android agent. A report is what marks the agent online.
 */
object Health {
    const val AGENT_VERSION = "android-0.2.0"

    fun payload(context: Context, store: Store, lastError: String): JSONObject {
        val memory = ActivityManager.MemoryInfo()
        (context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager).getMemoryInfo(memory)
        val disk = StatFs(context.filesDir.absolutePath)
        val mb = 1024L * 1024L
        return JSONObject()
            .put("hostname", Enroller.hostname())
            .put("agent_version", AGENT_VERSION)
            .put("uptime_seconds", SystemClock.elapsedRealtime() / 1000)
            .put("cpu_percent", 0.0)
            .put("load_1", 0.0)
            .put("mem_used_mb", (memory.totalMem - memory.availMem) / mb)
            .put("mem_total_mb", memory.totalMem / mb)
            .put("disk_used_mb", (disk.totalBytes - disk.availableBytes) / mb)
            .put("disk_total_mb", disk.totalBytes / mb)
            .put("lan_ip", Enroller.localIpv4() ?: JSONObject.NULL)
            .put("config_version", store.configVersion)
            .put("last_error", lastError.take(600).ifEmpty { JSONObject.NULL })
    }

    /** Sends one report and keeps what the server answers: the name, the report interval and the config version. */
    fun report(context: Context, store: Store, lastError: String) {
        val reply = Api.post(store, "/api/v1/monitors/health", payload(context, store, lastError))
        reply.optString("name").takeIf { it.isNotEmpty() }?.let { store.name = it }
        reply.optInt("health_interval_seconds", 0).takeIf { it > 0 }?.let { store.healthInterval = it }
        val version = reply.optInt("config_version", store.configVersion)
        if (version != store.configVersion) {
            store.configVersion = version
            // Tell the server straight away that the new settings are in use, as the Pi does.
            Api.post(store, "/api/v1/monitors/health", payload(context, store, lastError))
        }
    }
}
