#!/bin/bash
# Installs keka-reminder into ~/.keka-reminder and loads a launchd agent.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$HOME/.keka-reminder"
LABEL="com.$(whoami).keka-reminder"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
INTERVAL="${INTERVAL:-600}"
PYTHON="$(command -v python3)"

mkdir -p "$APP_DIR" "$HOME/Library/LaunchAgents"
cp "$SRC/keka_reminder.py" "$APP_DIR/"
chmod +x "$APP_DIR/keka_reminder.py"
# Keep an existing config so reinstalling doesn't wipe your edits.
[ -f "$APP_DIR/config.json" ] || cp "$SRC/config.json" "$APP_DIR/"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$APP_DIR/keka_reminder.py</string>
    <string>check</string>
  </array>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$APP_DIR/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$APP_DIR/launchd.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# Global `keka` command, e.g. `keka status`, `keka set-tokens <refresh_token>`.
mkdir -p "$HOME/.local/bin"
ln -sf "$APP_DIR/keka_reminder.py" "$HOME/.local/bin/keka"

echo "Installed. Checks every $((INTERVAL / 60)) min using $PYTHON."
echo "Next: keka set-tokens <refresh_token>   (needs ~/.local/bin on PATH)"
