#!/bin/bash
# Runs the daily 24h Pulse card and shows a macOS notification with the result.
# Meant for launchd (see ~/Library/LaunchAgents/com.agentid.dailycard.plist), but safe to run by hand.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/out/daily/run.log"
mkdir -p "$ROOT/out/daily"

{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  if /usr/bin/python3 "$ROOT/scripts/daily_card.py"; then
    /usr/bin/osascript -e 'display notification "Card + caption are in out/daily/" with title "Agent ID daily card ready"'
  else
    /usr/bin/osascript -e 'display notification "Generation failed, see out/daily/run.log" with title "Agent ID daily card"'
  fi
} >>"$LOG" 2>&1
