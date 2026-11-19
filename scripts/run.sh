#!/usr/bin/env bash
# Host-side helper, matching ltx25-docker's scripts/run.sh ergonomics.
#
#   ./scripts/run.sh build                      build the image
#   ./scripts/run.sh download                   cache ~58 GB of weights
#   ./scripts/run.sh prep                       extract shots, print the plan
#   ./scripts/run.sh batch                      render everything missing
#   ./scripts/run.sh check                      metrics + review pack
#   ./scripts/run.sh style-pack --styles pixar clay
#   ./scripts/run.sh pack --emit-render-sh
#   ./scripts/run.sh doctor
#   ./scripts/run.sh shell
#
# Path conversion is disabled so that /shots and friends reach the container as
# written when this is run from Git Bash on Windows.
set -euo pipefail
export MSYS_NO_PATHCONV=1

cd "$(dirname "${BASH_SOURCE[0]}")/.."

cmd="${1:-help}"
shift || true

mkdir -p models reference shots/in output

case "$cmd" in
  build)
    docker compose build "$@"
    ;;
  download)
    docker compose run --rm qwen download "$@"
    ;;
  prep|batch|check|style-pack|pack|doctor)
    docker compose run --rm qwen "$cmd" "$@"
    ;;
  shell)
    docker compose run --rm qwen shell
    ;;
  help|-h|--help)
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    ;;
  *)
    echo "unknown command: $cmd" >&2
    echo "try: ./scripts/run.sh help" >&2
    exit 2
    ;;
esac
