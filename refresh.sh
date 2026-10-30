#!/bin/sh
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

DO_PULL=1
for arg in "$@"; do
  case "$arg" in
    --no-pull) DO_PULL=0 ;;
    -h|--help)
      echo "Usage: $0 [--no-pull]"
      echo "  Default: git pull --ff-only (if .git exists), then restart systemd units."
      echo "  --no-pull: skip git, only restart services."
      exit 0
      ;;
  esac
done

HABIT_UNIT="${HABIT_UNIT:-habit-wallpaper.service}"
TUNNEL_UNIT="${TUNNEL_UNIT:-cloudflared-habit.service}"

if [ "$DO_PULL" -eq 1 ] && [ -d .git ]; then
  git pull --ff-only
fi

sudo systemctl restart "$HABIT_UNIT"
sudo systemctl restart "$TUNNEL_UNIT"

sudo systemctl --no-pager --lines=0 status "$HABIT_UNIT" "$TUNNEL_UNIT" || true
echo ""
echo "=== Last 100 log lines ($HABIT_UNIT) ==="
sudo journalctl -u "$HABIT_UNIT" -n 100 --no-pager
echo ""
echo "OK — follow live: journalctl -u $HABIT_UNIT -f"
