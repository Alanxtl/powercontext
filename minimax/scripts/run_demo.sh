#!/usr/bin/env bash
# Run the complete SDD context flow: import -> test -> context -> MiniMax -> test -> state -> resume.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${root}:${PYTHONPATH:-}"
cd "${root}"

: "${MINIMAX_API_KEY:?MINIMAX_API_KEY must be exported}"
export MINIMAX_BASE_URL="${MINIMAX_BASE_URL:-https://api.minimax.io/v1}"
export MINIMAX_MODEL="${MINIMAX_MODEL:-MiniMax-M3.1-Flash}"

exec python -m sdd_context demo "$@"
