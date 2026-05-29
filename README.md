# nudnik

A cron-friendly website uptime monitor that sends alerts via [ntfy.sh](https://ntfy.sh).

Nudnik checks each site for an HTTP 200 and an optional keyword in the response body. When a site goes down it fires an alert; when it recovers it fires a recovery notification. Repeated alerts for the same site are rate-limited so you don't get spammed.

## Requirements

Python 3.11+. No third-party dependencies.

For Python 3.10 and earlier, install `tomli`:

```
pip install tomli
```

## Setup

```sh
cp nudnik.example.toml nudnik.toml
$EDITOR nudnik.toml
python3 nudnik.py nudnik.toml
```

## Config

```toml
# ntfy.sh topic — use one of these two options:
ntfy_topic = "your-topic-here"
# ntfy_topic_file = "/run/secrets/ntfy_topic"  # read topic from a file

log_file = "/var/log/nudnik.log"
state_file = "/var/lib/nudnik/state.json"

# Minimum seconds between repeated down-alerts for the same site (default: 3600)
alert_interval_seconds = 3600

# Per-request timeout in seconds (default: 10)
request_timeout_seconds = 10

# Override the User-Agent sent with requests (default: Firefox on Linux)
# user_agent = "..."

[[sites]]
name = "My App"
url = "https://example.com/health"
keyword = "ok"

[[sites]]
name = "Private Dashboard"
url = "https://internal.example.com/health"
keyword = "ok"
username = "monitor"
password = "secret"
# user_agent = "..."  # per-site override
```

All `[[sites]]` fields except `url`:

| Field | Required | Description |
|---|---|---|
| `name` | no | Display name used in alerts (defaults to URL) |
| `keyword` | no | Case-insensitive string that must appear in the response body |
| `username` / `password` | no | HTTP Basic Auth credentials |
| `user_agent` | no | Overrides the global `user_agent` for this site |

## Cron

```
*/10 * * * * /path/to/nudnik.py /path/to/nudnik.toml
```

## Running on multiple machines

Nudnik coordinates across instances via the ntfy topic: before sending a down-alert it polls the topic history and suppresses the notification if another instance already sent one within `alert_interval_seconds`. No shared infrastructure needed.

To avoid duplicate alerts while still maintaining continuous coverage, stagger the cron jobs across machines:

```
# Machine A — runs at :00, :10, :20 ...
*/10 * * * * /path/to/nudnik.py /path/to/nudnik.toml

# Machine B — runs at :05, :15, :25 ...
5-59/10 * * * * /path/to/nudnik.py /path/to/nudnik.toml
```

## Verbose mode

Pass `-v` to see response headers and body excerpts for failing checks:

```sh
python3 nudnik.py -v nudnik.toml
```
