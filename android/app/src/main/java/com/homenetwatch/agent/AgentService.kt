package com.homenetwatch.agent

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.PowerManager

/**
 * Keeps the agent running in the background: the setup page and enrollment, then, once approved, the tunnel, the
 * health report, the voice stream and the speed test, each on its own loop like the Pi's.
 */
class AgentService : Service() {
    private lateinit var store: Store
    private var setup: SetupServer? = null
    private val threads = mutableListOf<Thread>()
    private var wakeLock: PowerManager.WakeLock? = null
    private var tunnel: TunnelManager? = null
    @Volatile private var running = false
    @Volatile private var resetTunnel = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        store = Store(this)
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "HomeNetWatch agent", NotificationManager.IMPORTANCE_LOW))
        val notification = Notification.Builder(this, CHANNEL)
            .setContentTitle("HomeNetWatch agent")
            .setContentText("Monitoring this network")
            .setSmallIcon(R.drawable.ic_launcher)
            .setOngoing(true)
            .build()
        if (android.os.Build.VERSION.SDK_INT >= 34) {
            startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(1, notification)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PAUSE -> store.paused = true
            ACTION_RESUME -> store.paused = false
            ACTION_RESET_TUNNEL -> resetTunnel = true
        }
        if (intent?.action != null) wake()
        if (!running) {
            running = true
            Status.startedAt = System.currentTimeMillis()
            // Keep the CPU awake while the TV is in standby, so the tests do not stop.
            val power = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "homenetwatch:agent").apply { acquire() }
            setup = SetupServer(store).also { it.start() }
            val tunnel = TunnelManager(this, store).also { this.tunnel = it }
            val voice = Voice(this, store).also { Status.voice = it }
            val speed = SpeedTest(store).also { Status.speed = it }
            val enroller = Enroller(store)
            loop("agent-loop") {
                if (!store.enrolled) {
                    if (store.serverUrl.isEmpty()) {
                        // Not set up yet: ask the server whether the owner has registered this device from its QR code.
                        try {
                            Pairing.claim(store, BuildConfig.DASHBOARD_URL)
                        } catch (_: Exception) {
                        }
                        if (store.serverUrl.isEmpty()) return@loop 5_000L
                    }
                    enroller.step()
                    return@loop 10_000L
                }
                if (store.paused) {
                    voice.stopAll()
                    tunnel.down()
                    Status.disconnected("Paused from the TV")
                    return@loop 60_000L
                }
                if (resetTunnel) {
                    // Asked from the TV: take the tunnel down and bring it straight back, flows and all.
                    resetTunnel = false
                    voice.stopAll()
                    tunnel.down()
                    Status.disconnected("Resetting the tunnel")
                }
                val problem = try {
                    tunnel.ensureUp()
                } catch (failure: Exception) {
                    "The tunnel did not start: ${failure.message ?: failure.javaClass.simpleName}"
                }
                if (problem != null) {
                    Status.disconnected(problem)
                    return@loop RETRY_MS
                }
                if (Status.tunnelUpSince == 0L) Status.tunnelUpSince = System.currentTimeMillis()
                Status.tunnel = "Tunnel up"
                try {
                    Health.report(this, store, voice.lastFailure)
                    val now = System.currentTimeMillis()
                    if (Status.connectedSince == 0L) Status.connectedSince = now
                    Status.lastReportAt = now
                    Status.health = ""
                    maxOf(store.healthInterval, 15) * 1000L
                } catch (failure: Exception) {
                    // Often the first report races the tunnel coming up; try again soon rather than in a minute.
                    Status.connectedSince = 0L
                    Status.health = "Report failed: ${failure.message ?: failure.javaClass.simpleName}"
                    RETRY_MS
                }
            }
            loop("voice-loop") {
                if (store.enrolled && !store.paused && tunnel.isUp()) voice.sync() else voice.stopAll()
                15_000L
            }
            loop("speedtest-loop") {
                if (store.enrolled && !store.paused && tunnel.isUp()) speed.pollOnce()
                3_000L
            }
        }
        return START_STICKY
    }

    /** Runs one pass after another. A failed pass must not take the app down; the next one tries again. */
    private fun loop(name: String, pass: () -> Long) {
        threads += Thread({
            while (running) {
                val wait = try {
                    pass()
                } catch (_: Exception) {
                    10_000L
                }
                try {
                    Thread.sleep(wait)
                } catch (_: InterruptedException) {
                    // Woken early by a button on the TV, or stopped: the while condition tells which.
                }
            }
        }, name).apply { isDaemon = true; start() }
    }

    override fun onDestroy() {
        running = false
        threads.forEach { it.interrupt() }
        Status.voice?.stopAll()
        tunnel?.down()
        Status.disconnected("Stopped")
        Status.startedAt = 0L
        setup?.stop()
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    /** Runs every loop's next pass now, so a button on the TV takes effect at once. */
    private fun wake() {
        threads.forEach { it.interrupt() }
    }

    /** What the TV screen shows about the running agent. Times are epoch milliseconds; 0 means not now. */
    object Status {
        @Volatile var tunnel = ""
        @Volatile var health = ""
        @Volatile var startedAt = 0L
        @Volatile var tunnelUpSince = 0L
        /** Since the first of an unbroken run of accepted health reports. */
        @Volatile var connectedSince = 0L
        @Volatile var lastReportAt = 0L

        fun disconnected(why: String) {
            tunnel = why
            tunnelUpSince = 0L
            connectedSince = 0L
        }
        @Volatile var voice: Voice? = null
        @Volatile var speed: SpeedTest? = null
    }

    companion object {
        private const val CHANNEL = "agent"
        private const val RETRY_MS = 15_000L
        const val ACTION_PAUSE = "com.homenetwatch.agent.PAUSE"
        const val ACTION_RESUME = "com.homenetwatch.agent.RESUME"
        const val ACTION_RESET_TUNNEL = "com.homenetwatch.agent.RESET_TUNNEL"

        fun start(context: Context, action: String? = null) {
            context.startForegroundService(Intent(context, AgentService::class.java).setAction(action))
        }
    }
}
