#!/bin/sh
# Copy wallpaper, Google data helper, and server into pcOutside habit-tracker tree, then run refresh there.
# Also rsyncs local images/ to the remote so new photos appear without copying the whole tree.
#
# Defaults: SSH host "pcOutside", remote dir ~/usefuleverydaycode/habit-tracker
#   REMOTE_HOST=user@box REMOTE_REL=my/habit-tracker ./remote_refresh.sh
#   REMOTE_DIR=/absolute/path ./remote_refresh.sh   # optional full path (no ~)
#
# Default runs sh refresh.py on the remote (override if you use refresh.sh here:
#   REFRESH_CMD='sh refresh.sh' ./remote_refresh.sh)
#
#   SYNC_IMAGES=0 ./remote_refresh.sh   # skip images/ rsync
set -eu

REMOTE_HOST="${REMOTE_HOST:-pcOutside}"
REFRESH_CMD="${REFRESH_CMD:-sh refresh.sh}"
REMOTE_DIR="${REMOTE_DIR:-~/habit-tracker}"
SYNC_IMAGES="${SYNC_IMAGES:-1}"

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

for f in gen_wallpaper.py google_data.py server.py; do
  if [ ! -f "$f" ]; then
    echo "missing: $ROOT/$f" >&2
    exit 1
  fi
done

if [ -n "${REMOTE_DIR:-}" ]; then
  scp_dest="${REMOTE_HOST}:${REMOTE_DIR}/"
  remote_cd="cd ${REMOTE_DIR}"
else
  REMOTE_REL="${REMOTE_REL:-usefuleverydaycode/habit-tracker}"
  scp_dest="${REMOTE_HOST}:~/${REMOTE_REL}/"
  remote_cd="cd ~/${REMOTE_REL}"
fi

scp gen_wallpaper.py google_data.py server.py "${scp_dest}"

if [ "$SYNC_IMAGES" -eq 1 ] && [ -d "${ROOT}/images" ]; then
  if command -v rsync >/dev/null 2>&1; then
    rsync -avz "${ROOT}/images/" "${scp_dest}images/"
  else
    echo "warning: rsync not found; skipping images/ sync (install rsync or set SYNC_IMAGES=0)" >&2
  fi
fi

# -t: allocate a pseudo-TTY so remote `sudo` (e.g. in refresh.sh) can prompt for a password
ssh -t "${REMOTE_HOST}" "${remote_cd} && ${REFRESH_CMD}"
