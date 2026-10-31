#!/usr/bin/env bash
# Run every approach on every dataset, then verify that they agree.
# Usage: ./run_all.sh <dir containing the three CSVs> [results_dir]
set -u
DATA_DIR="${1:?usage: ./run_all.sh <data_dir> [results_dir]}"
OUT="${2:-results}"
mkdir -p "$OUT"
for csv in "$DATA_DIR"/*.csv; do
  for engine in pure_python pandas polars; do
    name="$(basename "$csv" .csv)"
    echo ">>> $engine on $name"
    python3 "${engine}_stats.py" "$csv" --out-dir "$OUT" > "$OUT/${name}__${engine}.txt" 2>&1 \
      && echo "    ok" || echo "    FAILED (see $OUT/${name}__${engine}.txt)"
  done
done
python3 verify_agreement.py "$OUT" | tee "$OUT/verification.txt"
