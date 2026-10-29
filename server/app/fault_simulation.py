"""Prompt-based Fault Simulation: explain what HomeNetWatch would do for a described failure.

Does not inject readings, mutate monitors, or send Telegram. Uses the Gemini key from
Settings and a digest of production alert / Overview / tunnel_watch rules.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app import gemini_rca

MAX_PROMPT_CHARS = 4000

BEHAVIOR_CONTEXT = """
HomeNetWatch behavior digest (authoritative for Fault Simulation). Reason only from this.

## Architecture
- Raspberry Pi agent pushes results to the FastAPI server. Dashboard reads the server only.
- Each sensor has Wired (eth) and Wi-Fi (wlan) physical links.
- Access plane: public web HTTP checks (http-google, http-cloudflare, http-youtube, …) leave
  through the NIC to the open internet. They do NOT need WireGuard.
- Measurement tunnel: icmp-path / http-path target WireGuard peers 10.7.0.1 (wired/wg0) and
  10.7.1.1 (wifi/wg1). Synthetic app flows (voice, video, remote-desktop) use the same tunnel.
- LibreSpeed bandwidth tests run on wired and wifi networks.
- Server refreshes WireGuard handshake times from the hub; stale handshake ≥ ~10 minutes while
  the Pi is still checking in means the measurement tunnel is dead.

## Overview matrix (GET /api/v1/summary)
Columns per link: internet (path + librespeed), web (public HTTP quorum), apps (synthetic).
Diagnosis cause labels (monitor and/or cell):
- agent — probe offline / not reporting
- tunnel — public web OK (or access healthy) but tunnel-backed path/apps fail or handshake stale
- bandwidth — librespeed collapse (often both links)
- wan — both links internet/web/apps bad (shared upstream ISP)
- server — web OK both links but path/apps bad (receiver/server-side narrative)
- wifi / wired — single-link degradation while sibling healthy
- service — residual per-service web/apps fault

## Telegram alert evaluation (alerts.evaluate, ~every 60s)
Order: resolve stale → refresh WG handshakes → pi_conditions → network_conditions →
server_conditions → app_conditions.

### Pi / tunnel conditions
- pi-offline:{monitor_id} — no last_seen_at within ~3× health interval (min 5m)
- tunnel-down:{monitor_id}:wired|wireless — handshake stale, Pi still online
- pi-temp, pi-disk, pi-error — from latest MonitorHealth

### Network hierarchy (network_conditions)
1. net-down:{monitor}:wan — BOTH links path-down AND no recent public web on the link
2. net-bandwidth:{monitor}:wan — librespeed bad on both links, OR one link BW bad + sibling
   path degraded / ≥2 failing synthetic flows
3. net-degraded:{monitor}:wan — both links path degraded (not fully down)
4. Per-link: net-down, net-bandwidth, net-degraded, net-services (≥40% of ≥3 web services failing)
5. Tunnel suppression: stale tunnel + access_ok_recent (public web OK) → skip net faults on that
   link; tunnel is handled under pi tunnel-down instead
6. airwave:{issue_id} — open RF / WIDS security issues

### App conditions
- app:{run_id}:{link} — last 3 continuous synthetic samples fail within 15m
- Suppressed when a link/WAN net alert is open, or tunnel-down with web OK

### Server conditions
- server-disk, server-db-pool, server-errors

## KPI
- Internet availability / outage incidents ignore path fails when public web on that link still
  succeeds (tunnel death ≠ home internet down).
- Software synthetic blackouts (no packets while path/web still OK) are Issues, left out of KPI.

## Agent self-heal (tunnel_watch, agent 0.8.6+)
- Every ~30s ping tunnel peers 10.7.0.1 / 10.7.1.1.
- After 2 consecutive fails, run restore_tunnel.sh (full wg-quick stop/delete/start) for wg0/wg1.
- Cooldown while access plane also down: 15 minutes (avoid thrashing against empty gateway).
- Cooldown once public access on that link recovers (ping 1.1.1.1/8.8.8.8 via physical NIC):
  ~60 seconds so PostUp re-pins the current default gateway.
- Restores during an ISP outage can leave a stale route pin; after ISP returns, recovery cooldown
  should bring the measurement tunnel back within ~1–2 minutes without operator action.
- Probe restart also bounces wg0/wg1.
- Agent notes go to SystemLog via health check-in (subsystem often "tunnel").

## Typical scenario → outcome map
- Both links: path fail + public web fail → wan / net-down:wan; Overview cause wan; no tunnel alert
  as primary.
- Both links: librespeed collapse (esp. upload) → net-bandwidth:wan; apps may fail but app alerts
  suppressed under bandwidth hierarchy; Overview cause bandwidth.
- Wi-Fi only: public web OK, path/apps fail, handshake stale → tunnel-down:wireless; Overview
  cause tunnel; net-down:wifi suppressed; apps may be suppressed.
