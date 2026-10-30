package com.homenetwatch.agent

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.KeyEvent
import android.widget.LinearLayout
import android.widget.TextView

/** One screen for a TV: what the agent is doing, the installer code and where to open the setup page. */
class MainActivity : Activity() {
    private lateinit var store: Store
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var device: TextView
    private lateinit var status: TextView
    private lateinit var code: TextView
    private lateinit var where: TextView
    private lateinit var detail: TextView
    private val refresh = object : Runnable {
        override fun run() {
            draw()
            handler.postDelayed(this, 2000)
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
        setContentView(column)
        AgentService.start(this)
    }

    override fun onResume() {
        super.onResume()
        handler.post(refresh)
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

    private fun draw() {
        val address = Enroller.localIpv4()
        val step = Wizard.show(store)
        device.text = if (store.deviceName.isEmpty()) "Not named yet" else "Device name: ${store.deviceName}"
        status.text = if (step == Wizard.DONE) "Setup complete: on the dashboard as ${store.name.ifEmpty { store.deviceName }}"
        else "Step $step of ${Wizard.COUNT}: ${Wizard.title(step)}"
        code.text = if (step == Wizard.DONE) "" else store.ensureInstallerCode()
        where.text = if (step == Wizard.DONE) "" else
            "Follow the steps on your phone or computer: open  http://${address ?: "this device's address"}:${SetupServer.PORT}"
        detail.text = if (step == Wizard.APPROVE) store.lastError.ifEmpty {
            if (store.checkedIn) "Checked in. Choose Approve on the dashboard." else ""
        } else ""
    }
}
