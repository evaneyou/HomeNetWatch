"""Google Gemini LLM Root Cause Analysis for the KPI Dashboard.

Compiles a chronological, privacy-safe event and metric timeline for the requested KPI window
(plus a 24h baseline lead-in) and asks Gemini to determine when the issue started, what the
primary root cause is, and how upstream network/bandwidth conditions correlate with downstream
application health.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app import kpi, telegram
from app.config import get_settings
from app.models import (
    AlertEvent,
    GeminiSetting,
    Monitor,
    Network,
    NetworkKind,
    ResultStatus,
    SyntheticRun,
    SyntheticSample,
    Test,
    TestResult,
)

for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
EASTERN = ZoneInfo("America/New_York")
DEFAULT_MODEL = "gemini-2.5-flash"
ALLOWED_MODELS = ("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.0-flash")
# Older AI Studio keys: AIzaSy… (no dots). Newer auth keys (May 2026+): AQ.… with dots.
_KEY_RE = re.compile(r"^(?:AIza[A-Za-z0-9_-]{20,80}|AQ\.[A-Za-z0-9._-]{20,200})$")


class GeminiError(Exception):
    """Safe error message that never includes the API key."""


def valid_key(key: str) -> bool:
    return bool(_KEY_RE.fullmatch(key.strip()))


def get_settings_row(db: Session) -> GeminiSetting:
    row = db.get(GeminiSetting, 1)
    if row is None:
        row = GeminiSetting(id=1, model=DEFAULT_MODEL)
        db.add(row)
        db.flush()
    return row


def resolve_api_key(row: GeminiSetting) -> str:
    stored = telegram.decrypt(row.api_key_enc)
    if stored:
        return stored
    env_key = (get_settings().gemini_api_key or "").strip()
    return env_key


def is_configured(row: GeminiSetting) -> bool:
    return bool(resolve_api_key(row))


def _et(value: Optional[datetime]) -> str:
    if value is None:
        return ""
    aware = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(EASTERN).strftime("%Y-%m-%d %H:%M ET")


def _client() -> httpx.Client:
    return httpx.Client(timeout=35.0)


def _reason_from_status(status: int, body: dict[str, Any]) -> str:
    err = body.get("error") if isinstance(body, dict) else {}
    msg = str((err or {}).get("message") or "")
    lower = msg.lower()
    if status in (400, 401, 403) and ("api key" in lower or "permission" in lower or "unauthenticated" in lower):
        return "Gemini rejected the API key. Check that the key from Google AI Studio is active."
    if status == 429:
        return "Gemini rate limit or quota reached. Wait a moment and try again."
    if status == 404:
        return "The selected Gemini model was not found."
    if msg:
        # Strip any accidental key echo
        clean = re.sub(r"AIza[A-Za-z0-9_-]+", "[redacted]", msg)
        return f"Gemini error ({status}): {clean[:220]}"
    return f"Gemini returned an error ({status})."


def call_gemini(api_key: str, model: str, system_prompt: str, user_payload: str, json_mode: bool = True) -> dict[str, Any]:
    """Call Google Gemini generateContent endpoint and parse the JSON response."""
    chosen = model if model in ALLOWED_MODELS else DEFAULT_MODEL
    url = f"{API_BASE}/{chosen}:generateContent"
    gen_cfg: dict[str, Any] = {"temperature": 0.2}
    if json_mode:
        gen_cfg["responseMimeType"] = "application/json"
    payload = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_payload}]}],
        "generationConfig": gen_cfg,
    }
    try:
        with _client() as client:
            response = client.post(
                url,
                headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError:
        raise GeminiError("Could not reach the Google Gemini API.") from None

    try:
        body = response.json()
    except ValueError:
        body = {}

    if response.status_code != 200:
        raise GeminiError(_reason_from_status(response.status_code, body if isinstance(body, dict) else {}))

    candidates = (body or {}).get("candidates") or []
    if not candidates:
        raise GeminiError("Gemini returned an empty response.")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    text = "".join(str(p.get("text") or "") for p in parts).strip()
    if not text:
        raise GeminiError("Gemini returned no analysis text.")
    if not json_mode:
        return {"text": text}
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    return {
        "started_at": "Unknown",
        "root_cause": "Analysis completed",
        "confidence": "medium",
        "summary": text[:1500],
        "timeline": [],
        "evidence": [],
        "recommendations": [],
    }


def verify_key(api_key: str, model: str = DEFAULT_MODEL) -> None:
    """Verify the Gemini API key with a minimal request."""
    call_gemini(
        api_key=api_key,
        model=model,
        system_prompt="Reply with JSON {\"ok\": true}.",
        user_payload="ping",
        json_mode=True,
    )


def _to_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def build_event_context(
    db: Session,
    period: str,
    day: Optional[date] = None,
    monitor_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    """Assemble the KPI report, bandwidth test log, synthetic application transitions, and path/alert events."""
    kpi_report = kpi.report(db, period, monitor_id=monitor_id, day=day)
    start_dt = _to_dt(kpi_report["from"])
    end_dt = _to_dt(kpi_report["to"])
    # Include 24h before start_dt for bandwidth tests so we have baseline context
    bw_start = start_dt - timedelta(hours=24)

    # 1. LibreSpeed bandwidth log
    bw_q = (
        select(TestResult, Test, Network, Monitor)
        .join(Test, TestResult.test_id == Test.id)
        .join(Network, Test.network_id == Network.id)
        .join(Monitor, Test.monitor_id == Monitor.id)
        .where(
            Test.slug == "librespeed",
            Network.slug.in_(("wired", "wifi")),
            TestResult.started_at >= bw_start,
            TestResult.started_at <= end_dt,
        )
        .order_by(TestResult.started_at.asc())
    )
    if monitor_id is not None:
        bw_q = bw_q.where(Test.monitor_id == monitor_id)
    bw_rows = db.execute(bw_q).all()
    bandwidth_log: list[dict[str, Any]] = []
    for res, _test, net, mon in bw_rows[-80:]:
        m = res.metrics or {}
        down = m.get("down_mbps")
        up = m.get("up_mbps")
        bandwidth_log.append(
            {
                "time_et": _et(res.started_at),
                "sensor": mon.name,
                "link": "Wi-Fi" if net.kind == NetworkKind.wifi else "Wired",
                "status": res.status.value,
                "down_mbps": round(float(down), 2) if isinstance(down, (int, float)) else None,
                "up_mbps": round(float(up), 2) if isinstance(up, (int, float)) else None,
                "reason": res.status_reason or "",
            }
        )

    # 2. Synthetic application health transitions & failures
    syn_q = (
        select(SyntheticSample, SyntheticRun, Monitor)
        .join(SyntheticRun, SyntheticSample.run_id == SyntheticRun.id)
        .join(Monitor, SyntheticRun.monitor_id == Monitor.id)
        .where(
            SyntheticSample.recorded_at >= start_dt,
            SyntheticSample.recorded_at <= end_dt,
            ~SyntheticSample.sample_key.startswith("hour:"),
            ~SyntheticSample.sample_key.startswith("day:"),
        )
        .order_by(SyntheticSample.recorded_at.asc())
    )
    if monitor_id is not None:
        syn_q = syn_q.where(SyntheticRun.monitor_id == monitor_id)
    syn_rows = db.execute(syn_q).all()

    app_transitions: list[dict[str, Any]] = []
    app_fail_counts: dict[str, int] = {}
    first_app_fail_at: Optional[str] = None
    last_verdict: dict[tuple[str, str], str] = {}
    for sample, run, mon in syn_rows:
        link_label = "Wi-Fi" if sample.link == "wireless" else "Wired"
        app_name = (run.options or {}).get("preset") or run.profile_slug
        key = (f"{mon.name}:{app_name}", link_label)
        verdict = sample.verdict or "ok"
        if verdict != "ok":
            k_str = f"{app_name} ({link_label})"
            app_fail_counts[k_str] = app_fail_counts.get(k_str, 0) + 1
            if first_app_fail_at is None:
                first_app_fail_at = _et(sample.recorded_at)
        prev = last_verdict.get(key, "ok")
        if verdict != prev:
            last_verdict[key] = verdict
            if len(app_transitions) < 80:
                app_transitions.append(
                    {
                        "time_et": _et(sample.recorded_at),
                        "app": app_name,
                        "link": link_label,
                        "transition": f"{prev} -> {verdict}",
                        "loss_pct": round(float(sample.loss_pct), 2) if sample.loss_pct is not None else None,
                        "rtt_ms": round(float(sample.rtt_ms), 1) if sample.rtt_ms is not None else None,
                        "jitter_ms": round(float(sample.jitter_ms), 1) if sample.jitter_ms is not None else None,
                    }
                )

    # 3. Path & web service non-OK events
    probe_q = (
        select(TestResult, Test, Network, Monitor)
        .join(Test, TestResult.test_id == Test.id)
        .join(Network, Test.network_id == Network.id)
        .join(Monitor, Test.monitor_id == Monitor.id)
        .where(
            Test.slug != "librespeed",
            Network.slug.in_(("wired", "wifi")),
            TestResult.status != ResultStatus.ok,
            TestResult.started_at >= start_dt,
            TestResult.started_at <= end_dt,
        )
        .order_by(TestResult.started_at.asc())
    )
    if monitor_id is not None:
        probe_q = probe_q.where(Test.monitor_id == monitor_id)
    probe_rows = db.execute(probe_q).all()
    probe_events: list[dict[str, Any]] = []
    for res, test, net, mon in probe_rows[:80]:
        m = res.metrics or {}
        probe_events.append(
            {
                "time_et": _et(res.started_at),
                "probe": test.slug,
                "link": "Wi-Fi" if net.kind == NetworkKind.wifi else "Wired",
                "status": res.status.value,
                "loss_pct": m.get("loss_pct"),
                "rtt_ms": m.get("rtt_ms") or m.get("total_ms"),
                "reason": res.status_reason or "",
            }
        )

    # 4. Alert events
    alert_rows = db.scalars(
        select(AlertEvent)
        .where(AlertEvent.first_seen_at >= start_dt - timedelta(hours=12))
        .order_by(desc(AlertEvent.first_seen_at))
        .limit(30)
    ).all()
    alerts_log = [
        {
            "first_seen_et": _et(a.first_seen_at),
            "opened_et": _et(a.opened_at),
            "resolved_et": _et(a.resolved_at),
            "status": a.status,
            "category": a.category,
            "severity": a.severity,
            "title": a.title,
            "detail": a.detail,
        }
        for a in reversed(list(alert_rows))
    ]

    # 5. Compact series buckets
    series_compact = []
    for pt in kpi_report.get("series", []):
        dt = _to_dt(pt["t"])
        series_compact.append(
            {
                "bucket_et": _et(dt),
                "internet_wired_pct": pt.get("internet_wired"),
                "internet_wifi_pct": pt.get("internet_wireless"),
                "voice_pct": pt.get("voice"),
                "video_pct": pt.get("video"),
                "web_pct": pt.get("web"),
                "loss_voice_pct": pt.get("loss_voice"),
                "loss_video_pct": pt.get("loss_video"),
                "rtt_ms": pt.get("rtt_ms"),
                "down_mbps": pt.get("down_mbps"),
                "up_mbps": pt.get("up_mbps"),
                "outage_min": pt.get("outage_minutes"),
                "gap_min": pt.get("gap_minutes"),
            }
        )

    return {
        "window": {
            "period": kpi_report["period"],
            "from_et": _et(start_dt),
            "to_et": _et(end_dt),
        },
        "targets": kpi_report.get("targets"),
        "rule_based_diagnosis": kpi_report.get("diagnosis"),
        "kpi_summary": kpi_report.get("summary"),
        "kpi_by_link": kpi_report.get("links"),
        "incidents": [
            {**inc, "start_et": _et(_to_dt(inc["start"]))}
            for inc in kpi_report.get("incidents", [])
        ],
        "reporting_gaps": [
            {**gap, "start_et": _et(_to_dt(gap["start"]))}
            for gap in kpi_report.get("gaps", [])
        ],
        "bandwidth_test_log": bandwidth_log,
        "app_failure_summary": {
            "first_failure_et": first_app_fail_at,
            "failed_samples_by_stream": app_fail_counts,
            "state_transitions": app_transitions,
        },
        "path_and_web_non_ok_events": probe_events,
        "telegram_alert_events": alerts_log,
        "bucket_series": series_compact,
    }


SYSTEM_PROMPT = """You are a senior network reliability engineer analyzing telemetry from HomeNetWatch, a home network monitoring system with a Raspberry Pi probe connected via both Wired Ethernet and Wi-Fi.

