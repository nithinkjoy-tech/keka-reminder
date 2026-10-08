#!/bin/bash
# Removes the launchd agent, app folder, and Keychain entries.
set -uo pipefail

LABEL="com.$(whoami).keka-reminder"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -rf "$HOME/.keka-reminder"
rm -f "$HOME/.local/bin/keka"
security delete-generic-password -s keka-reminder -a access_token >/dev/null 2>&1
security delete-generic-password -s keka-reminder -a refresh_token >/dev/null 2>&1
echo "Uninstalled."
