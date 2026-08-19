#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PLIST="$SCRIPT_DIR/com.stan.activity-radar.push.plist"
TARGET="$HOME/Library/LaunchAgents/com.stan.activity-radar.push.plist"

mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST" "$TARGET"
launchctl bootstrap "gui/$(id -u)" "$TARGET"
echo "loaded com.stan.activity-radar.push"
