#!/usr/bin/env bash
# Apply all Meshtastic node config (LoRa radio, region, modem preset, channels,
# admin key, MQTT) from a .env file using the meshtastic CLI.
#
#   sudo bash scripts/configure_mesh.sh [env-file]
#
# Requires meshtasticd's TCP API (localhost:4403) to be reachable, which happens
# once the LoRa radio has enumerated. Safe to re-run; the LoRa region, modem
# preset, channels, admin key, and MQTT are each set idempotently.
set -euo pipefail

ENV_FILE="${1:-/opt/meshtastic-ai-bridge/.env}"
M="${MESHTASTIC_BIN:-/opt/meshtastic-ai-bridge/venv/bin/meshtastic}"
HOST="${MESHTASTIC_HOST:-localhost}"
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

get() { sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | head -1; }
run() { "$M" --host "$HOST" "$@"; }

apply() {
  # apply "<label>" <args...>
  local label="$1"; shift
  if run "$@" >/dev/null 2>&1; then
    echo "    ok: $label"
  else
    echo "    FAILED: $label  (retry: $M --host $HOST $*)"
  fi
}

echo "==> Applying Meshtastic config from $ENV_FILE"

# --- LoRa radio drop-in (which config.d preset enables the radio) ---
RADIO="$(get LORA_RADIO_PRESET)"
if [[ -n "$RADIO" ]]; then
  CONF_DIR="/etc/meshtasticd/config.d"
  mkdir -p "$CONF_DIR"
  SRC="$INSTALL_DIR/meshtasticd/config.d/lora-$RADIO.yaml"
  [[ -f "$SRC" ]] || SRC="/etc/meshtasticd/available.d/lora-$RADIO.yaml"
  if [[ -f "$SRC" ]]; then
    # Keep exactly one radio active: clear old lora-*.yaml, install the chosen one.
    find "$CONF_DIR" -maxdepth 1 -name 'lora-*.yaml' -delete 2>/dev/null || true
    cp "$SRC" "$CONF_DIR/"
    echo "    ok: radio preset -> lora-$RADIO.yaml (restart meshtasticd to take effect)"
  else
    echo "    WARN: radio preset 'lora-$RADIO.yaml' not found in $INSTALL_DIR/meshtasticd/config.d or /etc/meshtasticd/available.d"
  fi
fi

# --- LoRa region + global modem preset (shared by every channel) ---
REGION="$(get MESHTASTIC_REGION)"
[[ -n "$REGION" ]] && apply "lora.region=$REGION" --set lora.region "$REGION"
PRESET="$(get LORA_MODEM_PRESET)"
[[ -n "$PRESET" ]] && apply "lora.modem_preset=$PRESET" --set lora.modem_preset "$PRESET"

# --- Admin key (remote management from another node) ---
ADMIN="$(get MESHTASTIC_ADMIN_KEY)"
[[ -n "$ADMIN" ]] && apply "security.admin_key" --set security.admin_key "$ADMIN"

# --- Primary channel (index 0) ---
CH0_NAME="$(get CHANNEL_0_NAME)"
[[ -n "$CH0_NAME" ]] && apply "channel0.name=$CH0_NAME" --ch-index 0 --ch-set name "$CH0_NAME"
CH0_PSK="$(get CHANNEL_0_PSK)"
[[ -n "$CH0_PSK" ]] && apply "channel0.psk" --ch-index 0 --ch-set psk "$CH0_PSK"
CH0_UP="$(get CHANNEL_0_UPLINK)"
[[ -n "$CH0_UP" ]] && apply "channel0.uplink=$CH0_UP" --ch-index 0 --ch-set uplink_enabled "$CH0_UP"
CH0_DOWN="$(get CHANNEL_0_DOWNLINK)"
[[ -n "$CH0_DOWN" ]] && apply "channel0.downlink=$CH0_DOWN" --ch-index 0 --ch-set downlink_enabled "$CH0_DOWN"

# --- Extra channels: "name:psk[:role]" entries separated by ';' ---
EXTRA="$(get CHANNELS_EXTRA)"
if [[ -n "$EXTRA" ]]; then
  IDX=1
  IFS=';' read -ra ENTRIES <<< "$EXTRA"
  for entry in "${ENTRIES[@]}"; do
    [[ -z "$entry" ]] && continue
    IFS=':' read -ra P <<< "$entry"
    NAME="${P[0]:-}"; PSK="${P[1]:-}"; ROLE="${P[2]:-SECONDARY}"
    if [[ -n "$NAME" ]]; then
      # Only add if not already present (keeps re-runs idempotent).
      if "$M" --host "$HOST" --info 2>/dev/null | grep -qiF "name: $NAME\|$NAME" || \
         "$M" --host "$HOST" --info 2>/dev/null | grep -qiF "$NAME"; then
        echo "    skip: channel '$NAME' already present"
      else
        run --ch-add "$NAME" >/dev/null 2>&1 && echo "    ok: added channel '$NAME'" || echo "    FAILED: add channel '$NAME'"
      fi
      [[ -n "$PSK" ]] && apply "channel$IDX.psk" --ch-index "$IDX" --ch-set psk "$PSK"
      [[ -n "$ROLE" ]] && apply "channel$IDX.role=$ROLE" --ch-index "$IDX" --ch-set role "$ROLE"
    fi
    IDX=$((IDX + 1))
  done
fi

# --- MQTT ---
MQTT_ENABLED="$(get MQTT_ENABLED)"
if [[ "$MQTT_ENABLED" == "true" || "$MQTT_ENABLED" == "1" || "$MQTT_ENABLED" == "yes" ]]; then
  apply "mqtt.enabled" --set mqtt.enabled true
  for pair in \
    MQTT_ADDRESS:address MQTT_USERNAME:username MQTT_PASSWORD:password \
    MQTT_ROOT:root MQTT_TLS_ENABLED:tls_enabled \
    MQTT_ENCRYPTION_ENABLED:encryption_enabled MQTT_JSON_ENABLED:json_enabled; do
    VAR="${pair%%:*}"; FIELD="${pair##*:}"
    VAL="$(get "$VAR")"
    [[ -n "$VAL" ]] && apply "mqtt.$FIELD=$VAL" --set "mqtt.$FIELD" "$VAL"
  done
else
  echo "    mqtt disabled (set MQTT_ENABLED=true to enable)"
fi

echo "==> Done. Restart meshtasticd if you changed the radio preset or region."