Architecture & Root-Cause Hierarchy:
1. Pi Offline / Reporting Gap: Neither link reports because the sensor was powered off or unreachable.
2. Shared Upstream ISP / WAN Issue: Affects BOTH Wired and Wi-Fi simultaneously.
   - 2a. ISP Outage: Both links lose path & public web reachability.
   - 2b. Upstream ISP Bandwidth Collapse: Bandwidth Test (`librespeed`) download or upload drops sharply (e.g., upload collapsing from ~30 Mbps to <1 Mbps while download stays high). Because real-time UDP streams (Voice `voip-g711a`, Video `video-conference`, Remote Desktop `remote-desktop`) run concurrently across Wired and Wi-Fi, an upstream upload collapse starves egress queues and causes packet loss/jitter across all real-time applications on BOTH links even when light HTTP/ICMP pings still pass.
   - 2c. Upstream ISP Path Congestion: High latency/loss on both Wired and Wi-Fi path checks.
3. Single-Link Issue: Only Wired OR only Wi-Fi is degraded while the sibling link stays healthy (e.g. local Wi-Fi RF interference, or bad Ethernet cable/port).
4. Measurement Tunnel Issue: WireGuard measurement tunnel (`10.7.x.1`) or synthetic receiver blip where synthetic/path checks fail on a link while external public Web HTTP checks (`http-google`, `http-youtube`, etc.) on that same link remain 100% healthy.
5. Isolated Application Issue: Only one specific application profile fails while bandwidth, path, and sibling streams are healthy.

