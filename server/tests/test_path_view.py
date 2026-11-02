from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.internet_tests import _response_sample, select_trace_test
from app.models import Monitor, Network, NetworkKind, ResultStatus, Test, TestKind, TestResult
from app.path_enrich import enrich_hops
from app.path_view import build_trace


def _hop(**kwargs):
    base = {
        "loss_pct": 0,
        "best_ms": 1,
        "avg_ms": 1,
        "worst_ms": 1,
    }
    base.update(kwargs)
    return base


def test_mockup_groups_silent_hop_and_matching_path():
    hops = [
        _hop(ttl=1, ip="192.168.1.1", avg_ms=0.6, best_ms=0.5, worst_ms=0.9, as_name="Private address space"),
        _hop(ttl=2, ip="198.51.100.1", avg_ms=9.2, asn=7922, as_name="Comcast"),
        _hop(ttl=3, ip="198.51.100.22", avg_ms=10.4, asn=7922, as_name="Comcast"),
        _hop(ttl=4, ip="198.51.100.130", avg_ms=12.1, asn=7922, as_name="Comcast"),
        _hop(ttl=5, ip="203.0.113.9", avg_ms=13.0, role="ixp", ixp_name="Equinix Ashburn", name="Equinix Ashburn"),
        _hop(ttl=6, ip="203.0.113.41", avg_ms=14.2, asn=3356, as_name="Lumen"),
        _hop(ttl=7, ip="203.0.113.77", avg_ms=15.0, asn=3356, as_name="Lumen"),
        _hop(ttl=8, host="???", loss_pct=100, avg_ms=0, best_ms=0, worst_ms=0),
        _hop(ttl=9, ip="192.0.2.9", avg_ms=38.6, asn=64496, as_name="Example Cloud"),
        _hop(ttl=10, ip="192.0.2.50", avg_ms=39.1, asn=64496, as_name="Example Cloud"),
    ]
    trace = build_trace(hops, announced=[7922, 3356, 64496])
    roles = [group["role"] for group in trace["groups"]]
    assert roles == ["home", "isp", "ixp", "transit", "unknown", "destination"]
    unknown = trace["groups"][4]
    assert unknown["hop_ttls"] == [8]
    assert unknown["delta_in_ms"] is None
    destination = trace["groups"][-1]
    assert destination["delta_in_ms"] == 23.6
    assert trace["warn_ttls"] == [9]
    inferred = {hop["ttl"] for hop in trace["hops"] if hop["inferred"]}
    assert inferred == {6, 9}
    assert trace["bgp"]["path_match"] is True
    assert [item["type"] for item in trace["as_path_observed"]] == ["as", "ixp", "as", "as"]
    assert "Hop 8 stays silent" in trace["insight"]


def test_consecutive_silent_hops_stay_separate():
    hops = [
        _hop(ttl=1, ip="192.168.0.1", avg_ms=1),
        _hop(ttl=2, host="???", loss_pct=100, avg_ms=0),
        _hop(ttl=3, host="?", loss_pct=100, avg_ms=0),
        _hop(ttl=4, ip="192.0.2.10", avg_ms=20),
    ]
    trace = build_trace(hops)
    unknown = [group for group in trace["groups"] if group["role"] == "unknown"]
    assert [group["hop_ttls"] for group in unknown] == [[2], [3]]
    assert all(group["delta_in_ms"] is None for group in unknown)


def test_cgnat_is_home_and_unenriched_public_hops_group_as_isp():
    hops = [
        _hop(ttl=1, ip="100.64.0.1", avg_ms=0.4),
        _hop(ttl=2, ip="198.51.100.8", avg_ms=11),
        _hop(ttl=3, ip="198.51.100.9", avg_ms=12),
        _hop(ttl=4, ip="192.0.2.20", avg_ms=30),
    ]
    trace = build_trace(hops, isp_name="Comcast")
    assert [group["role"] for group in trace["groups"]] == ["home", "isp", "destination"]
    assert trace["groups"][1]["name"] == "Comcast"
    assert trace["groups"][1]["hop_ttls"] == [2, 3]
    assert trace["groups"][1]["delta_in_ms"] == 10.6
    dest = next(hop for hop in trace["hops"] if hop["ttl"] == 4)
    assert dest["asn"] is None
    assert dest["prefix"] is None
    assert dest["rdns"] is None
    assert trace["bgp"]["path_match"] is None
    assert trace["bgp"]["rpki"] is None