- Wi-Fi radio/AP down: public web fail + path fail on wifi, wired healthy → wifi / net-down:wifi.
- Pi powered off → pi-offline; matrix agent; no tunnel-down (Pi not online).
- ISP restored but wg1 still stale → access OK + tunnel fails → recovery restore ~60s; until then
  Overview still shows tunnel on wireless.
"""

SYSTEM_PROMPT = f"""You are a HomeNetWatch Fault Simulation engine. The operator describes a
hypothetical failure. You explain in detail what the REAL product would detect, show, alert,
log, and self-heal — based ONLY on the behavior digest below. Do not invent alert keys,
endpoints, cause labels, or cooldowns that are not in the digest. If the prompt is ambiguous,
state explicit assumptions in interpretation.

This is explanatory only: no live data is injected and no Telegram messages are sent.

BEHAVIOR DIGEST:
{BEHAVIOR_CONTEXT}

Respond ONLY with a valid JSON object matching this exact schema:
{{
  "assumptions": ["Short assumption you made because the prompt was incomplete"],
  "interpretation": "2-4 sentences: how you map the prompt onto access plane vs tunnel plane and which links/sensors are involved",
  "detection": [
    "Concrete signal the system would see (ingest test slug, handshake field, health last_error, synthetic verdict, etc.)"
  ],
  "overview": {{
    "matrix": "What Overview matrix cells would look like (internet / web / apps per Wired and Wi-Fi)",
    "cause": "Primary diagnosis cause label from the digest (agent|tunnel|bandwidth|wan|server|wifi|wired|service) or unknown",
    "detail": "Why that cause wins over alternatives"
  }},
  "alerts": {{
    "fires": ["Exact condition key patterns that would open, e.g. tunnel-down:{{id}}:wireless"],
    "suppresses": ["What is suppressed and why (hierarchy)"]
  }},
  "logs": [
    "Expected SystemLog / agent last_error / tunnel_watch note content"
  ],
  "self_heal": "What tunnel_watch or other automatic recovery would do, including cooldowns; or 'none' if not applicable",
  "will_not_happen": [
    "Explicit non-fires or mistaken narratives the operator might expect but the code will not do"
  ]
}}
"""


def simulate_fault(db: Session, prompt: str) -> dict[str, Any]:
    text = (prompt or "").strip()
    if not text:
        raise gemini_rca.GeminiError("Describe a failure scenario to simulate.")
    if len(text) > MAX_PROMPT_CHARS:
        raise gemini_rca.GeminiError(f"Prompt is too long (max {MAX_PROMPT_CHARS} characters).")

    row = gemini_rca.get_settings_row(db)
    api_key = gemini_rca.resolve_api_key(row)
    if not api_key:
        raise gemini_rca.GeminiError(
            "Gemini API key is not configured. Add your key in Settings → Configure Gemini AI "
            "(the same key used by the KPI Dashboard root-cause tool)."
        )

    user_payload = json.dumps(
        {
            "mode": "fault_simulation",
            "scenario_prompt": text,
            "instruction": "Walk through detection, Overview, alerts, logs, self-heal, and non-fires.",
        },
        default=str,
    )
    now = datetime.now(timezone.utc)
    try:
        result = gemini_rca.call_gemini(
            api_key=api_key,
            model=row.model or gemini_rca.DEFAULT_MODEL,
            system_prompt=SYSTEM_PROMPT,
            user_payload=user_payload,
            json_mode=True,
        )
    except gemini_rca.GeminiError as exc:
        row.last_error = str(exc)[:300]
        db.commit()
        raise

    row.last_ok_at = now
    row.last_error = ""
    db.commit()

    overview = result.get("overview") if isinstance(result.get("overview"), dict) else {}
    alerts = result.get("alerts") if isinstance(result.get("alerts"), dict) else {}

    return {
        "generated_at": now.isoformat(),
        "model": row.model or gemini_rca.DEFAULT_MODEL,
        "prompt": text,
        "assumptions": [str(x) for x in (result.get("assumptions") or []) if x],
        "interpretation": str(result.get("interpretation") or ""),
        "detection": [str(x) for x in (result.get("detection") or []) if x],
        "overview": {
            "matrix": str(overview.get("matrix") or ""),
            "cause": str(overview.get("cause") or "unknown"),
            "detail": str(overview.get("detail") or ""),
        },
        "alerts": {
            "fires": [str(x) for x in (alerts.get("fires") or []) if x],
            "suppresses": [str(x) for x in (alerts.get("suppresses") or []) if x],
        },
        "logs": [str(x) for x in (result.get("logs") or []) if x],
        "self_heal": str(result.get("self_heal") or "none"),
        "will_not_happen": [str(x) for x in (result.get("will_not_happen") or []) if x],
    }