Analyze the provided JSON event log and KPI telemetry. Determine:
- Exactly when the issue or degradation started (in Eastern Time, ET) based on the earliest signal in `bandwidth_test_log`, `bucket_series`, `path_and_web_non_ok_events`, or `app_failure_summary`.
- What the primary root cause is (do not blame individual applications when an upstream bandwidth drop or link issue is the real culprit).
- A clear chronological timeline of events showing how the issue unfolded.

Respond ONLY with a valid JSON object matching this exact schema:
{
  "started_at": "String naming the exact start timestamp in ET (e.g. 'Oct 28, 2026 at 14:15 ET') or 'No active incident detected'",
  "root_cause": "Concise 1-sentence headline naming the primary culprit",
  "category": "One of: 'bandwidth' | 'wan' | 'wifi' | 'wired' | 'tunnel' | 'pi' | 'healthy'",
  "confidence": "One of: 'high' | 'medium' | 'low'",
  "summary": "2-4 sentence plain-English explanation of what happened, why it started, and how the upstream cause explains the downstream symptoms.",
  "timeline": [
    {
      "time": "Timestamp in ET",
      "event": "What happened in the log",
      "impact": "How it affected network/application health"
    }
  ],
  "evidence": [
    "Specific metric comparison citing numbers from the log (e.g. upload Mbps vs baseline, Wired vs Wi-Fi loss %)"
  ],
  "recommendations": [
    "Concrete, actionable next step for the operator"
  ]
}
"""


def analyze_root_cause(
    db: Session,
    period: str = "day",
    day: Optional[date] = None,
    monitor_id: Optional[uuid.UUID] = None,
) -> dict[str, Any]:
    row = get_settings_row(db)
    api_key = resolve_api_key(row)
    if not api_key:
        raise GeminiError("Gemini API key is not configured. Add your key in Settings -> Configure Gemini AI.")

    context = build_event_context(db, period=period, day=day, monitor_id=monitor_id)
    user_payload = json.dumps(context, default=str)
    now = datetime.now(timezone.utc)
    try:
        result = call_gemini(
            api_key=api_key,
            model=row.model or DEFAULT_MODEL,
            system_prompt=SYSTEM_PROMPT,
            user_payload=user_payload,
            json_mode=True,
        )
    except GeminiError as exc:
        row.last_error = str(exc)[:300]
        db.commit()
        raise

    row.last_ok_at = now
    row.last_error = ""
    db.commit()

    return {
        "generated_at": now.isoformat(),
        "model": row.model or DEFAULT_MODEL,
        "started_at": str(result.get("started_at") or "Unknown"),
        "root_cause": str(result.get("root_cause") or "Analysis complete"),
        "category": str(result.get("category") or "wan"),
        "confidence": str(result.get("confidence") or "high").lower(),
        "summary": str(result.get("summary") or ""),
        "timeline": [
            {
                "time": str(item.get("time") or ""),
                "event": str(item.get("event") or ""),
                "impact": str(item.get("impact") or ""),
            }
            for item in (result.get("timeline") or [])
            if isinstance(item, dict)
        ],
        "evidence": [str(x) for x in (result.get("evidence") or []) if x],
        "recommendations": [str(x) for x in (result.get("recommendations") or []) if x],
    }
