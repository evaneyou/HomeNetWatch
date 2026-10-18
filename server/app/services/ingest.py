from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session, joinedload

from app.internet_tests import (
    classify_transfer,
    classify_web_service,
    is_path_check,
    is_web_check,
    load_internet_service,
    load_web_services,
    load_web_stability,
    path_variance,
    service_variance,
    stability_config,
)
from app.models import ResultStatus, Monitor, Test, TestResult
from app.schemas import IngestResult
from app.services.stability import next_state


def ingest_results(
    db: Session, monitor: Monitor, results: list[IngestResult]
) -> tuple[int, int, list[str]]:
    accepted = 0
    duplicates = 0
    ids: list[str] = []

    tests = (
        db.query(Test)
        .options(joinedload(Test.network))
        .filter(Test.monitor_id == monitor.id)
        .all()
    )
    test_map: dict[tuple[str, str], Test] = {(t.network.slug, t.slug): t for t in tests}
    settings = {row.key: row for row in load_web_services(db)}
    internet = load_internet_service(db)
    stability = stability_config(load_web_stability(db))
    touched: list[Test] = []

    for item in results:
        existing = db.query(TestResult).filter(TestResult.external_id == item.id).first()
        if existing:
            duplicates += 1
            ids.append(item.id)
            continue

        test = test_map.get((item.network_slug, item.test_slug))
        if test is None:
            continue

        try:
            status = ResultStatus(item.status)
        except ValueError:
            continue

        stages = [s.model_dump() for s in item.stages]
        variance = service_variance(settings, test.slug)
        if variance is None:
            variance = path_variance(internet, test.slug)
        if test.slug == "librespeed":
            pct = variance[0] if variance is not None else internet.librespeed_variance_pct
            status, reason = classify_transfer(db, test, item.metrics, pct)
        elif variance is not None:
            if test.kind.value == "http":
                abs_floor, jitter_floor = stability.abs_floor_http_ms, 0
            else:
                abs_floor, jitter_floor = stability.abs_floor_icmp_ms, stability.abs_floor_jitter_ms
            status, reason = classify_web_service(
                db,
                test,
                item.metrics,
                item.message,
                variance[0],
                variance[1],
                abs_floor,
                jitter_floor,
            )
        else:
            reason = None

        row = TestResult(
            external_id=item.id,
            test_id=test.id,
            started_at=item.started_at,
            finished_at=item.finished_at,
            status=status,
            stages=stages,
            metrics=item.metrics,
            message=item.message,
            status_reason=reason,
        )
        db.add(row)
        accepted += 1
        ids.append(item.id)
        if is_web_check(test.slug) or is_path_check(test.slug):
            touched.append(test)

    db.flush()
    for test in touched:
        _refresh_stable_status(db, test, stability)
    db.flush()
    return accepted, duplicates, ids


def _refresh_stable_status(db: Session, test: Test, cfg) -> None:
    recent = list(
        db.scalars(
            select(TestResult.status)
            .where(TestResult.test_id == test.id)
            .order_by(desc(TestResult.started_at))
            .limit(max(cfg.window_size, 3))
        ).all()
    )
    current = test.stable_status or ResultStatus.ok
    nxt = next_state(current, recent, cfg)
    if nxt != current or test.state_since is None:
        if nxt != current:
            test.state_since = datetime.now(timezone.utc)
        elif test.state_since is None:
            test.state_since = datetime.now(timezone.utc)
        test.stable_status = nxt
