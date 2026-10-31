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
    @Volatile private var running = false

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
        if (!running) {
            running = true
            // Keep the CPU awake while the TV is in standby, so the tests do not stop.
            val power = getSystemService(Context.POWER_SERVICE) as PowerManager
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "homenetwatch:agent").apply { acquire() }
            setup = SetupServer(store).also { it.start() }
            val tunnel = TunnelManager(this, store)
            val voice = Voice(this, store).also { Status.voice = it }
            val speed = SpeedTest(store).also { Status.speed = it }
            val enroller = Enroller(store)
            loop("agent-loop") {
                if (!store.enrolled) {
                    enroller.step()
                    return@loop 10_000L
                }
                val problem = try {
                    tunnel.ensureUp()
                } catch (failure: Exception) {
                    "The tunnel did not start: ${failure.message ?: failure.javaClass.simpleName}"
                }
                Status.tunnel = problem ?: "Tunnel up"
                if (problem != null) return@loop RETRY_MS
                try {
                    Health.report(this, store, voice.lastFailure)
                    Status.health = "Reported at ${java.time.LocalTime.now().withNano(0)}"
                    maxOf(store.healthInterval, 15) * 1000L
                } catch (failure: Exception) {
                    // Often the first report races the tunnel coming up; try again soon rather than in a minute.
                    Status.health = "Report failed: ${failure.message ?: failure.javaClass.simpleName}"
                    RETRY_MS
                }
            }
            loop("voice-loop") {
                if (store.enrolled && tunnel.isUp()) voice.sync() else voice.stopAll()
                15_000L
            }
            loop("speedtest-loop") {
                if (store.enrolled && tunnel.isUp()) speed.pollOnce()
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
                    break
                }
            }
        }, name).apply { isDaemon = true; start() }
    }

    override fun onDestroy() {
        running = false
        threads.forEach { it.interrupt() }
        Status.voice?.stopAll()
        setup?.stop()
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    /** What the TV screen shows about the running agent. */
    object Status {
        @Volatile var tunnel = ""
        @Volatile var health = ""
        @Volatile var voice: Voice? = null
        @Volatile var speed: SpeedTest? = null
    }

    companion object {
        private const val CHANNEL = "agent"
        private const val RETRY_MS = 15_000L

        fun start(context: Context) {
            context.startForegroundService(Intent(context, AgentService::class.java))
        }
    }
}
