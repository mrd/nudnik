import datetime
import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

import nudnik


# ---------------------------------------------------------------------------
# fmt_duration
# ---------------------------------------------------------------------------

def test_fmt_duration_seconds():
    assert nudnik.fmt_duration(45) == "45s"

def test_fmt_duration_minutes():
    assert nudnik.fmt_duration(90) == "1m 30s"

def test_fmt_duration_exact_minutes():
    assert nudnik.fmt_duration(120) == "2m 0s"

def test_fmt_duration_zero():
    assert nudnik.fmt_duration(0) == "0s"


# ---------------------------------------------------------------------------
# in_quiet_hours
# ---------------------------------------------------------------------------

def _dt(h, m):
    return datetime.datetime(2024, 1, 15, h, m, 0)

def test_quiet_hours_inside_same_day():
    assert nudnik.in_quiet_hours({"start": "09:00", "end": "17:00"}, None, _now=_dt(12, 0))

def test_quiet_hours_outside_same_day():
    assert not nudnik.in_quiet_hours({"start": "09:00", "end": "17:00"}, None, _now=_dt(8, 59))

def test_quiet_hours_at_start_boundary():
    assert nudnik.in_quiet_hours({"start": "09:00", "end": "17:00"}, None, _now=_dt(9, 0))

def test_quiet_hours_at_end_boundary():
    assert nudnik.in_quiet_hours({"start": "09:00", "end": "17:00"}, None, _now=_dt(17, 0))

def test_quiet_hours_overnight_inside_after_start():
    assert nudnik.in_quiet_hours({"start": "23:00", "end": "07:00"}, None, _now=_dt(23, 30))

def test_quiet_hours_overnight_inside_before_end():
    assert nudnik.in_quiet_hours({"start": "23:00", "end": "07:00"}, None, _now=_dt(3, 0))

def test_quiet_hours_overnight_outside():
    assert not nudnik.in_quiet_hours({"start": "23:00", "end": "07:00"}, None, _now=_dt(12, 0))


# ---------------------------------------------------------------------------
# in_quiet_days
# ---------------------------------------------------------------------------

# 2024-01-15 is a Monday (weekday=0)
MON = datetime.datetime(2024, 1, 15, 12, 0)
SAT = datetime.datetime(2024, 1, 20, 12, 0)
SUN = datetime.datetime(2024, 1, 21, 12, 0)

def test_quiet_days_match_full_name():
    assert nudnik.in_quiet_days(["saturday", "sunday"], None, _now=SAT)

def test_quiet_days_match_abbreviation():
    assert nudnik.in_quiet_days(["sat", "sun"], None, _now=SUN)

def test_quiet_days_case_insensitive():
    assert nudnik.in_quiet_days(["Saturday"], None, _now=SAT)

def test_quiet_days_no_match():
    assert not nudnik.in_quiet_days(["saturday", "sunday"], None, _now=MON)

def test_quiet_days_mixed_names():
    assert nudnik.in_quiet_days(["Saturday", "sun"], None, _now=SUN)


# ---------------------------------------------------------------------------
# resolve_timezone
# ---------------------------------------------------------------------------

def test_resolve_timezone_none():
    assert nudnik.resolve_timezone(None) is None

def test_resolve_timezone_valid():
    tz = nudnik.resolve_timezone("America/New_York")
    if nudnik.HAS_ZONEINFO:
        assert tz is not None
        assert str(tz) == "America/New_York"
    else:
        assert tz is None

def test_resolve_timezone_invalid(caplog):
    tz = nudnik.resolve_timezone("Not/ATimezone")
    assert tz is None
    if nudnik.HAS_ZONEINFO:
        assert "Unknown timezone" in caplog.text


# ---------------------------------------------------------------------------
# check_site
# ---------------------------------------------------------------------------

def _mock_response(status, body):
    resp = MagicMock()
    resp.status = status
    resp.headers = {}
    resp.read.return_value = body.encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_check_site_up_no_keyword():
    with patch("urllib.request.urlopen", return_value=_mock_response(200, "hello")):
        ok, reason = nudnik.check_site("http://example.com", "", 5)
    assert ok
    assert reason == "ok"

def test_check_site_up_keyword_found():
    with patch("urllib.request.urlopen", return_value=_mock_response(200, "status: ok")):
        ok, reason = nudnik.check_site("http://example.com", "ok", 5)
    assert ok

