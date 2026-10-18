import { Link } from "react-router-dom";

export default function AboutPage() {
  return (
    <div>
      <h1 className="page-title">About</h1>
      <p className="page-sub">HomeNetWatch 0.3</p>

      <section className="panel">
        <h2>Overview</h2>
        <p>
          HomeNetWatch monitors the wired and wireless performance of a home network. A remote
          agent on a Raspberry Pi runs connectivity tests from inside the home and sends the
          results to a central server. This dashboard reads those results: what is healthy, what
          is degraded, and where along the path a problem starts.
        </p>
        <p>
          The agent only makes outbound connections. The dashboard talks to the server and never
          to the Pi. Checks cover DNS, TCP, TLS, ping, path traces, file transfer, web services,
          and Wi-Fi scans. An admin can change settings and approve devices. A viewer is
          read-only.
        </p>
      </section>

      <section className="panel">
        <h2>Version history</h2>
        <p>
          The current release is <span className="mono">0.3</span>, dated 18 October 2026. The
          initial nine-day alpha build concluded with release <span className="mono">0.2</span> on
          14 October 2026, following the first release, <span className="mono">0.1</span>, the day
          before. HomeNetWatch follows semantic versioning and is still pre-1.0.
        </p>
        <Link to="/about/history">Open version history</Link>
      </section>

      <section className="panel">
        <h2>Copyright and author</h2>
        <p>Copyright (c) 2026 Evan You.</p>
        <p>
          Evan You is the author of HomeNetWatch. The project is released under the MIT License,
          which allows use, copying, and modification as long as the copyright notice and license
          stay with the software. It is provided as is, without warranty.
        </p>
        <p>
          <a href="https://github.com/evaneyou/homenetwatch">github.com/evaneyou/homenetwatch</a>
        </p>
      </section>
    </div>
  );
}

export function VersionHistoryPage() {
  return (
    <div>
      <h1 className="page-title">Version history</h1>
      <p className="page-sub">
        Notable changes to HomeNetWatch. The current version is <span className="mono">0.3</span>.
      </p>

      <section className="panel">
        <h2>
          0.3 <span className="muted">18 October 2026</span>
        </h2>

        <h3 className="wifi-heading">Added</h3>
        <ul className="about-list">
          <li>Home overview: an Apps chip for real-time application flows (voice, video), with Wi-Fi shown first, debounced path health, and offline probe detection on the face orb.</li>
          <li>Speed Test: a live 30-second speed test on the Pi over Wired or Wi-Fi, with an animated path diagram and real-time download and upload bar graphs.</li>
          <li>KPI Dashboard and daily history: executive reporting of availability, voice/video quality, packet drop, latency, and speed across day, week, month, and year views, with CSV export.</li>
          <li>Telegram alerts: real-time push notifications for network outages, ISP link degradation, Wi-Fi security alerts, Pi health, server issues, and application checks, with recovery notices.</li>
          <li>Network Test (stress run): multi-session synthetic load testing (voice, video, remote desktop, market data, round-trip probe, throughput flood) with live per-link charts.</li>
          <li>Airwaves redesign: compact phone-first layout, one-line band stats, channel graph, and Kismet memory caps with daily restarts.</li>
          <li>Guest visits dashboard: 7-day to 1-year visitor analytics with GeoIP country and city breakdown.</li>
        </ul>

        <h3 className="wifi-heading">Changed</h3>
        <ul className="about-list">
          <li>Guest sessions mask each character of identifying fields with <code>#</code> while keeping measurements visible.</li>
          <li>Hardened sign-in rate limiting, server-side DNS validation for traces, CSRF coverage, and Pi agent policy routing and Wi-Fi power management.</li>
        </ul>
      </section>

      <section className="panel">
        <h2>
          0.2 <span className="muted">14 October 2026</span>
        </h2>

        <h3 className="wifi-heading">Added</h3>
        <ul className="about-list">
          <li>Network Path Diagram: an animated diagram of the path each test takes, with hover details and zoom.</li>
          <li>Path View as React Flow diagrams that fit a phone, with the latency chart and hop detail folded into the path.</li>
          <li>Trace any host or public IP on demand from Path View (admin only).</li>
          <li>Zoom on the real-time application health charts.</li>
          <li>The agent reports its network interface names and LAN address.</li>
        </ul>

        <h3 className="wifi-heading">Changed</h3>
        <ul className="about-list">
          <li>A compact layout for Path View and the remote agent pages, with the time range on the tab row.</li>
          <li>File Transfer is now Bandwidth Test, and Home shows Web first.</li>
        </ul>

        <h3 className="wifi-heading">Removed</h3>
        <ul className="about-list">
          <li>draw.io, the web-service MTR checks, and the Video call check.</li>
        </ul>
      </section>

      <section className="panel">
        <h2>
          0.1 <span className="muted">13 October 2026</span>
        </h2>
        <p>First release.</p>

        <h3 className="wifi-heading">Added</h3>
        <ul className="about-list">
          <li>Server: FastAPI and Postgres, cookie auth, enrollment with a pinned TLS key, WireGuard tunnels, and retention of raw results.</li>
          <li>Dashboard: overview, remote-agent detail, internet path, airwaves, issues, and settings.</li>
          <li>Pi agent: LAN setup page, device health, and HTTP, ping, MTR, and Bandwidth Test checks.</li>
          <li>Bandwidth Test uses a LibreSpeed backend.</li>
          <li>Airwaves: Wi-Fi scans and Kismet capture, with trusted access points and nearby clients.</li>
        </ul>

        <h3 className="wifi-heading">Fixed</h3>
        <ul className="about-list">
          <li>A path that stays slower, when the latest samples agree, is treated as the new normal instead of remaining marked slow.</li>
          <li>A silent last hop on a path trace no longer turns the home tiles red by itself.</li>
        </ul>

        <h3 className="wifi-heading">Removed</h3>
        <ul className="about-list">
          <li>iperf3 bandwidth checks. Bandwidth Test is the throughput check.</li>
        </ul>
      </section>

      <Link to="/about">Back to About</Link>
    </div>
  );
}
