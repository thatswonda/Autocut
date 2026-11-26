#!/bin/sh
# Container entrypoint. Mounted volumes (e.g. a Render persistent disk at /data) arrive owned by root,
# so prepare the data directory as root, then drop to the unprivileged "autoedit" user for the app.
set -e
DATA_DIR="${AUTOEDIT_DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R autoedit:autoedit "$DATA_DIR"
  export HOME=/home/autoedit
  exec setpriv --reuid=autoedit --regid=autoedit --init-groups "$@"
fi
exec "$@"
