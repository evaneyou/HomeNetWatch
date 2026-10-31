package com.homenetwatch.agent

/** Saving the device's name and setup key, whether they come from the setup page or from the server. */
object Setup {
    fun name(store: Store, raw: String) {
        val name = raw.trim().replace(Regex("\\s+"), " ")
        require(name.isNotEmpty()) { "Give this device a name, for example Living Room Shield." }
        require(name.length <= 128) { "The name can be up to 128 characters." }
        store.deviceName = name
    }

    fun key(store: Store, raw: String) {
        val key = Wire.parseSetupKey(raw)
        store.serverUrl = key.serverUrl
        store.pins = key.pins.joinToString(" ")
        store.state = "waiting_for_approval"
        store.checkedIn = false
        store.lastError = ""
        store.keySavedAt = System.currentTimeMillis()
    }
}
