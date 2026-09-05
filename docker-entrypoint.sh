#!/bin/sh
set -e
data="${DATA_DIR:-/data}"
mkdir -p "$data"
if [ "$(id -u)" = "0" ]; then
  chown -R app:app "$data"
  exec runuser -u app -- "$@"
fi
exec "$@"
