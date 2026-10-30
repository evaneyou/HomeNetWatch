package com.homenetwatch.agent

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * Starts the agent when the device boots, after a power cut, and after the app is updated, so it never waits for
 * someone to open it. Android lets a boot broadcast start a foreground service.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED || intent.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            AgentService.start(context)
        }
    }
}
