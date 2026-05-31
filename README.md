# nudnik

A cron-friendly server health monitor that sends alerts via [ntfy.sh](https://ntfy.sh).

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

# Identifier included in alert messages (defaults to system hostname)
# node_name = "prod-monitor-us-east"

# Minimum seconds between repeated down-alerts for the same site (default: 3600)
alert_interval_seconds = 3600

# Per-request timeout in seconds (default: 10)
request_timeout_seconds = 10

# Override the User-Agent sent with requests (default: Firefox on Linux)
# user_agent = "..."

# Retry a failed check before treating the site as down (default: 2, max: 5).
# Delays use exponential backoff starting at 1s (1s, 2s, 4s, ...).
# retries = 2

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
# auth_file = "/run/secrets/creds"  # alternative: file containing "username:password"
# user_agent = "..."  # per-site override
# retries = 0         # per-site override
```

All `[[sites]]` fields:

| Field | Required | Description |
|---|---|---|
| `url` | yes (HTTP) | URL to check |
| `host` | yes (ping) | Hostname or IP address to ping |
| `method` | no | `"http"` (default) or `"ping"` |
| `name` | no | Display name used in alerts (defaults to URL or host) |
| `keyword` | no | Case-insensitive string that must appear in the response body |
| `username` / `password` | no | HTTP Basic Auth credentials |
| `auth_file` | no | Path to a file containing `username:password` (takes precedence over `username`/`password`) |
| `user_agent` | no | Overrides the global `user_agent` for this site |
| `retries` | no | Overrides the global `retries` for this site |
| `timezone` | no | Overrides the global `timezone` for quiet-hours evaluation |
| `quiet_hours` | no | Overrides the global `quiet_hours` for this site |
| `quiet_days` | no | Overrides the global `quiet_days` for this site |

## Splitting config with `include`

A config file can pull in one or more other TOML files:

```toml
include = ["base.toml", "extra-sites.toml"]
```

Included files are merged in order before the main file, so the main file's values always win. `[[sites]]` lists are concatenated (included sites first). Paths are relative to the including file, so you can keep everything in the same directory without worrying about where you invoke nudnik from.

Includes can be nested — an included file can itself include others. Circular references are silently ignored. Missing included files produce a warning and are skipped.

Use `includedir` to load every `*.toml` file from a directory (sorted alphabetically), which is handy for a `conf.d`-style layout:

```toml
includedir = "conf.d"
```

Both `include` and `includedir` accept either a string (single path) or a list. `include` files are processed first, then `includedir` files, then the main file wins.

You can also pass multiple files and/or directories on the command line — nudnik merges them in the order given, with later arguments winning for scalar settings and `[[sites]]` entries concatenated. Duplicate paths are silently skipped.

```sh
python3 nudnik.py base.toml overrides.toml
python3 nudnik.py /etc/nudnik/conf.d/ local.toml
```

Passing a single directory works too:

```sh
python3 nudnik.py /etc/nudnik/conf.d/
```

A common pattern is a shared base config for settings like `ntfy_topic` and `alert_interval_seconds`, included by several per-deployment configs that each add their own sites:

```toml
# base.toml
ntfy_topic_file = "/run/secrets/ntfy_topic"
alert_interval_seconds = 3600
request_timeout_seconds = 10
retries = 2
```

```toml
# my_node.toml
include = ["base.toml"]
node_name = "my_node"
state_file = "/var/lib/nudnik/my_node.json"

[[sites]]
name = "My App"
url = "https://example.com/health"
keyword = "ok"
```

## Cron

```
*/10 * * * * /path/to/nudnik.py /path/to/nudnik.toml
```

## Running on multiple machines

Nudnik coordinates across instances via the ntfy topic. At startup it polls the topic history and updates local state based on what other nodes have reported — so a node that was offline when a site went down will immediately know about it when it comes back. Before sending a down-alert it also checks whether another node already sent one within `alert_interval_seconds` and suppresses its own if so.

Each alert includes a `Reported by: <node_name>` line (hostname by default) so you can tell which machine sent it. Set `node_name` in your config if you want something more descriptive than the hostname.

To avoid duplicate alerts while still maintaining continuous coverage, stagger the cron jobs across machines:

```
# Machine A — runs at :00, :10, :20 ...
*/10 * * * * /path/to/nudnik.py /path/to/nudnik.toml

# Machine B — runs at :05, :15, :25 ...
5-59/10 * * * * /path/to/nudnik.py /path/to/nudnik.toml
```

## Ping monitoring

To monitor a host by ICMP ping rather than HTTP, use `method = "ping"`:

```toml
[[sites]]
name = "Router"
method = "ping"
host = "192.168.1.1"
```

The `host` field is required; `name` defaults to the host address. Timeout is controlled by the global `request_timeout_seconds`. Works on Linux, macOS, and Windows.

## Flags

### `-v` / `--verbose`

Show response headers and body excerpts for failing checks:

```sh
python3 nudnik.py -v nudnik.toml
```

### `--check`

Validate config and print the resolved settings, then exit — no checks are run and nothing is sent:

```sh
python3 nudnik.py --check nudnik.toml
```

### `--dry-run`

Run all checks but skip notifications and state saves — nothing is written to disk or sent to ntfy. Useful for testing config changes. Logs what would have been sent:

```sh
python3 nudnik.py --dry-run nudnik.toml
python3 nudnik.py -v --dry-run nudnik.toml  # Also useful: show more output
```
