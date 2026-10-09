#!/usr/bin/env python3
"""Keka clock-in reminder for macOS.

Run by launchd every few minutes. Inside the configured window on workdays it
checks Keka's attendance API; if there is no clock-in for today it nudges you.
Any failure (expired token, network error, unexpected response) also nudges,
so a broken check never silently swallows a reminder.

Commands:
  check        default; what launchd runs
  status       print today's clock-in state (no notification)
  set-tokens   store tokens in the macOS Keychain: `set-tokens <refresh_token>`,
               or no argument to be prompted
  test-notify  send a test notification; `test-notify expired|error` to hear
               the other sounds
  done         mark today as done manually (stops reminders today)
  skip         same as done, for leave/holidays
  reset        clear today's manual done/skip
"""

import base64
import datetime as dt
import getpass
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

APP_DIR = os.path.expanduser("~/.keka-reminder")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STATE_DIR = os.path.join(APP_DIR, "state")
LOG_PATH = os.path.join(APP_DIR, "reminder.log")
KEYCHAIN_SERVICE = "keka-reminder"
# Keka's Azure gateway rejects the default Python-urllib user agent with a 403.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
)

DEFAULT_CONFIG = {
    "base_url": "https://cloverbay.keka.com",
    "attendance_path": "/k/dashboard/api/mytime/attendance/attendancedayrequests",
    "token_url": "",
    "client_id": "",
    "workdays": [0, 1, 2, 3, 4],
    "window_start": "09:30",
    "window_end": "13:00",
    "holidays": [],
    "show_dialog": True,
    "timeout_seconds": 15,
    "idle_minutes": 5,
    "sounds": {"reminder": "default", "expired": "Basso", "error": "Funk"},
}


# ---------- utilities ----------

def log(msg):
    os.makedirs(APP_DIR, exist_ok=True)
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}\n"
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 512_000:
            os.replace(LOG_PATH, LOG_PATH + ".1")
        with open(LOG_PATH, "a") as f:
            f.write(line)
    except OSError:
        pass


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    return cfg


def today():
    return dt.date.today()


def parse_hhmm(s):
    h, m = s.split(":")
    return dt.time(int(h), int(m))


# ---------- keychain ----------

