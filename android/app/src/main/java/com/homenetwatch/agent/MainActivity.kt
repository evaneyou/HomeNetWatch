package com.homenetwatch.agent

import android.app.Activity
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Typeface
import android.net.VpnService
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
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
    private var qrFor = ""
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
        column.addView(text(34f, "#E8ECF8", true).apply { this.text = "HomeNetWatch agent" })
        device = text(20f, "#C3CCE6").also { column.addView(it) }
        status = text(24f, "#4FD1B5", true).also { column.addView(it) }
        code = text(44f, "#E8ECF8", true).apply { typeface = Typeface.MONOSPACE }.also { column.addView(it) }
        where = text(22f, "#C3CCE6").also { column.addView(it) }
        detail = text(18f, "#9AA6C4").also { column.addView(it) }
        column.addView(text(16f, "#9AA6C4").apply { this.text = "Press the Menu button to start over with a new installer code." })
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
        setContentView(row)
        AgentService.start(this)
    }

    override fun onResume() {
        super.onResume()
        handler.post(refresh)
        askForVpn()
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
            store.reset()
            draw()
            return true
        }
        return super.onKeyDown(keyCode, event)
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

    /** The agent's state once it is approved: tunnel, health report, voice stream and the last speed test. */
    private fun running(): String {
        val status = AgentService.Status
        return listOfNotNull(
            status.tunnel.ifEmpty { "Starting the tunnel" },
            status.health.takeIf { it.isNotEmpty() },
            status.voice?.summary(),
            status.speed?.lastResult?.takeIf { it.isNotEmpty() },
        ).joinToString("\n")
    }

    /** Dots that count up once a second while the device waits for the dashboard, so the screen never looks frozen. */
    private fun waitingDots(step: Int): String =
        if (step == Wizard.APPROVE) ".".repeat(((System.currentTimeMillis() / 1000) % 4).toInt()).padEnd(3, ' ') else ""

    private fun draw() {
        val address = Enroller.localIpv4()
        val step = Wizard.show(store)
        device.text = if (store.deviceName.isEmpty()) "Not named yet" else "Device name: ${store.deviceName}"
        status.text = if (step == Wizard.DONE) "Setup complete: on the dashboard as ${store.name.ifEmpty { store.deviceName }}"
        else "Step $step of ${Wizard.COUNT}: ${Wizard.title(step)}" + waitingDots(step)
        code.text = if (step == Wizard.DONE) "" else store.ensureInstallerCode()
        val scanning = step < Wizard.APPROVE && address != null
        if (scanning) status.text = "Scan the QR code with your phone to set up this device"
        if (scanning) showQr(Pairing.url(BuildConfig.DASHBOARD_URL, store.ensureInstallerCode(), Pairing.deviceBase(address!!, SetupServer.PORT), store.pairingToken()))
        qr.visibility = if (scanning) android.view.View.VISIBLE else android.view.View.GONE
        where.text = when {
            step == Wizard.DONE -> running()
            scanning -> "Scan the code with your phone. It opens your dashboard with this device filled in: " +
                "register it, tap Send setup key, then Approve.\nNo phone camera? Open  http://$address:${SetupServer.PORT}"
            else -> "Finish on your phone or open  http://${address ?: "this device's address"}:${SetupServer.PORT}"
        }
        detail.text = if (step == Wizard.APPROVE) store.lastError.ifEmpty {
            if (store.checkedIn) "Checked in. Choose Approve on the dashboard." else "Contacting the server"
        } else ""
    }
}
