#!/bin/zsh
set -euo pipefail

LABEL="com.stan.activity-radar.push"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$TARGET"
echo "unloaded $LABEL"
