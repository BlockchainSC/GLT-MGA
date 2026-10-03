#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <reentrancy|timestamp> [seed]" >&2
  exit 2
fi

TASK="$1"
SEED="${2:-9930}"
case "$TASK" in
  reentrancy|timestamp) ;;
  *) echo "Unsupported task: $TASK" >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"
RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUT="$ROOT/runs/main/$TASK/seed${SEED}_${RUN_ID}"

mkdir -p "$OUT"
cd "$ROOT"
set -o pipefail

"$PYTHON" -u GLT-MGA.py \
  --task "$TASK" \
  --config-file "configs/main/${TASK}.json" \
  --random_seed "$SEED" \
  --thresholds 0.45 \
  --log_dir "$OUT" \
  2>&1 | tee "$OUT/run.log"

status=${PIPESTATUS[0]}
echo "Exit status: $status"
echo "Output: $OUT"
exit "$status"
