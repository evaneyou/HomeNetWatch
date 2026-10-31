package com.homenetwatch.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class PairingTest {
    @Test
    fun theQrCodeOpensTheOnboardingPageWithTheCodeAndTokenOnly() {
        val link = Pairing.url("https://live.example.com/", "BWTD-NEP5-RQHH", "tok_123-abc")
        assertEquals("https://live.example.com/settings/onboarding?pair=android&code=BWTD-NEP5-RQHH&t=tok_123-abc", link)
        // The device's own address is not in it: the phone never has to reach the device.
        assertFalse(link.contains("device="))
    }
}
