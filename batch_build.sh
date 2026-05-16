#!/bin/bash
# batch_build.sh — rebuild PWA for each SKU in a file, then git commit & push
# Usage: bash batch_build.sh x

INPUT="${1:-x}"
SCRIPT="$(dirname "$0")/build_clock.py"

if [ ! -f "$INPUT" ]; then
  echo "Error: input file '$INPUT' not found"
  exit 1
fi

SUCCESS=()
SKIPPED=()
FAILED=()

while IFS= read -r sku || [ -n "$sku" ]; do
  # Skip blank lines and comments
  [[ -z "$sku" || "$sku" == \#* ]] && continue

  sku=$(echo "$sku" | tr '[:lower:]' '[:upper:]' | xargs)  # trim + uppercase
  json="${sku}-cal.json"

  if [ ! -f "$json" ]; then
    echo ""
    echo "⚠️  Skipping $sku — no ${json} found (run --calibrate first)"
    SKIPPED+=("$sku")
    continue
  fi

  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "▶  Building $sku"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  if python3 "$SCRIPT" "$sku"; then
    SUCCESS+=("$sku")
  else
    echo "❌ Build failed for $sku"
    FAILED+=("$sku")
  fi

done < "$INPUT"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "BUILD COMPLETE"
echo "  ✅ Built:   ${SUCCESS[*]:-none}"
echo "  ⚠️  Skipped: ${SKIPPED[*]:-none}"
echo "  ❌ Failed:  ${FAILED[*]:-none}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ${#SUCCESS[@]} -eq 0 ]; then
  echo "Nothing to commit."
  exit 0
fi

echo ""
echo "Running git commands..."
git add .
git commit -m "Batch rebuild: ${SUCCESS[*]}"
git push
echo "Done."