def test_check_site_keyword_missing():
    with patch("urllib.request.urlopen", return_value=_mock_response(200, "something else")):
        ok, reason = nudnik.check_site("http://example.com", "ok", 5)
    assert not ok
    assert "keyword" in reason

def test_check_site_keyword_case_insensitive():
    with patch("urllib.request.urlopen", return_value=_mock_response(200, "Status: OK")):
        ok, _ = nudnik.check_site("http://example.com", "ok", 5)
    assert ok

def test_check_site_http_error_status():
    with patch("urllib.request.urlopen", return_value=_mock_response(503, "")):
        ok, reason = nudnik.check_site("http://example.com", "", 5)
    assert not ok
    assert "503" in reason

def test_check_site_http_error_exception():
    err = urllib.error.HTTPError("http://x.com", 500, "Internal Server Error", {}, None)
    with patch("urllib.request.urlopen", side_effect=err):
        ok, reason = nudnik.check_site("http://example.com", "", 5)
    assert not ok
    assert "500" in reason

def test_check_site_url_error():
    err = urllib.error.URLError("connection refused")
    with patch("urllib.request.urlopen", side_effect=err):
        ok, reason = nudnik.check_site("http://example.com", "", 5)
    assert not ok
    assert "connection refused" in reason

def test_check_site_basic_auth():
    import base64
    captured = {}
    def fake_urlopen(req, timeout):
        captured["auth"] = req.get_header("Authorization")
        return _mock_response(200, "ok")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        nudnik.check_site("http://example.com", "", 5, username="user", password="pass")
    expected = "Basic " + base64.b64encode(b"user:pass").decode()
    assert captured["auth"] == expected


# ---------------------------------------------------------------------------
# auth_file
# ---------------------------------------------------------------------------

def test_main_auth_file_passes_credentials(monkeypatch, tmp_path):
    auth_file = tmp_path / "creds"
    auth_file.write_text("myuser:mypass\n")
    config = {**BASE_CONFIG, "sites": [
        {"name": "TestSite", "url": "http://example.com", "keyword": "ok",
         "auth_file": str(auth_file)}
    ]}
    captured = {}
    def fake_check_site(url, keyword, timeout, username=None, password=None, user_agent=None):
        captured["username"] = username
        captured["password"] = password
        return True, "ok"
    monkeypatch.setattr("nudnik.check_site", fake_check_site)
    monkeypatch.setattr("nudnik.sync_state_from_topic", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.topic_has_recent_alert", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.notify", lambda *a, **kw: None)

    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    cfg_file = tmp_path / "nudnik.toml"
    cfg_file.write_text(
        f'ntfy_topic = "t"\nstate_file = "{state_file}"\n'
        f'alert_interval_seconds = 3600\nrequest_timeout_seconds = 10\n'
        f'[[sites]]\nname = "TestSite"\nurl = "http://example.com"\n'
        f'keyword = "ok"\nauth_file = "{auth_file}"\n'
    )
    import sys
    monkeypatch.setattr(sys, "argv", ["nudnik", str(cfg_file)])
    nudnik.main()
    assert captured["username"] == "myuser"
    assert captured["password"] == "mypass"


# ---------------------------------------------------------------------------
# topic_has_recent_alert
# ---------------------------------------------------------------------------

def _ndjson_response(*msgs):
    lines = "\n".join(json.dumps(m) for m in msgs).encode()
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.__iter__ = lambda s: iter(lines.splitlines(keepends=True))
    return resp

def test_topic_has_recent_alert_found_no_reporter():
    msg = {"event": "message", "title": "mysite is down", "message": "down"}
    with patch("urllib.request.urlopen", return_value=_ndjson_response(msg)):
        assert nudnik.topic_has_recent_alert("topic", "mysite", 3600) == "unknown"

def test_topic_has_recent_alert_found_with_reporter():
    msg = {"event": "message", "title": "mysite is down", "message": "mysite is down\nReported by: node-b"}
    with patch("urllib.request.urlopen", return_value=_ndjson_response(msg)):
        assert nudnik.topic_has_recent_alert("topic", "mysite", 3600) == "node-b"

def test_topic_has_recent_alert_skips_own_node():
    msg = {"event": "message", "title": "mysite is down", "message": "mysite is down\nReported by: node-a"}
    with patch("urllib.request.urlopen", return_value=_ndjson_response(msg)):
        assert nudnik.topic_has_recent_alert("topic", "mysite", 3600, node_name="node-a") is None