def test_announced_path_mismatch_ignores_ixp():
    hops = [
        _hop(ttl=1, ip="198.51.100.1", avg_ms=5, asn=7922, as_name="Comcast"),
        _hop(ttl=2, ip="203.0.113.9", avg_ms=6, role="ixp", ixp_name="Equinix Ashburn"),
        _hop(ttl=3, ip="192.0.2.50", avg_ms=20, asn=64496, as_name="Example Cloud"),
    ]
    match = build_trace(hops, announced=[7922, 64496])
    assert match["bgp"]["path_match"] is True
    mismatch = build_trace(hops, announced=[7922, 3356, 64496])
    assert mismatch["bgp"]["path_match"] is False


def test_path_falls_back_to_the_network_that_has_a_trace(db):
    monitor = db.scalars(select(Monitor).where(Monitor.slug == db.info["monitor_slug"])).one()
    wan = db.scalars(select(Network).where(Network.group_id == monitor.group_id, Network.slug == "wan")).one()
    wired = Network(group_id=monitor.group_id, slug="wired", name="Wired", kind=NetworkKind.wired)
    db.add(wired)
    db.flush()
    now = datetime.now(timezone.utc)
    older = Test(
        monitor_id=monitor.id,
        network_id=wan.id,
        slug="mtr-youtube",
        name="YouTube MTR",
        kind=TestKind.mtr,
        target="www.youtube.com",
        interval_seconds=300,
    )
    empty = Test(
        monitor_id=monitor.id,
        network_id=wired.id,
        slug="mtr-youtube",
        name="YouTube MTR",
        kind=TestKind.mtr,
        target="www.youtube.com",
        interval_seconds=300,
    )
    db.add_all([older, empty])
    db.flush()
    db.add_all(
        [
            TestResult(
                external_id=f"yt-{uuid4().hex}",
                test_id=older.id,
                started_at=now,
                finished_at=now,
                status=ResultStatus.ok,
                stages=[],
                metrics={"hops": [{"hop": 1, "host": "192.0.2.1", "loss_pct": 0, "avg_ms": 1, "best_ms": 1, "worst_ms": 1}]},
            ),
            TestResult(
                external_id=f"yt-empty-{uuid4().hex}",
                test_id=empty.id,
                started_at=now,
                finished_at=now,
                status=ResultStatus.fail,
                stages=[],
                metrics={"hops": []},
            ),
        ]
    )
    db.flush()
    found = select_trace_test(db, monitor.id, "mtr-youtube", "wired")
    assert found is not None
    assert found.id == older.id


def test_enrichment_adds_asn_and_announced_path():
    seen: list[str] = []

    def fetch(url: str) -> dict:
        seen.append(url)
        if "prefix-overview" in url and "8.8.8.8" in url:
            return {"data": {"asns": [{"asn": 15169, "holder": "GOOGLE"}], "block": {"resource": "8.8.8.0/24"}}}
        if "prefix-overview" in url and "1.1.1.1" in url:
            return {"data": {"asns": [{"asn": 13335, "holder": "CLOUDFLARE"}], "block": {"resource": "1.1.1.0/24"}}}
        if "bgp-state" in url:
            return {
                "data": {
                    "bgp_state": [
                        {"path": [174, 15169]},
                        {"path": [174, 15169]},
                        {"path": [174, 15169]},
                    ]
                }
            }
        if "rpki-validation" in url:
            return {"data": {"status": "valid"}}
        if "as-overview" in url and "AS174" in url:
            return {"data": {"holder": "COGENT"}}
        return {"data": {}}

    hops, announced, names, rpki = enrich_hops(
        [
            {"hop": 1, "host": "192.168.1.1", "loss_pct": 0, "avg_ms": 1, "best_ms": 1, "worst_ms": 1},
            {"hop": 2, "host": "1.1.1.1", "loss_pct": 0, "avg_ms": 8, "best_ms": 8, "worst_ms": 8},
            {"hop": 3, "host": "???", "loss_pct": 100, "avg_ms": 0, "best_ms": 0, "worst_ms": 0},
            {"hop": 4, "host": "8.8.8.8", "loss_pct": 0, "avg_ms": 20, "best_ms": 20, "worst_ms": 20},
        ],
        fetch=fetch,
        resolve_names=False,
    )
    assert not any("192.168.1.1" in url for url in seen)
    assert hops[1]["asn"] == 13335
    assert hops[3]["asn"] == 15169
    assert hops[3]["prefix"] == "8.8.8.0/24"
    assert announced == [174, 15169]
    assert names[174] == "COGENT"
    assert rpki == "valid"
    trace = build_trace(hops, announced=announced, as_names=names, target_name="Google")
    assert [group["asn"] for group in trace["groups"]] == [None, 13335, None, 15169]
    assert trace["bgp"]["as_path_announced"][0]["name"] == "COGENT"
    assert trace["bgp"]["as_path_announced"][1]["name"] == "GOOGLE"


