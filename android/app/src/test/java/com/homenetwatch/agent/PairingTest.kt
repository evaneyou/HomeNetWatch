package com.homenetwatch.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PairingTest {
    private val dashboard = "https://live.example.com"

    @Test
    fun theQrCodeOpensTheOnboardingPageWithThisDeviceFilledIn() {
        val link = Pairing.url(dashboard, "BWTD-NEP5-RQHH", Pairing.deviceBase("192.168.1.50", 8080), "tok_123")
        assertEquals(
            "https://live.example.com/settings/onboarding?pair=android&code=BWTD-NEP5-RQHH" +
                "&device=http%3A%2F%2F192.168.1.50%3A8080&t=tok_123",
            link,
        )
        // The dashboard reads it back unchanged.
        val query = Pairing.query(link)
        assertEquals("http://192.168.1.50:8080", query["device"])
        assertEquals("BWTD-NEP5-RQHH", query["code"])
    }

    @Test
    fun theReceiveLinkKeepsSpacesAndPlusSigns() {
        val query = Pairing.query("/receive?name=Living%20Room%20Shield&key=hnw1.abc%2Bdef&t=x")
        assertEquals("Living Room Shield", query["name"])
        assertEquals("hnw1.abc+def", query["key"])
    }

    @Test
    fun thePhoneIsOnlySentBackToThisAppsDashboard() {
        assertEquals("$dashboard/settings/onboarding?sent=1", Pairing.safeReturn("$dashboard/settings/onboarding?sent=1", dashboard))
        assertNull(Pairing.safeReturn("https://evil.example/settings", dashboard))
        assertNull(Pairing.safeReturn("https://live.example.com.evil.example/x", dashboard))
        assertNull(Pairing.safeReturn(null, dashboard))
    }
}
