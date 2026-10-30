package com.homenetwatch.agent

/** The four setup steps, shared by the phone setup page and the TV screen so both always agree. */
object Wizard {
    const val NAME = 1
    const val REGISTER = 2
    const val KEY = 3
    const val APPROVE = 4
    const val DONE = 5
    const val COUNT = 4

    fun title(step: Int): String = when (step) {
        NAME -> "Name this device"
        REGISTER -> "Register this code on the dashboard"
        KEY -> "Paste the setup key"
        APPROVE -> "Approve it on the dashboard"
        else -> "All set"
    }

    /** The furthest step the owner can be on. A step cannot be skipped: each one needs what the one before saved. */
    fun furthest(hasName: Boolean, hasKey: Boolean, approved: Boolean): Int = when {
        approved -> DONE
        !hasName -> NAME
        !hasKey -> KEY
        else -> APPROVE
    }

    /**
     * The step to show. Registering the code happens on the dashboard, so the device cannot tell when it is done:
     * without a setup key the page starts on Register and the owner moves on to the key themselves.
     */
    fun show(requested: Int?, hasName: Boolean, hasKey: Boolean, approved: Boolean): Int {
        val furthest = furthest(hasName, hasKey, approved)
        if (furthest == DONE) return DONE
        val default = if (furthest == KEY) REGISTER else furthest
        return (requested ?: default).coerceIn(NAME, furthest)
    }

    fun show(store: Store, requested: Int? = null): Int =
        show(requested, store.deviceName.isNotEmpty(), store.serverUrl.isNotEmpty(), store.enrolled)
}
