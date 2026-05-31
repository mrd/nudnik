#!/usr/bin/env python3

import argparse
import base64
import datetime
import json
import logging
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        sys.exit("Python < 3.11 requires 'tomli': pip install tomli")

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    HAS_ZONEINFO = True
except ImportError:
    HAS_ZONEINFO = False


def load_state(path):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path, state):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


def check_site(url, keyword, timeout, username=None, password=None, user_agent=DEFAULT_UA):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    if username is not None and password is not None:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            headers = resp.headers
            body = resp.read().decode("utf-8", errors="replace")
            if not (200 <= status < 300):
                logging.warning(f"{url} -> HTTP {status}")
                logging.warning("headers:\n" + "\n".join(f"  {k}: {v}" for k, v in headers.items()))
                return False, f"HTTP {status}"
            if keyword and keyword.lower() not in body.lower():
                logging.warning(f"{url} -> HTTP {status}, keyword '{keyword}' not found")
                logging.warning("headers:\n" + "\n".join(f"  {k}: {v}" for k, v in headers.items()))
                logging.warning(f"body (first 500 chars): {body[:500]}")
                return False, f"keyword '{keyword}' not found"
            return True, "ok"
    except urllib.error.HTTPError as e:
        logging.warning(f"{url} -> HTTP {e.code}: {e.reason}")
        logging.warning("headers:\n" + "\n".join(f"  {k}: {v}" for k, v in e.headers.items()))
        return False, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        logging.warning(f"{url} -> URLError: {e.reason}")
        return False, str(e.reason)
    except Exception as e:
        logging.warning(f"{url} -> {type(e).__name__}: {e}", exc_info=True)
        return False, str(e)


def _parse_reporter(body):
    for part in body.splitlines():
        if part.startswith("Reported by: "):
            return part[len("Reported by: "):]
    return "unknown"


def _poll_topic(topic, since_seconds):
    """Yield parsed ntfy message dicts for the topic over the given window."""
    url = f"https://ntfy.sh/{topic}/json?poll=1&since={int(since_seconds)}s"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            for raw in resp:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("event") == "message":
                    yield msg
    except Exception as e:
        logging.debug(f"Could not poll ntfy topic: {e}")


def topic_has_recent_alert(topic, site_name, since_seconds, node_name=None):
    """Return the reporting node name if a different node sent a down-alert for site_name recently, else None."""
    for msg in _poll_topic(topic, since_seconds):
        if msg.get("title") != f"{site_name} is down":
            continue
        reporter = _parse_reporter(msg.get("message", ""))
        if node_name and reporter == node_name:
            continue
        return reporter
    return None


def sync_state_from_topic(topic, site_names, since_seconds, node_name, state, now):
    """Update local state from remote alerts so we reflect what other nodes have observed."""
    for msg in _poll_topic(topic, since_seconds):
        title = msg.get("title", "")
        reporter = _parse_reporter(msg.get("message", ""))
        if reporter == node_name:
            continue
        msg_time = msg.get("time", now)
        for name in site_names:
            if title == f"{name} is down":
                site_state = state.setdefault(name, {"status": None, "last_alert": 0, "down_since": None})
                if site_state.get("status") != "down":
                    site_state["status"] = "down"
                    if site_state.get("down_since") is None:
                        site_state["down_since"] = msg_time
                    logging.info(f"{name}: state updated to down (reported by {reporter})")
                if msg_time > site_state.get("last_alert", 0):
                    site_state["last_alert"] = msg_time
                    site_state["last_alert_node"] = reporter
            elif title == f"{name} is back up":
                site_state = state.setdefault(name, {"status": None, "last_alert": 0, "down_since": None})
                if site_state.get("status") != "up":
                    site_state["status"] = "up"
                    site_state["down_since"] = None
                    logging.info(f"{name}: state updated to up (reported by {reporter})")


def notify(topic, title, message, priority="default", tags=None):
    headers = {"Title": title, "Priority": priority}
    if tags:
        headers["Tags"] = ",".join(tags)
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode(),
        headers=headers,
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logging.warning(f"Failed to send ntfy notification: {e}")


def resolve_timezone(tz_name):
    if tz_name is None:
        return None
    if not HAS_ZONEINFO:
        logging.warning(f"zoneinfo unavailable (Python < 3.9); ignoring timezone '{tz_name}', using local time")
        return None
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        logging.warning(f"Unknown timezone '{tz_name}'; using local time")
        return None


def in_quiet_hours(quiet_hours, tz, _now=None):
    start = datetime.time(*map(int, quiet_hours["start"].split(":")))
    end = datetime.time(*map(int, quiet_hours["end"].split(":")))
    now = (_now or datetime.datetime.now(tz)).time().replace(second=0, microsecond=0)
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end  # overnight window e.g. 23:00–07:00


_DAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def in_quiet_days(quiet_days, tz, _now=None):
    today = (_now or datetime.datetime.now(tz)).weekday()
    return today in {_DAY_NAMES[d.lower()] for d in quiet_days}


def fmt_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60}s"


def main():
    parser = argparse.ArgumentParser(description="Website uptime monitor")
    parser.add_argument("config", help="Path to TOML config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show debug output including response headers and body excerpts")
    args = parser.parse_args()

    config_dir = Path(args.config).parent
    with open(args.config, "rb") as f:
        config = tomllib.load(f)

    log_file = config.get("log_file")
    handlers = [logging.StreamHandler()]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )

    # Resolve ntfy topic: explicit string takes precedence, else read from file
    if "ntfy_topic" in config:
        ntfy_topic = config["ntfy_topic"]
    elif "ntfy_topic_file" in config:
        ntfy_topic = Path(config["ntfy_topic_file"]).read_text().strip()
    else:
        sys.exit("Config must include 'ntfy_topic' or 'ntfy_topic_file'")

    node_name = config.get("node_name", socket.gethostname())
    state_file = config["state_file"]
    alert_interval = config.get("alert_interval_seconds", 3600)
    timeout = config.get("request_timeout_seconds", 10)
    default_ua = config.get("user_agent", DEFAULT_UA)
    global_retries = min(config.get("retries", 2), 5)
    sites = config.get("sites", [])
    global_tz = resolve_timezone(config.get("timezone"))
    global_quiet = config.get("quiet_hours")
    global_quiet_days = config.get("quiet_days")

    state = load_state(state_file)
    now = time.time()

    site_names = [s.get("name", s["url"]) for s in sites]
    sync_state_from_topic(ntfy_topic, site_names, alert_interval, node_name, state, now)

    for site in sites:
        url = site["url"]
        name = site.get("name", url)
        keyword = site.get("keyword", "")

        username = site.get("username")
        password = site.get("password")
        if "auth_file" in site:
            auth_path = Path(site["auth_file"])
            if not auth_path.is_absolute() and not auth_path.exists():
                auth_path = config_dir / auth_path
            creds = auth_path.read_text().strip()
            username, _, password = creds.partition(":")

        user_agent = site.get("user_agent", default_ua)
        retries = min(site.get("retries", global_retries), 5)

        tz = resolve_timezone(site.get("timezone")) if "timezone" in site else global_tz
        site_quiet_cfg = site.get("quiet_hours", global_quiet)
        site_quiet_days_cfg = site.get("quiet_days", global_quiet_days)
        quiet_now = (
            (isinstance(site_quiet_cfg, dict) and in_quiet_hours(site_quiet_cfg, tz)) or
            (isinstance(site_quiet_days_cfg, list) and in_quiet_days(site_quiet_days_cfg, tz))
        )

        up, reason = check_site(url, keyword, timeout, username, password, user_agent)
        for attempt in range(retries):
            if up:
                break
            delay = 2 ** attempt
            logging.debug(f"{name}: attempt {attempt + 1} failed ({reason}), retrying in {delay}s")
            time.sleep(delay)
            up, reason = check_site(url, keyword, timeout, username, password, user_agent)
        site_state = state.setdefault(name, {"status": None, "last_alert": 0, "down_since": None})
        prev_status = site_state["status"]

        if up:
            logging.debug(f"{name}: up")
            if prev_status == "down":
                down_since = site_state.get("down_since") or now
                msg = f"{name} recovered (was down for {fmt_duration(now - down_since)}).\n{url}\nReported by: {node_name}"
                if quiet_now:
                    logging.debug(f"{name}: in quiet period, suppressing recovery notification")
                else:
                    notify(ntfy_topic, f"{name} is back up", msg, tags=["white_check_mark"])
                    logging.info(f"{name}: recovery notification sent")
            site_state.update(status="up", down_since=None)
        else:
            logging.warning(f"{name}: down — {reason}")
            if prev_status != "down":
                site_state["down_since"] = now
            site_state["status"] = "down"
            last_alert = site_state.get("last_alert", 0)
            if quiet_now:
                logging.debug(f"{name}: in quiet period, suppressing alert")
            elif now - last_alert >= alert_interval:
                down_since = site_state.get("down_since") or now
                duration = fmt_duration(now - down_since)
                msg = f"{name} is unreachable ({reason}).\nDown for: {duration}\n{url}\nReported by: {node_name}"
                reporter = topic_has_recent_alert(ntfy_topic, name, alert_interval, node_name)
                if reporter:
                    site_state["last_alert"] = now
                    site_state["last_alert_node"] = reporter
                    logging.info(f"{name}: alert suppressed (already notified by {reporter})")
                else:
                    notify(ntfy_topic, f"{name} is down", msg, priority="high", tags=["rotating_light"])
                    site_state["last_alert"] = now
                    site_state["last_alert_node"] = node_name
                    logging.warning(f"{name}: alert sent")

    save_state(state_file, state)


if __name__ == "__main__":
    main()
