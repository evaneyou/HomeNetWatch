"""Which tests are due, on-demand traces, and the synthetic traffic loop."""

from __future__ import annotations

import os
import sys
import threading
import time
import urllib.error

import agent_state as state
import synthetic
from checks import link_lock, run_check
from enrollment import load_config
from health import get_json, post_json
from radio import remember_trusted, upload_wifi_scan

def honor_restart(config: dict, restart: bool) -> None:
    """Ack a queued restart, then replace this process so the new code is what runs."""
    if not restart:
        return
    with state._lock:
        if state._restarting:
            return
        state._restarting = True
    try:
        post_json(
            f"{config['server_url'].rstrip('/')}/api/v1/monitors/command",
            {},
            config.get("token"),
            config.get("name"),
        )
        synthetic.stop_all()
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        with state._lock:
            state._restarting = False
        raise

def run_requested_trace(config: dict, request: dict) -> None:
    """Trace to a host someone asked for in the dashboard, out the link they chose, and report the hops."""
    link = request.get("link") if request.get("link") in {"wired", "wireless"} else "wired"
    test = {
        "slug": "adhoc-trace",
        "kind": "mtr",
        "target": str(request["target"]),
        "link": link,
        "network_slug": "wired" if link == "wired" else "wifi",
    }
    hops: list = []
    error = ""
    try:
        with link_lock:
            result = run_check(test) or {}
        hops = list((result.get("metrics") or {}).get("hops") or [])
        error = str(result.get("message") or "")
    except (OSError, ValueError) as exc:
        error = str(exc)
    try:
        post_json(
            f"{config['server_url'].rstrip('/')}/api/v1/monitors/traces/{request['id']}/result",
            {"hops": hops, "error": error[:300]},
            config.get("token"),
            config.get("name"),
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        pass

def poll_restart(config: dict) -> None:
    try:
        body = get_json(
            f"{config['server_url'].rstrip('/')}/api/v1/monitors/command",
            config.get("token"),
            config.get("name"),
        )
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return
    remember_trusted(body)
    honor_restart(config, bool(body.get("restart")))
    for request in body.get("traces") or []:
        if isinstance(request, dict) and request.get("id") and request.get("target"):
            threading.Thread(target=run_requested_trace, args=(config, request), daemon=True).start()
    if body.get("scan"):
        upload_wifi_scan(config, forced=True)
    if body.get("lan_scan"):
        from lan_scan import upload_lan_scan

        threading.Thread(target=upload_lan_scan, args=(config, post_json), daemon=True, name="lan-scan").start()

def remember_tests(tests: object) -> None:
    if not isinstance(tests, list):
        return
    assigned = []
    for item in tests:
        if not isinstance(item, dict):
            continue
        if item.get("kind") not in {"http", "librespeed", "ping", "mtr"}:
            continue
        if not item.get("slug") or not item.get("target"):
            continue
        assigned.append(item)
    with state._lock:
        state._assigned_tests = assigned

def _test_key(test: dict) -> str:
    return f"{test.get('network_slug')}:{test.get('slug')}"

def _interval_seconds(test: dict) -> int:
    interval = int(test.get("interval_seconds") or 300)
    kind = test.get("kind")
    if kind in state.HEAVY_KINDS:
        return max(interval, 300)
    if kind == "mtr":
        return max(interval, 120)
    return max(interval, 60)

def due_tests(now: float) -> list[dict]:
    with state._lock:
        assigned = list(state._assigned_tests)
        live = {_test_key(test) for test in assigned}
        for key in list(state._next_run):
            if key not in live:
                state._next_run.pop(key, None)
        ready = [test for test in assigned if now >= state._next_run.get(_test_key(test), 0)]
    return ready

def _mark_ran(test: dict) -> None:
    with state._lock:
        state._next_run[_test_key(test)] = time.monotonic() + _interval_seconds(test)

def _run_one(test: dict) -> dict | None:
    result = None
    try:
        result = run_check(test)
    except (OSError, TimeoutError, ValueError) as exc:
        with state._lock:
            state._last_error = str(exc)
    finally:
        _mark_ran(test)
    return result

def _idle_seconds() -> float:
    """Sleep until the next check is due, but wake at least every 15s for a new assignment."""
    now = time.monotonic()
    with state._lock:
        assigned = list(state._assigned_tests)
        upcoming = [state._next_run.get(_test_key(test), 0) for test in assigned]
    if not upcoming:
        return 15
    return min(15.0, max(0.5, min(upcoming) - now))

def run_due_tests(config: dict) -> None:
    ready = due_tests(time.monotonic())
    if not ready:
        return
    with link_lock:
        _run_due_tests(config, ready)

def _run_due_tests(config: dict, ready: list[dict]) -> None:
    latency = [test for test in ready if test.get("kind") not in state.HEAVY_KINDS | {"mtr"}]
    traces = [test for test in ready if test.get("kind") == "mtr"]
    heavy = [test for test in ready if test.get("kind") in state.HEAVY_KINDS]
    results = [result for result in state._probes.map(_run_one, latency) if result]
    # A trace shares the link with nothing else, so its timing is the path, not the other probes.
    results.extend(result for result in state._probes.map(_run_one, traces) if result)
    results.extend(result for result in (_run_one(test) for test in heavy) if result)
    if not results:
        return
    post_json(
        f"{config['server_url'].rstrip('/')}/api/v1/ingest",
        {"results": results},
        config.get("token"),
        config.get("name"),
    )

def test_loop() -> None:
    while not state._stop.is_set():
        with state._lock:
            config = load_config()
        if config.get("token") and config.get("name") and config.get("server_url"):
            try:
                snapshot = dict(config)
                poll_restart(snapshot)
                run_due_tests(snapshot)
            except Exception as exc:
                state._last_error = str(exc)
        state._stop.wait(_idle_seconds())

def synthetic_loop() -> None:
    while not state._stop.is_set():
        with state._lock:
            config = load_config()
        fast = False
        if config.get("token") and config.get("name") and config.get("server_url"):
            try:
                fast = bool(synthetic.sync(dict(config), get_json, post_json))
            except Exception as exc:
                state._last_error = str(exc)
        # While a stress test is open, look for added and removed sessions every 2 seconds.
        state._stop.wait(2 if fast else 15)
