#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  echo "Usage: $0 <reentrancy|timestamp> <variant|all> [seed]" >&2
  exit 2
fi

TASK="$1"
REQUESTED_VARIANT="$2"
SEED="${3:-9930}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"
RUN_ID="$(date +%Y%m%d_%H%M%S)"

case "$TASK" in
  reentrancy) R=2; S=10 ;;
  timestamp) R=3; S=15 ;;
  *) echo "Unsupported task: $TASK" >&2; exit 2 ;;
esac

declare -A FOLDERS=(
  [ast]="GLT-MGA_AST"
  [cfg]="GLT-MGA_CFG"
  [dfg]="GLT-MGA_DFG"
  [without_ast]="GLT-MGA_without_AST"
  [without_cfg]="GLT-MGA_without_CFG"
  [without_dfg]="GLT-MGA_without_DFG"
  [local]="GLT-MGA_L"
  [full]="Full_GLT-MGA"
)

if [[ "$REQUESTED_VARIANT" == "all" ]]; then
  VARIANTS=(ast cfg dfg without_ast without_cfg without_dfg local full)
elif [[ -n "${FOLDERS[$REQUESTED_VARIANT]:-}" ]]; then
  VARIANTS=("$REQUESTED_VARIANT")
else
  echo "Unsupported variant: $REQUESTED_VARIANT" >&2
  exit 2
fi

cd "$ROOT"
for variant in "${VARIANTS[@]}"; do
  folder="${FOLDERS[$variant]}"
  train_file="train_data/ablation_study/$folder/$TASK/train.json"
  test_file="train_data/ablation_study/$folder/$TASK/valid.json"
  if [[ ! -s "$train_file" || ! -s "$test_file" ]]; then
    echo "Missing derived data for $variant. Run:" >&2
    echo "  python pipeline/ablation_study.py --task $TASK --variant $variant" >&2
    exit 1
  fi

  runner="experiments/ablation/GLT-MGA_ablation.py"
  [[ "$variant" == "local" ]] && runner="GLT-MGA_test_ablation.py"
  out="$ROOT/runs/rq2/$TASK/$folder/seed${SEED}_R${R}_S${S}_H4_${RUN_ID}"
  mkdir -p "$out"

  config=$(printf '{"train_file":"%s","valid_file":"%s","test_file":"%s","num_epochs":500,"patience":50,"learning_rate":0.001,"hidden_size":256,"propagation_rounds":%s,"propagation_substeps":%s,"readout_num_heads":4,"readout_attn_hidden":128,"class_weight_negative":1.0,"class_weight_positive":1.0,"internal_validation_ratio":0.10,"internal_split_seed":9930}' "$train_file" "$test_file" "$test_file" "$R" "$S")

  echo "Running $TASK / $variant with R=$R S=$S H=4"
  set -o pipefail
  "$PYTHON" -u "$runner" \
    --task "$TASK" \
    --config "$config" \
    --random_seed "$SEED" \
    --thresholds 0.45 \
    --log_dir "$out" \
    2>&1 | tee "$out/run.log"
  status=${PIPESTATUS[0]}
  [[ "$status" -eq 0 ]] || exit "$status"
done
