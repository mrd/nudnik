#!/usr/bin/env python3

import argparse
import base64
import json
import logging
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


def topic_has_recent_alert(topic, site_name, since_seconds):
    """Return True if another instance already sent a down-alert for site_name recently."""
    url = f"https://ntfy.sh/{topic}/json?poll=1&since={int(since_seconds)}s"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            for line in resp:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("event") != "message":
                    continue
                if msg.get("title") == f"{site_name} is down":
                    return True
    except Exception as e:
        logging.debug(f"Could not poll topic for remote state: {e}")
    return False


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

    state_file = config["state_file"]
    alert_interval = config.get("alert_interval_seconds", 3600)
    timeout = config.get("request_timeout_seconds", 10)
    default_ua = config.get("user_agent", DEFAULT_UA)
    sites = config.get("sites", [])

    state = load_state(state_file)
    now = time.time()

    for site in sites:
        url = site["url"]
        name = site.get("name", url)
        keyword = site.get("keyword", "")

        username = site.get("username")
        password = site.get("password")
        user_agent = site.get("user_agent", default_ua)
        up, reason = check_site(url, keyword, timeout, username, password, user_agent)
        site_state = state.setdefault(name, {"status": None, "last_alert": 0, "down_since": None})
        prev_status = site_state["status"]

        if up:
            logging.debug(f"{name}: up")
            if prev_status == "down":
                down_since = site_state.get("down_since") or now
                msg = f"{name} recovered (was down for {fmt_duration(now - down_since)}).\n{url}"
                notify(ntfy_topic, f"{name} is back up", msg, tags=["white_check_mark"])
                logging.info(f"{name}: recovery notification sent")
            site_state.update(status="up", down_since=None)
        else:
            logging.warning(f"{name}: down — {reason}")
            if prev_status != "down":
                site_state["down_since"] = now
            site_state["status"] = "down"
            last_alert = site_state.get("last_alert", 0)
            if now - last_alert >= alert_interval:
                down_since = site_state.get("down_since") or now
                duration = fmt_duration(now - down_since)
                msg = f"{name} is unreachable ({reason}).\nDown for: {duration}\n{url}"
                if topic_has_recent_alert(ntfy_topic, name, alert_interval):
                    site_state["last_alert"] = now
                    logging.info(f"{name}: alert suppressed (another instance already notified)")
                else:
                    notify(ntfy_topic, f"{name} is down", msg, priority="high", tags=["rotating_light"])
                    site_state["last_alert"] = now
                    logging.warning(f"{name}: alert sent")

    save_state(state_file, state)


if __name__ == "__main__":
    main()
