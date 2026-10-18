"""Configure Telegram: the token stays secret, the settings are admin-only, and each problem is announced once."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import alerts, telegram
from app.main import app
from app.models import AlertEvent, Monitor, Network, ResultStatus, Test, TelegramSetting, TestKind
from tests.conftest import login
from tests.test_guest_login import _ensure_guest
from tests.test_ingest import _make_monitor

TOKEN = "123456789:" + "Ab3_" * 9  # 36 characters after the colon, the right shape
CHAT = "424242"


@pytest.fixture(autouse=True)
def _clean(db):
    db.execute(delete(AlertEvent))
    db.execute(delete(TelegramSetting))
    db.commit()
    alerts._errors.clear()
    yield
    db.rollback()
    db.execute(delete(AlertEvent))
    db.execute(delete(TelegramSetting))
    db.commit()


def _configure(db, **flags) -> TelegramSetting:
    row = alerts.get_settings_row(db)
    row.enabled = True
    row.bot_token_enc = telegram.encrypt(TOKEN)
    row.token_hint = TOKEN[-4:]
    row.chat_id = CHAT
    for name, value in flags.items():
        setattr(row, name, value)
    db.commit()
    return row


class Outbox:
    def __init__(self, fail: bool = False):
        self.messages: list[str] = []
        self.fail = fail

    def __call__(self, token: str, chat_id: str, text: str) -> None:
        assert token == TOKEN and chat_id == CHAT
        if self.fail:
            raise telegram.TelegramError("Telegram is limiting messages. It will retry.")
        self.messages.append(text)

    def about(self, name: str) -> list[str]:
        return [text for text in self.messages if name in text]


def _offline_pi(db, name: str) -> Monitor:
    monitor, _token = _make_monitor(db, name)
    monitor.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    db.commit()
    return monitor


# ---- the settings API

def test_the_token_is_stored_encrypted_and_never_returned(client: TestClient, db, monkeypatch):
    monkeypatch.setattr(telegram, "_call", lambda token, method, payload=None: {"username": "hnw_alert_bot"})
    headers = login(client)
    saved = client.put("/api/v1/settings/telegram", headers=headers, json={"token": TOKEN, "chat_id": CHAT})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["token_set"] is True and body["token_hint"] == TOKEN[-4:] and body["bot_username"] == "hnw_alert_bot"
    assert TOKEN not in saved.text and TOKEN not in client.get("/api/v1/settings/telegram").text

    row = db.scalar(select(TelegramSetting))
    assert TOKEN not in row.bot_token_enc and telegram.decrypt(row.bot_token_enc) == TOKEN

    # Saving again without a token keeps the one that is stored.
    again = client.put("/api/v1/settings/telegram", headers=headers, json={"chat_id": CHAT, "notify_pi": False})
    assert again.status_code == 200 and again.json()["notify_pi"] is False
    db.expire_all()
    assert telegram.decrypt(db.scalar(select(TelegramSetting)).bot_token_enc) == TOKEN


def test_only_an_admin_can_read_or_change_the_settings(client: TestClient, db):
    _ensure_guest(db)
    assert client.get("/api/v1/settings/telegram").status_code in (401, 403)
    guest = TestClient(app, base_url="https://testserver")
    login(guest, "guest", "guest")
    assert guest.get("/api/v1/settings/telegram").status_code == 403
    assert guest.get("/api/v1/settings/telegram/events").status_code == 403
    viewer = TestClient(app, base_url="https://testserver")
    login(viewer, "viewer", "viewer-change-me")
    assert viewer.get("/api/v1/settings/telegram").status_code == 403
    login(client)  # an admin session, but no CSRF token on the write
    assert client.put("/api/v1/settings/telegram", json={"chat_id": CHAT}).status_code == 403
    assert client.post("/api/v1/settings/telegram/test").status_code == 403


def test_bad_input_is_refused(client: TestClient, monkeypatch):
    monkeypatch.setattr(telegram, "_call", lambda *args, **kwargs: {"username": "x_bot"})
    headers = login(client)
    put = lambda **body: client.put("/api/v1/settings/telegram", headers=headers, json=body)  # noqa: E731
    assert put(token="not a token", chat_id=CHAT).status_code == 400
    assert put(token=TOKEN, chat_id="not a chat").status_code == 400
    # Turning alerts on needs both a token and a chat.
    assert put(chat_id=CHAT, enabled=True).status_code == 400
    assert put(chat_id="", enabled=False).status_code == 200


def test_a_rejected_token_is_not_saved(client: TestClient, db, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise telegram.TelegramError("Telegram rejected the bot token.")

    monkeypatch.setattr(telegram, "_call", refuse)
    headers = login(client)
    response = client.put("/api/v1/settings/telegram", headers=headers, json={"token": TOKEN, "chat_id": CHAT})
    assert response.status_code == 400 and "rejected" in response.json()["detail"]
    assert TOKEN not in response.text
    assert not alerts.get_settings_row(db).bot_token_enc


def test_test_message_and_chat_detection(client: TestClient, db, monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send", lambda token, chat, text: sent.append((token, chat, text)))
    headers = login(client)
    assert client.post("/api/v1/settings/telegram/test", headers=headers).status_code == 400  # nothing saved yet
    _configure(db)
    ok = client.post("/api/v1/settings/telegram/test", headers=headers)
    assert ok.status_code == 200 and sent and sent[0][1] == CHAT and ok.json()["last_ok_at"]

    monkeypatch.setattr(
        telegram,
        "_call",
        lambda token, method, payload=None: [
            {"message": {"chat": {"id": 7, "first_name": "Evan", "type": "private"}}},
            {"message": {"chat": {"id": -100, "title": "Home", "type": "group"}}},
            {"message": {"chat": {"id": 7, "first_name": "Evan", "type": "private"}}},
            {"edited_message": {}},
        ],
    )
    chats = client.post("/api/v1/settings/telegram/detect", headers=headers, json={}).json()["chats"]
    assert {item["id"] for item in chats} == {"7", "-100"}

    def blocked(*_args, **_kwargs):
        raise telegram.TelegramError("The bot was blocked or removed from that chat.")

    monkeypatch.setattr(telegram, "send", blocked)
    failed = client.post("/api/v1/settings/telegram/test", headers=headers)
    assert failed.status_code == 400 and "blocked" in failed.json()["detail"]
    assert "blocked" in client.get("/api/v1/settings/telegram").json()["last_error"]


def test_disconnect_clears_the_token(client: TestClient, db):
    _configure(db)
    headers = login(client)
    body = client.delete("/api/v1/settings/telegram", headers=headers).json()
    assert body["token_set"] is False and body["chat_id"] == "" and body["enabled"] is False
    db.expire_all()
    assert db.scalar(select(TelegramSetting)).bot_token_enc == ""


# ---- the client: the token must not leak

def test_the_token_never_reaches_the_logs_or_an_error(monkeypatch, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        assert TOKEN in str(request.url)  # Telegram needs it in the path
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    monkeypatch.setattr(telegram, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(telegram.TelegramError) as caught:
            telegram.bot_username(TOKEN)
    assert "rejected" in str(caught.value)
    assert TOKEN not in str(caught.value) and TOKEN not in caplog.text
    assert logging.getLogger("httpx").level >= logging.WARNING

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    monkeypatch.setattr(telegram, "_client", lambda: httpx.Client(transport=httpx.MockTransport(unreachable)))
    with pytest.raises(telegram.TelegramError) as caught:
        telegram.send(TOKEN, CHAT, "hello")
    assert TOKEN not in str(caught.value) and caught.value.__cause__ is None


# ---- the engine

def test_a_pi_going_offline_is_announced_once_and_again_when_it_recovers(db):
    _configure(db)
    out = Outbox()
    name = f"Roof Pi {uuid.uuid4().hex[:6]}"
    monitor = _offline_pi(db, name)

    alerts.run_cycle(db, sender=out)
    assert len(out.about(name)) == 1 and "offline" in out.about(name)[0]
    alerts.run_cycle(db, sender=out)
    alerts.run_cycle(db, sender=out)
    assert len(out.about(name)) == 1, "the same problem is not announced twice"

    monitor.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    alerts.run_cycle(db, sender=out)
    messages = out.about(name)
    assert len(messages) == 2 and "Resolved" in messages[1]
    alerts.run_cycle(db, sender=out)
    assert len(out.about(name)) == 2


def test_recovery_notices_and_categories_can_be_turned_off(db):
    _configure(db, notify_recovery=False)
    out = Outbox()
    name = f"Garage Pi {uuid.uuid4().hex[:6]}"
    monitor = _offline_pi(db, name)
    alerts.run_cycle(db, sender=out)
    monitor.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    alerts.run_cycle(db, sender=out)
    assert len(out.about(name)) == 1, "no recovery message when recovery notices are off"

    row = alerts.get_settings_row(db)
    row.notify_pi = False
    db.commit()
    quiet = f"Shed Pi {uuid.uuid4().hex[:6]}"
    _offline_pi(db, quiet)
    alerts.run_cycle(db, sender=out)
    assert out.about(quiet) == []


def test_nothing_is_sent_until_telegram_is_configured_and_on(db):
    out = Outbox()
    name = f"Attic Pi {uuid.uuid4().hex[:6]}"
    _offline_pi(db, name)
    alerts.run_cycle(db, sender=out)
    assert out.messages == []
    row = _configure(db)
    row.enabled = False
    db.commit()
    alerts.run_cycle(db, sender=out)
    assert out.messages == []


def test_a_failed_send_is_retried_and_a_short_blip_is_never_announced(db):
    _configure(db)
    name = f"Porch Pi {uuid.uuid4().hex[:6]}"
    monitor = _offline_pi(db, name)
    down = Outbox(fail=True)
    alerts.run_cycle(db, sender=down)
    assert down.messages == []
    assert alerts.get_settings_row(db).last_error
    up = Outbox()
    alerts.run_cycle(db, sender=up)
    assert len(up.about(name)) == 1, "an unsent alert goes out on the next pass"
    assert alerts.get_settings_row(db).last_error == ""

    # A condition with a debounce that clears first is dropped without a message.
    monitor.last_seen_at = datetime.now(timezone.utc)
    db.commit()
    healthy = _hot_pi(db)
    blip = Outbox()
    alerts.run_cycle(db, sender=blip)
    assert blip.about(healthy.name) == []
    healthy_health = healthy.health_samples[-1]
    healthy_health.temp_c = 50.0
    db.commit()
    alerts.run_cycle(db, sender=blip)
    assert blip.about(healthy.name) == []
    assert db.scalar(select(AlertEvent).where(AlertEvent.key == f"pi-temp:{healthy.id}")) is None


def _hot_pi(db) -> Monitor:
    from app.models import MonitorHealth

    monitor, _token = _make_monitor(db, f"Hot Pi {uuid.uuid4().hex[:6]}")
    now = datetime.now(timezone.utc)
    monitor.last_seen_at = now
    db.add(
        MonitorHealth(
            monitor_id=monitor.id,
            reported_at=now,
            hostname="pi",
            uptime_seconds=100,
            cpu_percent=10,
            load_1=0.1,
            mem_used_mb=100,
            mem_total_mb=1000,
            disk_used_mb=100,
            disk_total_mb=1000,
            temp_c=85.0,
        )
    )
    db.commit()
    db.refresh(monitor)
    return monitor


def test_a_hot_pi_waits_for_its_debounce_then_is_announced(db):
    _configure(db)
    out = Outbox()
    pi = _hot_pi(db)
    start = datetime.now(timezone.utc)
    alerts.run_cycle(db, now=start, sender=out)
    assert out.about(pi.name) == []
    alerts.run_cycle(db, now=start + timedelta(seconds=130), sender=out)
    assert len(out.about(pi.name)) == 1 and "85" in out.about(pi.name)[0]


def test_an_internet_outage_on_a_link_is_a_critical_alert(db):
    _configure(db)
    out = Outbox()
    monitor, _token = _make_monitor(db, f"Link Pi {uuid.uuid4().hex[:6]}")
    monitor.last_seen_at = datetime.now(timezone.utc)
    network = db.scalar(select(Network).where(Network.group_id == db.info["group_id"]))
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    for slug, kind in (("http-path", TestKind.http), ("icmp-path", TestKind.ping)):
        db.add(
            Test(
                monitor_id=monitor.id,
                network_id=network.id,
                slug=slug,
                name=slug,
                kind=kind,
                target="x",
                stable_status=ResultStatus.fail,
                state_since=since,
            )
        )
    db.commit()
    start = datetime.now(timezone.utc)
    alerts.run_cycle(db, now=start, sender=out)
    alerts.run_cycle(db, now=start + timedelta(seconds=70), sender=out)
    sent = out.about(monitor.name)
    assert len(sent) == 1 and "Internet outage on the Wired link" in sent[0] and sent[0].startswith("\U0001F534")


def test_server_problems_are_announced(db, monkeypatch):
    _configure(db)
    out = Outbox()
    for _ in range(alerts.SERVER_ERRORS_LIMIT):
        alerts.note_server_error()
    monkeypatch.setattr(alerts, "_pool_use", lambda: (14, 15))
    start = datetime.now(timezone.utc)
    alerts.run_cycle(db, now=start, sender=out)
    alerts.run_cycle(db, now=start + timedelta(seconds=130), sender=out)
    text = "\n".join(out.messages)
    assert "server is returning errors" in text and "database connections" in text


def test_the_hourly_limit_holds_messages_back(db, monkeypatch):
    _configure(db)
    monkeypatch.setattr(alerts, "MAX_PER_HOUR", 2)
    out = Outbox()
    names = [f"Many Pi {i} {uuid.uuid4().hex[:4]}" for i in range(4)]
    for name in names:
        _offline_pi(db, name)
    counts = alerts.run_cycle(db, sender=out)
    assert counts["sent"] == 2 and counts["limited"] >= 1
    later = alerts.run_cycle(db, now=datetime.now(timezone.utc) + timedelta(hours=1, minutes=1), sender=out)
    assert later["sent"] >= 1, "held messages go out once the hour has passed"


def test_events_list_shows_what_was_announced(client: TestClient, db):
    _configure(db)
    name = f"List Pi {uuid.uuid4().hex[:6]}"
    _offline_pi(db, name)
    alerts.run_cycle(db, sender=Outbox())
    headers = login(client)
    events = client.get("/api/v1/settings/telegram/events", headers=headers).json()
    mine = [item for item in events if name in item["title"]]
    assert mine and mine[0]["status"] == "open" and mine[0]["notified"] is True and mine[0]["category"] == "pi"


def test_degraded_link_and_airwave_security_issues_are_announced(db):
    from app.models import Issue, IssueSeverity, IssueStatus

    _configure(db)
    out = Outbox()
    monitor, _token = _make_monitor(db, f"WiFi Pi {uuid.uuid4().hex[:6]}")
    start = datetime.now(timezone.utc)
    monitor.last_seen_at = start
    network = db.scalar(select(Network).where(Network.group_id == db.info["group_id"], Network.slug == "wifi"))
    if network is None:
        network = db.scalar(select(Network).where(Network.group_id == db.info["group_id"]))
    since = start - timedelta(minutes=10)
    db.add(
        Test(
            monitor_id=monitor.id,
            network_id=network.id,
            slug="icmp-path",
            name="icmp-path",
            kind=TestKind.ping,
            target="10.7.1.1",
            stable_status=ResultStatus.degraded,
            state_since=since,
        )
    )
    db.add(
        Issue(
            group_id=monitor.group_id,
            monitor_id=monitor.id,
            title="Possible evil twin HomeWiFi on aa:bb:cc:dd:ee:ff",
            summary="Possible evil twin HomeWiFi on aa:bb:cc:dd:ee:ff",
            severity=IssueSeverity.warning,
            status=IssueStatus.open,
            root_cause_stage="airwave",
            opened_at=since,
        )
    )
    db.commit()

    alerts.run_cycle(db, now=start, sender=out)
    later = start + timedelta(seconds=310)
    monitor.last_seen_at = later
    db.commit()
    alerts.run_cycle(db, now=later, sender=out)
    text = "\n".join(out.about(monitor.name))
    assert "link is degraded" in text
    assert "Wi-Fi security alert" in text
