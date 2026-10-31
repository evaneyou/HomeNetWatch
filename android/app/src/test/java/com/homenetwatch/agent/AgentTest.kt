package com.homenetwatch.agent

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class AgentTest {
    // The server's voip-g711a profile options (server/app/synthetic.py PROFILES).
    private val g711 = JSONObject(
        """{"preset":"voip-g711a","wire":"rtp","rtp":"g711a","ptime_ms":20,"bidir":true,"rtcp_interval_s":5,
           "report_interval_s":10,"max_rtt_ms":300,"max_jitter_ms":30,"max_loss_pct":1}""",
    )

    @Test
    fun voiceStreamArgsMatchThePis() {
        val args = Voice.sendArgs("/lib/librtnp_send.so", g711, "203.0.113.7", "/k", "/s", forever = true)
        // Same order and values as monitor/synthetic.py send_args, with the control channel on the one tunnel.
        assertEquals(
            listOf(
                "/lib/librtnp_send.so", "flow", "-t", "203.0.113.7:8998", "-c", "10.7.0.1:8999", "--preset", "voip-g711a",
                "--json", "-i", "10s", "--key-file", "/k", "--max-rtt", "300", "--max-jitter", "30", "--max-loss", "1",
                "--wire", "rtp", "--srtp-key-file", "/s", "--rtp", "g711a", "--ptime", "20", "--bidir",
                "--rtcp-interval", "5s", "--forever",
            ),
            args,
        )
    }

    @Test
    fun ulawVoiceRunsBothWays() {
        // The server's voip-g711 profile: G.711 u-law, payload type 0, two-way like A-law.
        val ulaw = JSONObject(g711.toString()).put("preset", "voip-g711").put("rtp", "g711")
        val args = Voice.sendArgs("/b", ulaw, "203.0.113.7", "/k", "/s", forever = true)
        assertEquals("voip-g711", args[args.indexOf("--preset") + 1])
        assertTrue("--bidir" in args)
    }

    @Test
    fun flowsWithoutRtpMeasureARealRoundTrip() {
        // remote-desktop is plain UDP: one-way delay would compare the TV's clock with the server's.
        val rdp = JSONObject("""{"preset":"remote-desktop","report_interval_s":10,"max_rtt_ms":150,"max_jitter_ms":30,"max_loss_pct":1}""")
        val args = Voice.sendArgs("/b", rdp, "203.0.113.7", "/k", "/s", forever = true)
        assertEquals(listOf("--meter", "rtt"), args.subList(args.indexOf("--meter"), args.indexOf("--meter") + 2))
        // RTP flows already get a round trip from RTCP and keep their arguments.
        assertTrue("--meter" !in Voice.sendArgs("/b", g711, "203.0.113.7", "/k", "/s", forever = true))
    }

    @Test
    fun voiceTrafficNeverGoesToTheTunnelOrAFile() {
        for ((host, options) in listOf(
            "10.7.0.1" to g711,
            "203.0.113.7" to JSONObject(g711.toString()).put("payload", "file:/etc/passwd"),
            "203.0.113.7" to JSONObject(g711.toString()).put("preset", "flood"),
            "203.0.113.7" to JSONObject(g711.toString()).put("rate_pps", 5000),
        )) {
            try {
                Voice.sendArgs("/b", options, host, "/k", "/s", forever = true)
                fail("expected a refusal for $host $options")
            } catch (_: IllegalArgumentException) {
            }
        }
    }

    @Test
    fun aTimedRunNeedsADuration() {
        val args = Voice.sendArgs("/b", g711, "203.0.113.7", "/k", "/s", forever = false, durationSeconds = 60)
        assertEquals(listOf("-d", "60s"), args.takeLast(2))
        try {
            Voice.sendArgs("/b", g711, "203.0.113.7", "/k", "/s", forever = false, durationSeconds = 0)
            fail("expected a refusal")
        } catch (_: IllegalArgumentException) {
        }
    }

    @Test
    fun quietFlowsAreSpottedLikeOnThePi() {
        assertEquals("missing", Voice.flowState(JSONObject("""{"sent":500}""")))
        assertEquals("lost", Voice.flowState(JSONObject("""{"rx":0,"loss_pct":100.0}""")))
        assertEquals("", Voice.flowState(JSONObject("""{"rx":498,"loss_pct":0.4}""")))
    }

    @Test
    fun stressJobsAndUnknownLinksAreLeftOut() {
        val jobs = JSONArray(
            """[{"run_id":"a","link":"wireless","mode":"continuous"},{"run_id":"b","link":"wired","mode":"stress"},
               {"run_id":"c","link":"lte","mode":"manual"}]""",
        )
        assertEquals(setOf("a" to "wireless"), Voice.wantedJobs(jobs).keys)
    }

    @Test
    fun tunnelConfigOnlyRoutesTheServer() {
        val tunnel = JSONObject(
            """{"link":"wired","address":"10.7.0.3","server_public_key":"SERVERKEY=","endpoint":"203.0.113.7:51820",
               "allowed_ips":"10.7.0.1/32","persistent_keepalive":25,"preshared_key":"PSK="}""",
        )
        val text = TunnelManager.configText(tunnel, "PRIVATE=")
        assertTrue(text.contains("Address = 10.7.0.3/32\n"))
        assertTrue(text.contains("AllowedIPs = 10.7.0.1/32\n"))
        assertTrue(text.contains("PresharedKey = PSK=\n"))
        // Once the server has seen a handshake it stops sending the preshared key; the saved one is used then.
        assertTrue(!TunnelManager.configText(tunnel.put("preshared_key", JSONObject.NULL), "P").contains("PresharedKey"))
    }

    @Test
    fun theNameHeaderIsEncodedLikeThePis() {
        assertEquals("Living%20Room%20Shield", Api.nameHeader("Living Room Shield"))
    }
}