def test_topic_has_recent_alert_not_found():
    msg = {"event": "message", "title": "other is down", "message": "down"}
    with patch("urllib.request.urlopen", return_value=_ndjson_response(msg)):
        assert nudnik.topic_has_recent_alert("topic", "mysite", 3600) is None

def test_topic_has_recent_alert_network_error():
    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        assert nudnik.topic_has_recent_alert("topic", "mysite", 3600) is None


# ---------------------------------------------------------------------------
# sync_state_from_topic
# ---------------------------------------------------------------------------

NOW = 1000000.0

def test_sync_sets_down_state_from_remote_alert():
    msg = {"event": "message", "time": int(NOW) - 60, "title": "mysite is down",
           "message": "mysite is unreachable\nReported by: node-b"}
    state = {}
    with patch("urllib.request.urlopen", return_value=_ndjson_response(msg)):
        nudnik.sync_state_from_topic("topic", ["mysite"], 3600, "node-a", state, NOW)
    assert state["mysite"]["status"] == "down"
    assert state["mysite"]["last_alert_node"] == "node-b"
    assert state["mysite"]["down_since"] == int(NOW) - 60

def test_sync_sets_up_state_from_remote_recovery():
    msg = {"event": "message", "time": int(NOW) - 30, "title": "mysite is back up",
           "message": "mysite recovered\nReported by: node-b"}
    state = {"mysite": {"status": "down", "last_alert": NOW - 100, "down_since": NOW - 200}}
    with patch("urllib.request.urlopen", return_value=_ndjson_response(msg)):
        nudnik.sync_state_from_topic("topic", ["mysite"], 3600, "node-a", state, NOW)
    assert state["mysite"]["status"] == "up"
    assert state["mysite"]["down_since"] is None

def test_sync_skips_own_node_messages():
    msg = {"event": "message", "time": int(NOW) - 60, "title": "mysite is down",
           "message": "mysite is unreachable\nReported by: node-a"}
    state = {}
    with patch("urllib.request.urlopen", return_value=_ndjson_response(msg)):
        nudnik.sync_state_from_topic("topic", ["mysite"], 3600, "node-a", state, NOW)
    assert "mysite" not in state

def test_sync_does_not_overwrite_existing_down_since():
    msg = {"event": "message", "time": int(NOW) - 60, "title": "mysite is down",
           "message": "mysite is unreachable\nReported by: node-b"}
    state = {"mysite": {"status": "down", "last_alert": NOW - 100,
                        "down_since": NOW - 500, "last_alert_node": "node-b"}}
    with patch("urllib.request.urlopen", return_value=_ndjson_response(msg)):
        nudnik.sync_state_from_topic("topic", ["mysite"], 3600, "node-a", state, NOW)
    assert state["mysite"]["down_since"] == NOW - 500

def test_sync_updates_last_alert_from_newer_message():
    msg = {"event": "message", "time": int(NOW) - 10, "title": "mysite is down",
           "message": "mysite is unreachable\nReported by: node-b"}
    state = {"mysite": {"status": "down", "last_alert": NOW - 100,
                        "down_since": NOW - 200, "last_alert_node": "node-c"}}
    with patch("urllib.request.urlopen", return_value=_ndjson_response(msg)):
        nudnik.sync_state_from_topic("topic", ["mysite"], 3600, "node-a", state, NOW)
    assert state["mysite"]["last_alert"] == int(NOW) - 10
    assert state["mysite"]["last_alert_node"] == "node-b"

def test_sync_network_error_leaves_state_unchanged():
    state = {}
    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        nudnik.sync_state_from_topic("topic", ["mysite"], 3600, "node-a", state, NOW)
    assert state == {}


# ---------------------------------------------------------------------------
# main — integration
# ---------------------------------------------------------------------------

BASE_CONFIG = {
    "ntfy_topic": "test-topic",
    "state_file": "",      # filled in per test
    "alert_interval_seconds": 3600,
    "request_timeout_seconds": 10,
    "retries": 0,
    "sites": [{"name": "TestSite", "url": "http://example.com", "keyword": "ok"}],
}

