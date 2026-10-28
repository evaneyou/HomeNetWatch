"""LAN setup page for a Raspberry Pi remote agent.

Enrollment, health, radio capture, and the test scheduler live in their own modules.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agent_state as state
import health
import radio
import scheduler
import speedtest
from agent_state import AGENT_VERSION, CHECK_IN_PATH, POLL_PATH
from enrollment import (
    certificate_pin,
    check_in_payload,
    enroll_once,
    enrollment_base,
    ensure_enrollment_loop,
    ensure_identity,
    ensure_installer_code,
    ensure_wireguard_keys,
    load_config,
    new_installer_code,
    parse_pins,
    poll_payload,
    post_pinned,
    save_config,
    signed_message,
    tunnel_config_text,
    wireguard_secret_path,
)
from health import lan_ip
from radio import airwave_mode, kismet_get, kismet_once

def setup_allowed(enrolled: bool, host: str, current_key: str, stored_token: str) -> bool:
    """An enrolled agent accepts a LAN change only when the current device key is presented."""
    if not enrolled:
        return True
    if host in {"127.0.0.1", "::1"}:
        return True
    return bool(stored_token) and current_key == stored_token


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Remote Agent setup</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; font: 16px/1.4 system-ui, sans-serif; background: #141414; color: #e4e4e4; }
    main { max-width: 28rem; margin: 3rem auto; padding: 0 1rem; }
    h1 { font-size: 1.4rem; margin: 0 0 0.4rem; }
    p { color: #8a8a8a; }
    label { display: block; margin: 0.9rem 0 0.3rem; color: #8a8a8a; font-size: 0.85rem; }
    input { width: 100%; box-sizing: border-box; padding: 0.55rem 0.7rem; border-radius: 6px;
      border: 1px solid #2a2a2a; background: #181818; color: inherit; }
    button { margin-top: 1rem; padding: 0.55rem 0.9rem; border: 0; border-radius: 6px;
      background: #4c9df3; color: #0d0d0d; font: inherit; }
    .error { color: #e34671; }
    .ok { color: #3fa266; }
    .code { font: 1.25rem/1.4 ui-monospace, monospace; letter-spacing: 0.04em; color: #e4e4e4; }
    textarea { width: 100%; box-sizing: border-box; min-height: 4.5rem; padding: 0.55rem 0.7rem;
      border-radius: 6px; border: 1px solid #2a2a2a; background: #181818; color: inherit; font: inherit; }
  </style>
</head>
<body>
  <main>
    <h1>Remote Agent setup</h1>
    <p id="lead">Register this installer code in Remote Agent Onboarding, then enter the server and its TLS key pins.</p>
    <p id="installer-code" class="code"></p>
    <form id="setup">
      <label for="server">Server</label>
      <input id="server" name="server_url" placeholder="https://home.example" required />
      <label for="pins">TLS key pins</label>
      <textarea id="pins" name="tls_pins" required spellcheck="false" placeholder="sha256/…"></textarea>
      <label id="current-label" for="current" hidden>Current device key</label>
      <input id="current" name="current_key" hidden autocomplete="off" spellcheck="false" />
      <button type="submit">Connect</button>
    </form>
    <button id="replace" type="button" hidden>Update server</button>
    <p id="reset-note" hidden>To start over, run sudo hnw-reset on this Pi. That clears /etc/monitor/config.json.</p>
    <p id="message"></p>
  </main>
  <script>
    const form = document.getElementById("setup");
    const message = document.getElementById("message");
    const lead = document.getElementById("lead");
    const installer = document.getElementById("installer-code");
    const replace = document.getElementById("replace");
    const currentLabel = document.getElementById("current-label");
    const currentKey = document.getElementById("current");
    const resetNote = document.getElementById("reset-note");
    let editing = false;
    replace.addEventListener("click", () => {
      editing = true;
      form.hidden = false;
      replace.hidden = true;
      currentLabel.hidden = false;
      currentKey.hidden = false;
      resetNote.hidden = false;
    });
    async function refresh() {
      const status = await fetch("/api/status").then((r) => r.json());
      installer.hidden = !status.installer_code;
      installer.textContent = status.installer_code || "";
      if (!status.enrolled) {
        if (status.state === "waiting") {
          lead.textContent = "Waiting for approval. The installer code stays here until the server accepts this Pi.";
        }
        message.className = status.last_error ? "error" : "";
        message.textContent = status.last_error || "";
        return;
      }
      if (!editing) {
        form.hidden = true;
        replace.hidden = false;
      }
      lead.textContent = status.name + " is connected to " + status.server_url + ".";
      message.className = status.last_error ? "error" : "ok";
      const mode = status.airwave_mode ? " Airwave capture is " + status.airwave_mode + "." : "";
      message.textContent = status.last_error || (status.last_health_at ? "Last report " + status.last_health_at + "." + mode : "Waiting for the first report." + mode);
    }
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      message.className = "";
      message.textContent = "Connecting…";
      const body = Object.fromEntries(new FormData(form).entries());
      const response = await fetch("/api/setup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        message.className = "error";
        message.textContent = payload.detail || "Could not connect";
        return;
      }
      editing = false;
      await refresh();
    });
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""



class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        try:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            return

    def do_GET(self) -> None:
        if self.path.startswith("/api/status"):
            with state._lock:
                config = ensure_installer_code(load_config())
            try:
                ensure_identity()
            except (OSError, RuntimeError):
                pass
            enrolled = bool(config.get("token")) and str(config.get("server_url") or "").startswith("http://10.7.")
            waiting = bool(config.get("enrollment_url")) and not enrolled
            payload = {
                "enrolled": enrolled,
                "state": "enrolled" if enrolled else "waiting" if waiting else "setup",
                "name": config.get("name") or "",
                "server_url": config.get("server_url") or config.get("enrollment_url") or "",
                "installer_code": "" if enrolled else config.get("installer_code") or "",
                "last_health_at": state._last_health_at,
                "last_error": state._last_error,
                "airwave_mode": airwave_mode(),
            }
            self._send(200, json.dumps(payload).encode("utf-8"), "application/json")
            return
        self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        if not self.path.startswith("/api/setup"):
            self._send(404, b"{}", "application/json")
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = -1
        if length < 0 or length > 65536:
            self._send(400, json.dumps({"detail": "Invalid request"}).encode("utf-8"), "application/json")
            return
        try:
            incoming = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._send(400, json.dumps({"detail": "Invalid request"}).encode("utf-8"), "application/json")
            return
        server_url = str(incoming.get("server_url") or "").strip().rstrip("/")
        current_key = str(incoming.get("current_key") or "")
        with state._lock:
            config = ensure_installer_code(load_config())
        if not setup_allowed(bool(config.get("token")), self.client_address[0], current_key, str(config.get("token") or "")):
            self._send(
                403,
                json.dumps({"detail": "This remote agent is already enrolled. Enter the current device key, or run sudo hnw-reset on the Pi."}).encode("utf-8"),
                "application/json",
            )
            return
        try:
            pins = parse_pins(str(incoming.get("tls_pins") or ""))
            enrollment_base(server_url)
        except ValueError as exc:
            self._send(400, json.dumps({"detail": str(exc)}).encode("utf-8"), "application/json")
            return
        try:
            ensure_identity()
            ensure_wireguard_keys()
        except (OSError, RuntimeError):
            self._send(500, json.dumps({"detail": "Could not prepare the identity key"}).encode("utf-8"), "application/json")
            return
        config["enrollment_url"] = server_url
        config["tls_pins"] = pins
        with state._lock:
            save_config(config)
        ensure_enrollment_loop()
        self._send(200, json.dumps({"ok": True}).encode("utf-8"), "application/json")


def ensure_health_loop() -> None:
    """Start the report, test, scan, capture, and synthetic loops once."""
    with state._lock:
        start_health = not state._health_started
        start_tests = not state._tests_started
        start_scan = not state._scan_started
        start_kismet = not state._kismet_started
        start_synthetic = not state._synthetic_started
        start_speedtest = not state._speedtest_started
        start_tunnel = not getattr(state, "_tunnel_started", False)
        state._health_started = True
        state._tests_started = True
        state._scan_started = True
        state._kismet_started = True
        state._synthetic_started = True
        state._speedtest_started = True
        state._tunnel_started = True
    if start_health:
        threading.Thread(target=health.health_loop, name="health", daemon=True).start()
    if start_tests:
        threading.Thread(target=scheduler.test_loop, name="internet", daemon=True).start()
    if start_scan:
        threading.Thread(target=radio.scan_loop, name="wifi-scan", daemon=True).start()
    if start_kismet:
        threading.Thread(target=radio.kismet_loop, name="kismet", daemon=True).start()
    if start_synthetic:
        threading.Thread(target=scheduler.synthetic_loop, name="synthetic", daemon=True).start()
    if start_tunnel:
        from tunnel_watch import tunnel_loop

        threading.Thread(target=tunnel_loop, name="tunnel-watch", daemon=True).start()
    if start_speedtest:
        def _locked_config() -> dict:
            with state._lock:
                return load_config()

        threading.Thread(
            target=speedtest.poll_loop,
            args=(state._stop, _locked_config, health.get_json, health.post_json),
            name="speedtest",
            daemon=True,
        ).start()


state.ensure_health_loop = ensure_health_loop


def main() -> None:
    config = load_config()
    if config.get("token"):
        state.ensure_health_loop()
    if config.get("enrollment_url") and not (config.get("token") and str(config.get("server_url") or "").startswith("http://10.7.")):
        ensure_enrollment_loop()
    server = ThreadingHTTPServer(("0.0.0.0", state.PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