def kc_get(account):
    r = subprocess.run(
        ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def kc_set(account, value):
    subprocess.run(
        ["security", "add-generic-password", "-U", "-s", KEYCHAIN_SERVICE, "-a", account, "-w", value],
        check=True, capture_output=True,
    )


# ---------- manual done/skip markers ----------

def marker_path(day=None):
    return os.path.join(STATE_DIR, f"done-{(day or today()).isoformat()}")


def is_marked_done():
    return os.path.exists(marker_path())


def mark_done(reason):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(marker_path(), "w") as f:
        f.write(reason)
    # Tidy up markers older than a week.
    cutoff = today() - dt.timedelta(days=7)
    for name in os.listdir(STATE_DIR):
        try:
            if dt.date.fromisoformat(name.removeprefix("done-")) < cutoff:
                os.remove(os.path.join(STATE_DIR, name))
        except ValueError:
            pass


# ---------- notifications ----------

def _osa_quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _terminal_notifier():
    # launchd's PATH doesn't include Homebrew, so check the usual locations.
    for path in ("/opt/homebrew/bin/terminal-notifier", "/usr/local/bin/terminal-notifier"):
        if os.path.exists(path):
            return path
    return None


def _system_alert_sound():
    """Name of the alert sound chosen in System Settings > Sound, for the
    osascript fallback, which has no "default" option."""
    r = subprocess.run(["defaults", "read", "-g", "com.apple.sound.beep.sound"], capture_output=True, text=True)
    name = os.path.splitext(os.path.basename(r.stdout.strip()))[0]
    return name or "Glass"


def notify(title, message, open_url=None, sound="default"):
    """Clicking the notification opens open_url when terminal-notifier is
    installed and allowed; otherwise falls back to a plain notification.
    sound is "default" or a name from /System/Library/Sounds."""
    tn = _terminal_notifier()
    if tn:
        args = [tn, "-title", title, "-message", message, "-sound", sound, "-group", "keka-reminder"]
        if open_url:
            args += ["-open", open_url]
        if subprocess.run(args, capture_output=True).returncode == 0:
            return
    script = (
        f"display notification {_osa_quote(message)} with title {_osa_quote(title)} "
        f"sound name {_osa_quote(_system_alert_sound() if sound == 'default' else sound)}"
    )
    subprocess.run(["osascript", "-e", script], capture_output=True)


def dialog(message, cfg):
    """Returns the button pressed, or None if it timed out."""
    script = (
        f"display dialog {_osa_quote(message)} with title \"Keka\" "
        "buttons {\"Done for today\", \"Later\", \"Open Keka\"} default button \"Open Keka\" "
        "giving up after 120"
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    out = r.stdout.strip()
    if "gave up:true" in out or not out:
        return None
    return out.split("button returned:")[-1].split(",")[0].strip()


def nudge(cfg, message, kind="reminder"):
    """kind picks the sound from cfg["sounds"]: reminder, expired or error."""
    log(f"nudge ({kind}): {message}")
    sound = cfg["sounds"].get(kind, "default")
    notify("Keka: clock in", f"{message} Click to open Keka.", open_url=cfg["base_url"], sound=sound)
    if not cfg.get("show_dialog"):
        return
    choice = dialog(message, cfg)
    if choice == "Open Keka":
        subprocess.run(["open", cfg["base_url"]])
    elif choice == "Done for today":
        mark_done("dialog")
        log("marked done via dialog")


# ---------- auth ----------

def jwt_exp(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except (IndexError, ValueError):
        return None


def token_is_fresh(token, margin=120):
    exp = jwt_exp(token)
    return exp is not None and exp - margin > dt.datetime.now().timestamp()


def refresh_access_token(cfg):
    refresh = kc_get("refresh_token")
    if not (refresh and cfg.get("token_url") and cfg.get("client_id")):
        return None
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": cfg["client_id"],
    }).encode()
    req = urllib.request.Request(
        cfg["token_url"], data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": USER_AGENT,
            "Origin": cfg["base_url"].rstrip("/"),
            "Referer": cfg["base_url"].rstrip("/") + "/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout_seconds"]) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, ValueError) as e:
        log(f"refresh failed: {e}")
        return None
    access = data.get("access_token")
    if not access:
        log("refresh response had no access_token")
        return None
    kc_set("access_token", access)
    if data.get("refresh_token"):
        kc_set("refresh_token", data["refresh_token"])
    log("refreshed access token")
    return access


def get_access_token(cfg):
    token = kc_get("access_token")
    if token and token_is_fresh(token):
        return token
    return refresh_access_token(cfg)


# ---------- attendance ----------

class CheckError(Exception):
    pass