def _bgp_fetch(paths: list[list[int]]):
    # RIPEstat answers are cached by URL; each test brings its own.
    from app import path_enrich

    path_enrich._CACHE.clear()

    def fetch(url: str) -> dict:
        if "prefix-overview" in url and "1.1.1.1" in url:
            return {"data": {"asns": [{"asn": 7922, "holder": "COMCAST"}], "block": {"resource": "1.1.1.0/24"}}}
        if "prefix-overview" in url and "8.8.8.8" in url:
            return {"data": {"asns": [{"asn": 31898, "holder": "ORACLE"}], "block": {"resource": "8.8.8.0/24"}}}
        if "bgp-state" in url:
            return {"data": {"bgp_state": [{"path": path} for path in paths]}}
        return {"data": {}}

    return fetch


_HOME_TO_ORACLE = [
    {"hop": 1, "host": "192.168.1.1", "loss_pct": 0, "avg_ms": 1, "best_ms": 1, "worst_ms": 1},
    {"hop": 2, "host": "1.1.1.1", "loss_pct": 0, "avg_ms": 9, "best_ms": 9, "worst_ms": 9},
    {"hop": 3, "host": "8.8.8.8", "loss_pct": 0, "avg_ms": 30, "best_ms": 30, "worst_ms": 30},
]


def test_announced_path_prefers_a_route_through_the_agents_provider():
    # Most collectors reach Oracle through NTT; one sees it through Comcast, prepended. That one is shown,
    # from Comcast onward, so it starts where the trace does.
    paths = [[24482, 2914, 31898], [24482, 2914, 31898], [3356, 2914, 31898], [6939, 7922, 7922, 31898]]
    hops, announced, names, _rpki = enrich_hops(_HOME_TO_ORACLE, fetch=_bgp_fetch(paths), resolve_names=False)
    assert announced == [7922, 31898]
    trace = build_trace(hops, announced=announced, as_names=names)
    assert trace["bgp"]["via_isp"] is True
    assert trace["bgp"]["path_match"] is True


def test_announced_path_falls_back_when_no_collector_sees_the_provider():
    paths = [[24482, 2914, 31898], [24482, 2914, 31898], [3356, 2914, 31898]]
    hops, announced, names, _rpki = enrich_hops(_HOME_TO_ORACLE, fetch=_bgp_fetch(paths), resolve_names=False)
    assert announced == [24482, 2914, 31898]
    trace = build_trace(hops, announced=announced, as_names=names)
    assert trace["bgp"]["via_isp"] is False
    assert trace["bgp"]["path_match"] is False


def test_mtr_uses_the_destination_hop_not_a_silent_router():
    responded, value, imperfect = _response_sample(
        TestKind.mtr,
        {
            "rtt_ms": 0,
            "loss_pct": 100,
            "hops": [
                {"hop": 1, "loss_pct": 100, "avg_ms": 0},
                {"hop": 2, "loss_pct": 0, "avg_ms": 21.5},
            ],
        },
        None,
    )
    assert responded is True
    assert value == 21.5
    assert imperfect is False


def test_large_step_marks_only_the_entering_hop():
    hops = [
        _hop(ttl=1, ip="192.168.1.1", avg_ms=1),
        _hop(ttl=2, ip="198.51.100.1", avg_ms=4, asn=7922, as_name="Comcast"),
        _hop(ttl=3, ip="203.0.113.41", avg_ms=40, asn=3356, as_name="Lumen"),
        _hop(ttl=4, ip="203.0.113.42", avg_ms=41, asn=3356, as_name="Lumen"),
    ]
    trace = build_trace(hops)
    assert trace["warn_ttls"] == [3]
    assert trace["focus_ttl"] == 3
    assert trace["stats"]["biggest_step_ms"] == 36.0


if __name__ == "__main__":
    test_mockup_groups_silent_hop_and_matching_path()
    test_consecutive_silent_hops_stay_separate()
    test_cgnat_is_home_and_unenriched_public_hops_group_as_isp()
    test_announced_path_mismatch_ignores_ixp()
    test_large_step_marks_only_the_entering_hop()
    print("ok")
