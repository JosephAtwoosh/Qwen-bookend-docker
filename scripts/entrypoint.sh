#!/usr/bin/env bash
# Container entrypoint. Keeps the CLI surface identical to ltx25-docker's:
# the first argument is the verb, everything after it is passed through.
set -euo pipefail

case "${1:-doctor}" in
  download)
    exec /app/scripts/download.sh
    ;;
  prep|batch|check|style-pack|pack|doctor)
    exec python3 -m bookends "$@"
    ;;
  shell)
    exec /bin/bash
    ;;
  *)
    exec "$@"
    ;;
esac
