import { Link } from "react-router-dom";

export default function AboutPage() {
  return (
    <div>
      <h1 className="page-title">About</h1>
      <p className="page-sub">HomeNetWatch v0.4</p>

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
          The current release is <span className="mono">v0.4</span>, dated 20 October 2026. The
          initial nine-day alpha build concluded with release <span className="mono">v0.2</span> on
          14 October 2026, following the first release, <span className="mono">v0.1</span>, the day
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
        Notable changes to HomeNetWatch. The current version is <span className="mono">v0.4</span>.
      </p>

      <section className="panel">
        <h2>
          v0.4 <span className="muted">20 October 2026</span>
        </h2>

        <ul className="about-list">
          <li>The system now watches itself. One page shows how hard the server is working, how full its disk is, and when it would run out of space.</li>
          <li>It also checks the server's software for known security flaws and says which ones have a fix ready.</li>
          <li>A test that fails while the connection is clearly still working is treated as a software glitch, kept out of the performance report, and restarted on its own.</li>
        </ul>
      </section>

      <section className="panel">
        <h2>
          v0.3 <span className="muted">18 October 2026</span>
        </h2>

        <ul className="about-list">
          <li>A one-page report of how the network has performed, by day, week, month or year, and it can be exported to a spreadsheet.</li>
          <li>A 30-second speed test you can watch as it runs, over the wired connection or Wi-Fi.</li>
          <li>A way to put the connection under load, with several kinds of traffic at once, and watch how it copes.</li>
          <li>Alerts that arrive on your phone the moment something goes wrong, and again when it recovers.</li>
          <li>A view of who has visited the guest network, and where they came from.</li>
        </ul>
      </section>

      <section className="panel">
        <h2>
          v0.2 <span className="muted">14 October 2026</span>
        </h2>

        <ul className="about-list">
          <li>The route from home to any website, drawn step by step, so you can see exactly where a connection slows down.</li>
          <li>The whole view works on a phone.</li>
          <li>Guests can be given view-only access, so someone can look without being able to change anything.</li>
        </ul>
      </section>

      <section className="panel">
        <h2>
          v0.1 <span className="muted">13 October 2026</span>
        </h2>
        <ul className="about-list">
          <li>A small device at home measures the connection around the clock and reports back to a central dashboard.</li>
          <li>The dashboard shows at a glance what is healthy, what is struggling, and where along the path a problem begins.</li>
          <li>It watches the Wi-Fi too: the nearby networks, the trusted access points, and the devices using them.</li>
        </ul>
      </section>

      <Link to="/about">Back to About</Link>
    </div>
  );
}
