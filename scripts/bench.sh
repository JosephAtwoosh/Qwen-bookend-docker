#!/usr/bin/env bash
# Time N bookend generations so docs/VRAM.md can hold measurements, not guesses.
#
# The seeds are derived from the shot name, so --force re-renders byte-identical
# images: benchmarking overwrites approved frames with the same frames. It costs
# GPU time, not correctness.
#
#   ./scripts/bench.sh 3
set -euo pipefail
export MSYS_NO_PATHCONV=1
cd "$(dirname "${BASH_SOURCE[0]}")/.."

count="${1:-3}"
echo "benchmarking $count images through the configured profile..."
docker compose run --rm qwen batch --limit "$count" --force
echo
echo "record the mean/median above in docs/VRAM.md with the GPU and RAM it ran on."