def fetch_attendance(cfg, token):
    req = urllib.request.Request(
        cfg["base_url"].rstrip("/") + cfg["attendance_path"],
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": USER_AGENT,
            "Referer": cfg["base_url"].rstrip("/") + "/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg["timeout_seconds"]) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise CheckError("session expired")
        raise CheckError(f"HTTP {e.code}")
    except urllib.error.URLError as e:
        raise CheckError(f"network error: {e.reason}")
    except ValueError:
        raise CheckError("response was not JSON")


def _iter_time_entries(node):
    """Yield every dict found inside any 'timeEntries' list, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "timeEntries" and isinstance(v, list):
                yield from (e for e in v if isinstance(e, dict))
            else:
                yield from _iter_time_entries(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_time_entries(item)


def _entry_local_datetime(entry):
    for key in ("timestamp", "actualTimestamp", "originalTimestamp"):
        raw = entry.get(key)
        if isinstance(raw, str) and len(raw) >= 19:
            try:
                parsed = dt.datetime.fromisoformat(raw[:19] + raw[19:].replace("Z", "+00:00").lstrip("0123456789."))
            except ValueError:
                continue
            if parsed.tzinfo:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed
    return None


def today_punches(data):
    """Today's non-deleted punches as sorted (local datetime, punchStatus)
    pairs. punchStatus 0 = clock-in, 1 = clock-out."""
    found_any = False
    punches = []
    for entry in _iter_time_entries(data):
        found_any = True
        if entry.get("isDeleted") or entry.get("deleted"):
            continue
        when = _entry_local_datetime(entry)
        if when and when.date() == today() and entry.get("punchStatus") in (0, 1):
            punches.append((when, entry["punchStatus"]))
    if not found_any and not _looks_like_attendance(data):
        raise CheckError("unexpected response shape")
    return sorted(punches)


def clocked_in_today(data):
    """True if there's a non-deleted clock-in dated today."""
    return any(status == 0 for _, status in today_punches(data))


def _looks_like_attendance(data):
    text = json.dumps(data)[:20000]
    return "webclockin" in text or "attendanceDate" in text


def current_status(cfg):
    """Returns (logged_in_today: bool, punches). Raises CheckError on failure."""
    token = get_access_token(cfg)
    if not token:
        raise CheckError("session expired")
    punches = today_punches(fetch_attendance(cfg, token))
    return any(status == 0 for _, status in punches), punches


def describe(punches, color=False):
    """One-line state: currently clocked in (green) or out / not yet (red)."""
    green, red, dim, reset = ("\033[32m", "\033[31m", "\033[2m", "\033[0m") if color else ("", "", "", "")
    if not punches:
        return f"{red}● Not clocked in yet{reset}"
    when, status = punches[-1]
    first_in = next((w for w, s in punches if s == 0), None)
    since = f" {dim}· first in {first_in:%H:%M}{reset}" if first_in else ""
    if status == 0:
        return f"{green}● Clocked in since {when:%H:%M}{reset}{since}"
    return f"{red}● Clocked out at {when:%H:%M}{reset}{since}"


# ---------- commands ----------

def in_window(cfg, now=None):
    now = now or dt.datetime.now()
    if now.weekday() not in cfg["workdays"]:
        return False
    if now.date().isoformat() in cfg["holidays"]:
        return False
    return parse_hhmm(cfg["window_start"]) <= now.time() <= parse_hhmm(cfg["window_end"])


def idle_seconds():
    """Seconds since the last keyboard/mouse/trackpad input."""
    out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "HIDIdleTime" in line:
            return int(line.rsplit("=", 1)[1].strip()) / 1e9
    return 0


def screen_locked():
    out = subprocess.run(["ioreg", "-n", "Root", "-d1"], capture_output=True, text=True).stdout
    return '"CGSSessionScreenIsLocked"=Yes' in out


def user_away(cfg):
    return screen_locked() or idle_seconds() > cfg["idle_minutes"] * 60


def status_with_retry(cfg, attempts=4, delay=15):
    """Right after wake, Wi-Fi may not be back yet; retry network errors
    for up to a minute before giving up."""
    for attempt in range(attempts):
        try:
            return current_status(cfg)
        except CheckError as e:
            if not str(e).startswith("network error") or attempt == attempts - 1:
                raise
            time.sleep(delay)


def cmd_check(cfg):
    if not in_window(cfg) or is_marked_done():
        return
    if user_away(cfg):
        log("user away or screen locked; skipping")
        return
    try:
        logged_in, _ = status_with_retry(cfg)
    except CheckError as e:
        msg = str(e)
        if msg == "session expired":
            nudge(cfg, "Keka session expired. Clock in, then run: keka set-tokens <refresh_token>", kind="expired")
        else:
            nudge(cfg, f"Couldn't verify your clock-in ({msg}). Have you clocked in?", kind="error")
        return
    if logged_in:
        mark_done("api")
        log("clock-in detected; done for today")
    else:
        nudge(cfg, "You haven't clocked in on Keka yet.")


MARKER_REASONS = {
    "api": "clock-in detected",
    "dialog": "marked done from reminder",
    "done": "marked done",
    "skip": "skipped",
}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _read_marker():
    try:
        with open(marker_path()) as f:
            return f.read().strip()
    except OSError:
        return None


def cmd_status(cfg):
    color = sys.stdout.isatty()
    bold, dim, red, reset = ("\033[1m", "\033[2m", "\033[31m", "\033[0m") if color else ("", "", "", "")

    try:
        _, punches = current_status(cfg)
        state, failed = describe(punches, color), False
    except CheckError as e:
        state, failed = f"{red}● Couldn't check Keka: {e}{reset}", True

    marker = _read_marker()
    if marker is not None:
        reminders = f"Off for today ({MARKER_REASONS.get(marker, marker)})"
    elif in_window(cfg):
        reminders = f"Active until {cfg['window_end']}"
    else:
        reminders = "Idle (outside reminder window)"

    days = cfg["workdays"]
    if days == list(range(days[0], days[-1] + 1)) and len(days) > 1:
        day_text = f"{DAY_NAMES[days[0]]}–{DAY_NAMES[days[-1]]}"
    else:
        day_text = ", ".join(DAY_NAMES[d] for d in days)

    token = kc_get("access_token")
    exp = jwt_exp(token) if token else None
    expiry = dt.datetime.fromtimestamp(exp).strftime("%a %-d %b, %H:%M") if exp else "Not set"
    refresh = "Automatic" if kc_get("refresh_token") else "Manual (no refresh token)"

    rows = [
        ("Reminders", reminders),
        ("Schedule", f"{cfg['window_start']}–{cfg['window_end']}, {day_text}"),
        ("Token expires", expiry),
        ("Token renewal", refresh),
    ]
    print(f"{bold}Keka{reset} {dim}· {dt.datetime.now():%a %-d %b, %H:%M}{reset}")
    print(f"  {state}")
    print()
    for label, value in rows:
        print(f"  {dim}{label:<16}{reset}{value}")
    if failed:
        sys.exit(1)


def cmd_set_tokens(cfg, token=None):
    """`set-tokens <token>` takes a refresh token (or a JWT access token);
    with no argument it prompts for both."""
    if token:
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if token.startswith("eyJ"):
            kc_set("access_token", token)
            print("access token saved")
        else:
            kc_set("refresh_token", token)
            # Drop the old access token so the new refresh token is used now.
            subprocess.run(
                ["security", "delete-generic-password", "-s", KEYCHAIN_SERVICE, "-a", "access_token"],
                capture_output=True,
            )
            print("refresh token saved")
    else:
        access = getpass.getpass("Access token (Bearer value, hidden; Enter to skip): ").strip()
        if access.lower().startswith("bearer "):
            access = access[7:].strip()
        if access:
            kc_set("access_token", access)
            print("access token saved")
        refresh = getpass.getpass("Refresh token (hidden; Enter to skip): ").strip()
        if refresh:
            kc_set("refresh_token", refresh)
            print("refresh token saved")
    try:
        _, punches = current_status(cfg)
        print(f"Saved. Keka says: {describe(punches, color=sys.stdout.isatty())}")
    except CheckError as e:
        print(f"saved, but keka check failed: {e}")
        sys.exit(1)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    cfg = load_config()
    if cmd == "check":
        cmd_check(cfg)
    elif cmd == "status":
        cmd_status(cfg)
    elif cmd == "set-tokens":
        cmd_set_tokens(cfg, sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "test-notify":
        kind = sys.argv[2] if len(sys.argv) > 2 else "reminder"
        sound = cfg["sounds"].get(kind, "default")
        notify("Keka: clock in", f"Test {kind} notification ({sound} sound). Click to open Keka.",
               open_url=cfg["base_url"], sound=sound)
    elif cmd in ("done", "skip"):
        mark_done(cmd)
        print(f"marked {cmd} for {today()}")
    elif cmd == "reset":
        if is_marked_done():
            os.remove(marker_path())
        print(f"cleared marker for {today()}")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
