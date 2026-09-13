#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/meshtastic-ai-bridge}"
ENV_FILE="${1:-$INSTALL_DIR/.env}"
get_env() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | head -1; }
DATA_DIR="$(get_env KIWIX_DATA_DIR)"; [[ -n "$DATA_DIR" ]] || DATA_DIR="${KIWIX_DATA_DIR:-$INSTALL_DIR/zim}"
BUNDLES="$(get_env KIWIX_BUNDLES)"; [[ -n "$BUNDLES" ]] || BUNDLES=prompt
MAX_GB="$(get_env KIWIX_MAX_DOWNLOAD_GB)"; [[ -n "$MAX_GB" ]] || MAX_GB=4
INTERACTIVE="$(get_env KIWIX_INTERACTIVE)"; [[ -n "$INTERACTIVE" ]] || INTERACTIVE=true

declare -A BUNDLE_LABEL BUNDLE_URLS
BUNDLE_LABEL[medical]="Medical and emergency care (~0.7 GB)"
BUNDLE_URLS[medical]=$'https://download.kiwix.org/zim/wikipedia/wikipedia_en_medicine_mini_2026-04.zim\nhttps://download.kiwix.org/zim/other/wikem_en_all_nopic_2026-07.zim\nhttps://download.kiwix.org/zim/zimit/nhs.uk_en_medicines_2025-12.zim\nhttps://download.kiwix.org/zim/zimit/wwwnc.cdc.gov_en_all_2024-11.zim'
BUNDLE_LABEL[food]="Food, cooking, and preservation (~0.2 GB)"
BUNDLE_URLS[food]=$'https://download.kiwix.org/zim/other/zimgit-food-preparation_en_2025-04.zim\nhttps://download.kiwix.org/zim/zimit/publicdomainrecipes.com_en_all_2026-08.zim\nhttps://download.kiwix.org/zim/zimit/foss.cooking_en_all_2026-05.zim'
BUNDLE_LABEL[networking]="Computers and network engineering (~0.6 GB)"
BUNDLE_URLS[networking]=$'https://download.kiwix.org/zim/wikipedia/wikipedia_en_computer_nopic_2026-06.zim\nhttps://download.kiwix.org/zim/stack_exchange/networkengineering.stackexchange.com_en_all_2026-08.zim'
BUNDLE_LABEL[reference]="Broad Simple English reference (~0.5 GB)"
BUNDLE_URLS[reference]="https://download.kiwix.org/zim/wikipedia/wikipedia_en-simple_all_mini_2026-06.zim"

if [[ "$BUNDLES" == prompt && "$INTERACTIVE" == true && -t 0 ]]; then
  echo "Choose bundles: medical, food, networking, reference, or all"
  read -r -p "Bundles [medical,food,networking,reference]: " BUNDLES
  [[ -n "$BUNDLES" ]] || BUNDLES=medical,food,networking,reference
elif [[ "$BUNDLES" == prompt ]]; then
  echo "KIWIX_BUNDLES=prompt requires a terminal; skipping downloads."
  exit 0
fi
[[ "$BUNDLES" == none || -z "$BUNDLES" ]] && exit 0
[[ "$BUNDLES" == all ]] && BUNDLES=medical,food,networking,reference

python3 - "$BUNDLES" "$MAX_GB" <<'PY'
import sys
names = [x.strip() for x in sys.argv[1].split(',')]
sizes = {'medical': .7, 'food': .2, 'networking': .6, 'reference': .5}
unknown = [x for x in names if x not in sizes]
if unknown:
    raise SystemExit(f"Unknown KIWIX bundle(s): {', '.join(unknown)}")
total = sum(sizes[x] for x in names)
if total > float(sys.argv[2]):
    raise SystemExit(f"Selected bundles need about {total:.1f} GB, above KIWIX_MAX_DOWNLOAD_GB={sys.argv[2]}")
print(f"Selected offline bundles: {', '.join(names)} (~{total:.1f} GB)")
PY

command -v aria2c >/dev/null 2>&1 || { echo "aria2c is required; install kiwix-tools first" >&2; exit 1; }
mkdir -p "$DATA_DIR"
OWNER="$(get_env SERVICE_USER)"; [[ -n "$OWNER" ]] || OWNER=meshtasticbridge
chown "$OWNER":"$OWNER" "$DATA_DIR" 2>/dev/null || true

download_one() {
  local url="$1"
  [[ -z "$url" ]] && return
  if id "$OWNER" >/dev/null 2>&1; then
    runuser -u "$OWNER" -- aria2c --allow-overwrite=false --auto-file-renaming=false \
      --max-connection-per-server=4 --split=4 --continue=true --dir="$DATA_DIR" "$url"
  else
    aria2c --allow-overwrite=false --auto-file-renaming=false --max-connection-per-server=4 \
      --split=4 --continue=true --dir="$DATA_DIR" "$url"
  fi
}

IFS=',' read -ra SELECTED <<< "$BUNDLES"
for bundle in "${SELECTED[@]}"; do
  bundle="${bundle//[[:space:]]/}"
  echo "==> Installing Kiwix bundle: ${BUNDLE_LABEL[$bundle]}"
  while IFS= read -r url; do download_one "$url"; done <<< "${BUNDLE_URLS[$bundle]}"
done
chown -R "$OWNER":"$OWNER" "$DATA_DIR" 2>/dev/null || true
du -sh "$DATA_DIR"