def _run_main(config, state, monkeypatch, tmp_path,
              site_up=True, has_remote_alert=False):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state))
    config = {**config, "state_file": str(state_file)}

    cfg_file = tmp_path / "nudnik.toml"
    # write a minimal toml manually
    lines = [f'ntfy_topic = "{config["ntfy_topic"]}"',
             f'state_file = "{config["state_file"]}"',
             f'alert_interval_seconds = {config["alert_interval_seconds"]}',
             f'request_timeout_seconds = {config["request_timeout_seconds"]}',
             f'retries = {config.get("retries", 2)}']
    for site in config["sites"]:
        lines.append("[[sites]]")
        for k, v in site.items():
            lines.append(f'{k} = "{v}"')
    if "quiet_hours" in config:
        qh = config["quiet_hours"]
        lines.append(f'[quiet_hours]')
        lines.append(f'start = "{qh["start"]}"')
        lines.append(f'end = "{qh["end"]}"')
    if "quiet_days" in config:
        days = ", ".join(f'"{d}"' for d in config["quiet_days"])
        lines.append(f"quiet_days = [{days}]")
    cfg_file.write_text("\n".join(lines))

    result = {"up": site_up, "reason": "ok" if site_up else "timeout"}
    monkeypatch.setattr("nudnik.check_site", lambda *a, **kw: (result["up"], result["reason"]))
    monkeypatch.setattr("nudnik.sync_state_from_topic", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.topic_has_recent_alert", lambda *a, **kw: "remote-node" if has_remote_alert else None)
    monkeypatch.setattr("nudnik.time.sleep", lambda s: None)
    notifications = []
    monkeypatch.setattr("nudnik.notify", lambda *a, **kw: notifications.append((a, kw)))

    import sys
    monkeypatch.setattr(sys, "argv", ["nudnik", str(cfg_file)])
    nudnik.main()

    return json.loads(state_file.read_text()), notifications


def test_main_site_up_no_history(monkeypatch, tmp_path):
    state, notifs = _run_main(BASE_CONFIG, {}, monkeypatch, tmp_path, site_up=True)
    assert state["TestSite"]["status"] == "up"
    assert notifs == []

def test_main_site_down_sends_alert(monkeypatch, tmp_path):
    state, notifs = _run_main(BASE_CONFIG, {}, monkeypatch, tmp_path, site_up=False)
    assert state["TestSite"]["status"] == "down"
    assert len(notifs) == 1
    assert notifs[0][0][1] == "TestSite is down"

def test_main_site_recovery_sends_notification(monkeypatch, tmp_path):
    prior = {"TestSite": {"status": "down", "last_alert": 0, "down_since": 0}}
    state, notifs = _run_main(BASE_CONFIG, prior, monkeypatch, tmp_path, site_up=True)
    assert state["TestSite"]["status"] == "up"
    assert any("back up" in n[0][1] for n in notifs)

def test_main_alert_suppressed_within_interval(monkeypatch, tmp_path):
    import time
    prior = {"TestSite": {"status": "down", "last_alert": time.time(), "down_since": 0}}
    state, notifs = _run_main(BASE_CONFIG, prior, monkeypatch, tmp_path, site_up=False)
    assert notifs == []

def test_main_alert_suppressed_by_remote(monkeypatch, tmp_path):
    state, notifs = _run_main(BASE_CONFIG, {}, monkeypatch, tmp_path,
                               site_up=False, has_remote_alert=True)
    assert notifs == []

def test_main_quiet_hours_suppresses_alert(monkeypatch, tmp_path):
    now = datetime.datetime.now()
    start = (now - datetime.timedelta(hours=1)).strftime("%H:%M")
    end = (now + datetime.timedelta(hours=1)).strftime("%H:%M")
    config = {**BASE_CONFIG, "quiet_hours": {"start": start, "end": end}}
    state, notifs = _run_main(config, {}, monkeypatch, tmp_path, site_up=False)
    assert notifs == []

def test_main_quiet_hours_suppresses_recovery(monkeypatch, tmp_path):
    now = datetime.datetime.now()
    start = (now - datetime.timedelta(hours=1)).strftime("%H:%M")
    end = (now + datetime.timedelta(hours=1)).strftime("%H:%M")
    config = {**BASE_CONFIG, "quiet_hours": {"start": start, "end": end}}
    prior = {"TestSite": {"status": "down", "last_alert": 0, "down_since": 0}}
    state, notifs = _run_main(config, prior, monkeypatch, tmp_path, site_up=True)
    assert notifs == []

def test_main_quiet_days_suppresses_alert(monkeypatch, tmp_path):
    today = datetime.datetime.now().strftime("%A").lower()
    config = {**BASE_CONFIG, "quiet_days": [today]}
    state, notifs = _run_main(config, {}, monkeypatch, tmp_path, site_up=False)
    assert notifs == []


def _run_main_with_check_sequence(results, monkeypatch, tmp_path, retries=2):
    """Run main with check_site returning successive (up, reason) values from results."""
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    cfg_file = tmp_path / "nudnik.toml"
    cfg_file.write_text(
        f'ntfy_topic = "t"\nstate_file = "{state_file}"\n'
        f'alert_interval_seconds = 3600\nrequest_timeout_seconds = 10\n'
        f'retries = {retries}\n'
        f'[[sites]]\nname = "TestSite"\nurl = "http://example.com"\nkeyword = "ok"\n'
    )
    it = iter(results)
    monkeypatch.setattr("nudnik.check_site", lambda *a, **kw: next(it))
    monkeypatch.setattr("nudnik.sync_state_from_topic", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.topic_has_recent_alert", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.time.sleep", lambda s: None)
    notifications = []
    monkeypatch.setattr("nudnik.notify", lambda *a, **kw: notifications.append((a, kw)))
    import sys
    monkeypatch.setattr(sys, "argv", ["nudnik", str(cfg_file)])
    nudnik.main()
    return json.loads(state_file.read_text()), notifications


def test_retry_succeeds_on_second_attempt(monkeypatch, tmp_path):
    results = [(False, "timeout"), (True, "ok")]
    state, notifs = _run_main_with_check_sequence(results, monkeypatch, tmp_path)
    assert state["TestSite"]["status"] == "up"
    assert notifs == []

def test_retry_all_fail_sends_alert(monkeypatch, tmp_path):
    results = [(False, "timeout")] * 3
    state, notifs = _run_main_with_check_sequence(results, monkeypatch, tmp_path, retries=2)
    assert state["TestSite"]["status"] == "down"
    assert len(notifs) == 1

def test_retry_sleeps_with_exponential_backoff(monkeypatch, tmp_path):
    sleeps = []
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    cfg_file = tmp_path / "nudnik.toml"
    cfg_file.write_text(
        f'ntfy_topic = "t"\nstate_file = "{state_file}"\n'
        f'alert_interval_seconds = 3600\nrequest_timeout_seconds = 10\n'
        f'retries = 3\n'
        f'[[sites]]\nname = "TestSite"\nurl = "http://example.com"\nkeyword = "ok"\n'
    )
    results = iter([(False, "timeout")] * 4)
    monkeypatch.setattr("nudnik.check_site", lambda *a, **kw: next(results))
    monkeypatch.setattr("nudnik.sync_state_from_topic", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.topic_has_recent_alert", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.notify", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.time.sleep", lambda s: sleeps.append(s))
    import sys
    monkeypatch.setattr(sys, "argv", ["nudnik", str(cfg_file)])
    nudnik.main()
    assert sleeps == [1, 2, 4]

def test_default_retries_is_two(monkeypatch, tmp_path):
    results = [(False, "timeout")] * 3
    state, notifs = _run_main_with_check_sequence(results, monkeypatch, tmp_path, retries=2)
    assert state["TestSite"]["status"] == "down"
    assert len(notifs) == 1

def test_retries_capped_at_five(monkeypatch, tmp_path):
    call_count = {"n": 0}
    def counting_check(*a, **kw):
        call_count["n"] += 1
        return False, "timeout"
    monkeypatch.setattr("nudnik.check_site", counting_check)
    monkeypatch.setattr("nudnik.sync_state_from_topic", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.topic_has_recent_alert", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.notify", lambda *a, **kw: None)
    monkeypatch.setattr("nudnik.time.sleep", lambda s: None)
    state_file = tmp_path / "state.json"
    state_file.write_text("{}")
    cfg_file = tmp_path / "nudnik.toml"
    cfg_file.write_text(
        f'ntfy_topic = "t"\nstate_file = "{state_file}"\n'
        f'alert_interval_seconds = 3600\nrequest_timeout_seconds = 10\n'
        f'retries = 99\n'
        f'[[sites]]\nname = "TestSite"\nurl = "http://example.com"\nkeyword = "ok"\n'
    )
    import sys
    monkeypatch.setattr(sys, "argv", ["nudnik", str(cfg_file)])
    nudnik.main()
    assert call_count["n"] == 6  # 1 initial + 5 retries
