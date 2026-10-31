package com.homenetwatch.agent

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.net.Uri
import android.net.VpnService
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.widget.Button
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.TextView
import com.google.zxing.BarcodeFormat
import com.google.zxing.EncodeHintType
import com.google.zxing.qrcode.QRCodeWriter

/** One screen for a TV: what the agent is doing, the installer code and where to open the setup page. */
class MainActivity : Activity() {
    private companion object {
        const val VPN_REQUEST = 1
        const val QR_SIZE = 560
    }

    private lateinit var store: Store
    private val handler = Handler(Looper.getMainLooper())
    private var asking = false
    private lateinit var device: TextView
    private lateinit var status: TextView
    private lateinit var code: TextView
    private lateinit var where: TextView
    private lateinit var detail: TextView
    private lateinit var qr: ImageView
    private lateinit var controls: LinearLayout
    private lateinit var pauseButton: Button
    private var qrFor = ""
    private var offering = false
    /** An update the owner agreed to while Android still had to allow the app to install it. */
    private var pendingUpdate: Updater.Release? = null
    private val refresh = object : Runnable {
        override fun run() {
            draw()
            handler.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(Color.parseColor("#0F1424"))
            setPadding(96, 48, 96, 48)
        }
        fun text(size: Float, color: String, bold: Boolean = false) = TextView(this).apply {
            textSize = size
            setTextColor(Color.parseColor(color))
            if (bold) typeface = Typeface.DEFAULT_BOLD
            setPadding(0, 10, 0, 10)
        }
        column.addView(text(34f, "#E8ECF8", true).apply { this.text = "Android agent" })
        device = text(20f, "#C3CCE6").also { column.addView(it) }
        status = text(24f, "#4FD1B5", true).also { column.addView(it) }
        code = text(44f, "#E8ECF8", true).apply { typeface = Typeface.MONOSPACE }.also { column.addView(it) }
        where = text(22f, "#C3CCE6").also { column.addView(it) }
        detail = text(18f, "#9AA6C4").also { column.addView(it) }
        // Buttons for the remote: shown once the device is approved.
        fun button(label: String, onClick: () -> Unit) = Button(this).apply {
            text = label
            isAllCaps = false
            textSize = 18f
            setPadding(36, 18, 36, 18)
            // The focused button must be obvious from the sofa: mint with dark text, the rest dark with light text.
            fun shape(color: String) = GradientDrawable().apply {
                cornerRadius = 18f
                setColor(Color.parseColor(color))
            }
            background = StateListDrawable().apply {
                addState(intArrayOf(android.R.attr.state_focused), shape("#4FD1B5"))
                addState(intArrayOf(android.R.attr.state_pressed), shape("#4FD1B5"))
                addState(intArrayOf(), shape("#2A3556"))
            }
            setTextColor(
                ColorStateList(
                    arrayOf(intArrayOf(android.R.attr.state_focused), intArrayOf()),
                    intArrayOf(Color.parseColor("#06201B"), Color.parseColor("#E8ECF8")),
                ),
            )
            layoutParams = LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT)
                .apply { marginEnd = 20 }
            setOnClickListener { onClick() }
        }
        pauseButton = button("Pause agent") {
            AgentService.start(this, if (store.paused) AgentService.ACTION_RESUME else AgentService.ACTION_PAUSE)
        }
        controls = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(0, 24, 0, 8)
            addView(pauseButton)
            addView(button("Reset tunnel") { AgentService.start(this@MainActivity, AgentService.ACTION_RESET_TUNNEL) })
            addView(button("Restart agent") { restartAgent() })
            addView(button("Start over") { confirmStartOver() })
        }
        column.addView(controls)
        column.addView(text(16f, "#9AA6C4").apply { this.text = "Use the remote to choose a button. Start over removes this device's setup and shows a new QR code." })
        // The QR code sits beside the text: scanning it with a phone is the whole setup.
        qr = ImageView(this).apply { setPadding(24, 24, 24, 24); setBackgroundColor(Color.WHITE) }
        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(Color.parseColor("#0F1424"))
            setPadding(0, 0, 96, 0)
            addView(column, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, 1f))
            addView(qr, LinearLayout.LayoutParams(QR_SIZE, QR_SIZE))
        }
        // The HomeNetWatch logo in the top-left corner, as on the dashboard, in place of the system title bar.
        actionBar?.hide()
        val logo = ImageView(this).apply {
            setImageResource(R.drawable.logo)
            adjustViewBounds = true
            contentDescription = "HomeNetWatch"
        }
        val screen = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#0F1424"))
            setPadding(0, 48, 0, 0)
            addView(logo, LinearLayout.LayoutParams(LinearLayout.LayoutParams.WRAP_CONTENT, 110).apply { marginStart = 96 })
            addView(row, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        }
        setContentView(screen)
        AgentService.start(this)
        checkForUpdate()
    }

    override fun onResume() {
        super.onResume()
        handler.post(refresh)
        askForVpn()
        // Back from Android's "install unknown apps" screen: carry on with the update if it is now allowed.
        pendingUpdate?.let { release ->
            pendingUpdate = null
            if (packageManager.canRequestPackageInstalls()) startUpdate(release)
            else Updater.status = "Update skipped: HomeNetWatch is not allowed to install apps. Choose Restart agent to try again."
        }
    }

    /** Each start of the app (and Restart agent) looks for a newer version; the owner decides whether to install it. */
    private fun checkForUpdate() {
        if (offering) return
        offering = true
        Thread {
            val release = Updater.check()
            handler.post {
                if (release != null && !isFinishing) offerUpdate(release) else offering = false
            }
        }.start()
    }

    private fun offerUpdate(release: Updater.Release) {
        AlertDialog.Builder(this)
            .setTitle("Update available")
            .setMessage(
                "Version ${release.versionName} of the HomeNetWatch agent is available. This TV has " +
                    "${BuildConfig.VERSION_NAME}.\n\nThe agent keeps its setup and starts again by itself after the update.",
            )
            .setPositiveButton("Update") { _, _ -> startUpdate(release) }
            .setNegativeButton("Later", null)
            .setOnDismissListener { offering = false }
            .show()
    }

    /** Android must allow the app to install apps once; then the download is checked and handed to Android. */
    private fun startUpdate(release: Updater.Release) {
        if (!packageManager.canRequestPackageInstalls()) {
            AlertDialog.Builder(this)
                .setTitle("Allow updates")
                .setMessage("Android needs your OK once before HomeNetWatch can install its updates. On the next screen, turn on HomeNetWatch, then press Back.")
                .setPositiveButton("Open settings") { _, _ ->
                    pendingUpdate = release
                    try {
                        startActivity(Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:$packageName")))
                    } catch (_: Exception) {
                        pendingUpdate = null
                        Updater.status = "Open Settings > Device Preferences > Security & restrictions > Unknown sources, " +
                            "turn on HomeNetWatch, then choose Restart agent."
                    }
                }
                .setNegativeButton("Cancel", null)
                .show()
            return
        }
        Updater.status = "Downloading version ${release.versionName}"
        val context = applicationContext
        Thread {
            try {
                val apk = Updater.download(context, release)
                Updater.status = "Installing version ${release.versionName}"
                Updater.install(context, apk)
            } catch (e: Exception) {
                Updater.status = "Update failed: ${e.message ?: e.javaClass.simpleName}"
            }
        }.start()
    }

    /**
     * Android asks the owner once before an app may make a VPN connection, which the tunnel is. It can only ask
     * from a screen, so the TV asks as soon as the device is approved. After that it starts by itself, also on boot.
     */
    private fun askForVpn() {
        if (!store.enrolled || asking) return
        val consent = VpnService.prepare(this) ?: return
        asking = true
        startActivityForResult(consent, VPN_REQUEST)
    }

    @Deprecated("The platform Activity has no replacement without AndroidX")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == VPN_REQUEST) asking = false
    }

    override fun onPause() {
        handler.removeCallbacks(refresh)
        super.onPause()
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent?): Boolean {
        if (keyCode == KeyEvent.KEYCODE_MENU) {
            confirmStartOver()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    /** Starting over wipes the enrollment, so it asks first: a stray press on the remote must not do it. */
    private fun confirmStartOver() {
        AlertDialog.Builder(this)
            .setTitle("Start over?")
            .setMessage(
                "This device stops reporting and forgets its setup. To use it again, delete it on the dashboard " +
                    "and set it up with the new QR code.",
            )
            .setPositiveButton("Start over") { _, _ ->
                stopService(Intent(this, AgentService::class.java))
                store.reset()
                AgentService.start(this)
                draw()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }

    /** Stops the background agent (its tests and the tunnel) and starts it again a moment later. */
    private fun restartAgent() {
        stopService(Intent(this, AgentService::class.java))
        AgentService.Status.tunnel = "Restarting the agent"
        handler.postDelayed({ AgentService.start(this) }, 1_500)
        checkForUpdate()
    }

    /** Draws the QR code for this link, once per link. Dark modules on white, so any phone camera reads it. */
    private fun showQr(link: String) {
        if (link == qrFor) return
        val matrix = QRCodeWriter().encode(link, BarcodeFormat.QR_CODE, QR_SIZE, QR_SIZE, mapOf(EncodeHintType.MARGIN to 1))
        val pixels = IntArray(matrix.width * matrix.height) { index ->
            if (matrix.get(index % matrix.width, index / matrix.width)) Color.BLACK else Color.WHITE
        }
        qr.setImageBitmap(Bitmap.createBitmap(pixels, matrix.width, matrix.height, Bitmap.Config.ARGB_8888))
        qrFor = link
    }

    /** The agent's state once it is approved: how long it has run and been connected, its tests and speed test. */
    private fun running(): String {
        val status = AgentService.Status
        val now = System.currentTimeMillis()
        if (store.paused) return "Paused. No tests or reports until you choose Resume agent."
        val clock = java.time.format.DateTimeFormatter.ofPattern("h:mm a")
        fun at(ms: Long) = java.time.Instant.ofEpochMilli(ms).atZone(java.time.ZoneId.systemDefault()).format(clock)
        return listOfNotNull(
            if (status.startedAt > 0) "Agent running for ${since(status.startedAt, now)} (since ${at(status.startedAt)})" else "Agent starting",
            if (status.tunnelUpSince > 0) "Tunnel up for ${since(status.tunnelUpSince, now)}" else status.tunnel.ifEmpty { "Starting the tunnel" },
            if (status.connectedSince > 0) {
                "Connected to the server for ${since(status.connectedSince, now)} · last report ${since(status.lastReportAt, now)} ago"
            } else {
                status.health.ifEmpty { "Not connected to the server yet" }
            },
            status.voice?.summary(),
            status.speed?.lastResult?.takeIf { it.isNotEmpty() },
        ).joinToString("\n")
    }

    /** "3 h 12 m", "4 m 05 s", "1 d 2 h": short enough to read across a room. */
    private fun since(then: Long, now: Long): String {
        val seconds = maxOf(0L, (now - then) / 1000)
        val hours = seconds / 3600
        val minutes = (seconds % 3600) / 60
        return when {
            hours >= 24 -> "${hours / 24} d ${hours % 24} h"
            hours > 0 -> "$hours h $minutes m"
            minutes > 0 -> "$minutes m ${"%02d".format(seconds % 60)} s"
            else -> "$seconds s"
        }
    }

    /** Dots that count up once a second while the device waits for the dashboard, so the screen never looks frozen. */
    private fun waitingDots(step: Int): String =
        if (step == Wizard.APPROVE) ".".repeat(((System.currentTimeMillis() / 1000) % 4).toInt()).padEnd(3, ' ') else ""

    private fun draw() {
        val address = Enroller.localIpv4()
        val step = Wizard.show(store)
        device.text = (if (store.deviceName.isEmpty()) "Not named yet" else "Device name: ${store.deviceName}") +
            " · app ${BuildConfig.VERSION_NAME}"
        status.text = if (step == Wizard.DONE) "Setup complete: on the dashboard as ${store.name.ifEmpty { store.deviceName }}"
        else "Step $step of ${Wizard.COUNT}: ${Wizard.title(step)}" + waitingDots(step)
        code.text = if (step == Wizard.DONE) "" else store.ensureInstallerCode()
        val scanning = step < Wizard.APPROVE && address != null
        if (scanning) status.text = "Scan the QR code with your phone to set up this device"
        if (scanning) showQr(Pairing.url(BuildConfig.DASHBOARD_URL, store.ensureInstallerCode(), store.pairingToken()))
        qr.visibility = if (scanning) View.VISIBLE else View.GONE
        controls.visibility = if (step == Wizard.DONE) View.VISIBLE else View.GONE
        // Lines with nothing to say take no room, so the buttons and the hint fit on the screen.
        code.visibility = if (code.text.isNullOrEmpty()) View.GONE else View.VISIBLE
        pauseButton.text = if (store.paused) "Resume agent" else "Pause agent"
        where.text = when {
            step == Wizard.DONE -> running()
            scanning -> "Scan the code with your phone. It opens your dashboard with this device filled in: " +
                "name it, tap Register, then Approve. This TV picks up the rest by itself.\nNo phone camera? Open  http://$address:${SetupServer.PORT}"
            else -> "Finish on your phone or open  http://${address ?: "this device's address"}:${SetupServer.PORT}"
        }
        detail.text = if (step == Wizard.APPROVE) store.lastError.ifEmpty {
            if (store.checkedIn) "Checked in. Choose Approve on the dashboard." else "Contacting the server"
        } else Updater.status
        detail.visibility = if (detail.text.isNullOrEmpty()) View.GONE else View.VISIBLE
    }
}
