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

/** Keeps the agent running in the background: the setup page, then enrollment, and later the tests. */
class AgentService : Service() {
    private lateinit var store: Store
    private var setup: SetupServer? = null
    private var worker: Thread? = null
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
            val enroller = Enroller(store)
            worker = Thread({
                while (running) {
                    enroller.step()
                    try {
                        Thread.sleep(if (store.enrolled) 60_000 else 10_000)
                    } catch (_: InterruptedException) {
                        break
                    }
                }
            }, "agent-loop").apply { isDaemon = true; start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        worker?.interrupt()
        setup?.stop()
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "agent"

        fun start(context: Context) {
            context.startForegroundService(Intent(context, AgentService::class.java))
        }
    }
}
