# keka-reminder

A macOS reminder that nags you every workday until you clock in on [Keka](https://www.keka.com/).

Every 10 minutes during your reminder window, it checks your Keka attendance. If you haven't clocked in today, you get a notification (click it to open Keka) and a dialog. Once it sees a clock-in, it stays quiet until the next workday.

Reminders only appear while you're using the Mac. Nothing runs while it's asleep, and checks are skipped while the screen is locked or you're away, so you don't come back to a pile of notifications.

If the check fails (expired session, no network), it still reminds you, so a broken check never means a missed clock-in.

> **Unofficial.** This tool isn't affiliated with Keka. It uses your own login session to read your own attendance. Check that your company's IT policy allows this before using it.

## Requirements

- macOS
- Python 3 (`/usr/bin/python3` is enough; no extra packages)
- Optional: [terminal-notifier](https://github.com/julienXX/terminal-notifier), for notifications you can click to open Keka

## Setup

### 1. Configure your company

Edit `config.json` before installing:

| Key | What to set |
| --- | --- |
| `base_url` | Your company's Keka URL, e.g. `https://yourcompany.keka.com` |
| `client_id` | Keka's app ID. See [Finding your client_id](#finding-your-client_id) |
| `window_start`, `window_end` | When reminders run, e.g. `09:30` to `13:00` |
| `workdays` | `0` = Monday ... `6` = Sunday. Default is Monday to Friday |
| `holidays` | Dates to skip, e.g. `["2026-12-25"]` |
| `show_dialog` | `true` to also show a dialog with an **Open Keka** button |
| `sounds` | Sound per reminder type: `reminder`, `expired`, `error`. Use `default` or a name from `/System/Library/Sounds` (Basso, Funk, Hero, Ping, ...) |
| `idle_minutes` | Skip reminders while the screen is locked or you've been idle this long (default `5`) |

Leave `token_url` and `attendance_path` as they are.

### 2. Install

```sh
bash install.sh
```

This copies the script to `~/.keka-reminder/`, starts a background job that runs every 10 minutes (and again after every restart), and adds a `keka` command to `~/.local/bin`. If `keka` isn't found afterwards, add this to your `~/.zshrc`:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

### 3. Add your refresh token

1. Log in to Keka in Chrome.
2. Open DevTools (`Cmd+Option+I`), then go to **Application → Local Storage → `https://yourcompany.keka.com`**.
3. Copy the value of `refresh_token`.
4. Run:

   ```sh
   keka set-tokens <refresh_token>
   ```

The token is stored in the macOS Keychain, never in a file. The command checks it against Keka right away and shows your status.

From then on the script renews its own access token. You only repeat this step if Keka ends your session, for example after a password change. You'll get a "session expired" reminder when that happens.

**Tip:** to keep your browser and the script from interfering with each other, copy the token from a Chrome **Incognito** window, then close that window **without signing out**. Also avoid clicking **Sign out** in Keka, which may end the script's session too.

### 4. Check it works

```sh
keka status
```

```
Keka · Thu 8 Oct, 12:57
  ● Clocked in since 12:07 · first in 11:25

  Reminders       Off for today (clock-in detected)
  Schedule        09:30–13:00, Mon–Fri
  Token expires   Fri 9 Oct, 12:33
  Token renewal   Automatic
```

### 5. Optional: clickable notifications

```sh
brew install terminal-notifier
keka test-notify
```

Then allow **terminal-notifier** in **System Settings → Notifications** (choose **Alerts** so reminders stay on screen). If it doesn't appear in the list, launch it once as an app:

```sh
open -a "$(brew --prefix terminal-notifier)/terminal-notifier.app"
```

Without terminal-notifier, you get standard macOS notifications, which can't open Keka when clicked.

## Commands

| Command | What it does |
| --- | --- |
| `keka status` | Shows whether you're clocked in, plus reminder and token status |
| `keka set-tokens <token>` | Saves a new refresh token and verifies it |
| `keka skip` | No reminders today (leave, holidays) |
| `keka done` | Marks today as done manually |
| `keka reset` | Undoes `skip` or `done` for today |
| `keka test-notify [reminder\|expired\|error]` | Sends a test notification with that type's sound |

## Finding your client_id

`client_id` identifies the Keka web app to Keka's login server. It isn't a secret. To find yours:

- In DevTools, go to **Application → Local Storage**, open `id_token_claims_obj`, and copy the value of `"aud"`.

Don't paste your tokens into websites like jwt.io to decode them.

## Changing settings

The installed config lives at `~/.keka-reminder/config.json`. Changes take effect on the next check, with no reinstall needed.

If you edit `keka_reminder.py` in this repo, run `bash install.sh` again to copy it over. Reinstalling keeps your existing config.

## Troubleshooting

- **Logs:** `~/.keka-reminder/reminder.log` (contains no tokens)
- **Is the background job running?** `launchctl list | grep keka-reminder`
- **"session expired":** copy a fresh `refresh_token` and run `keka set-tokens <token>`
- **No reminders while the Mac was asleep:** checks resume within about 10 minutes of waking

## Uninstall

```sh
bash uninstall.sh
```

Removes the background job, `~/.keka-reminder/`, the `keka` command, and the stored tokens from the Keychain.
