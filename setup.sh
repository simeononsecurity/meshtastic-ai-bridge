#!/usr/bin/env bash
# meshtastic-ai-bridge: one-line install for Raspberry Pi (SPI LoRa radio + AI bridge).
#
#   sudo ./setup.sh
#
# Does everything: enables SPI, installs meshtasticd, wires up the LoRa radio
# preset, installs the Meshtastic <-> AI bridge, and starts both services.
#
# Options:
#   --lora-slot N      RAK13300 slot to enable: 1 (spidev0.0, default) or 2
#                      (spidev0.1). Ignored when --preset is given.
#   --preset NAME      Override with a specific preset from /etc/meshtasticd/available.d.
#   --channel IDX      Meshtastic channel to monitor (default 0, not currently used).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo $0"
  exit 1
fi

# --- Resolve the LoRa preset from CLI args ---
PRESET=""
LORA_SLOT="1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --preset)
      PRESET="${2:-}"; shift 2 ;;
    --lora-slot)
      LORA_SLOT="${2:-1}"; shift 2 ;;
    --channel)
      shift 2 ;;
    *)
      echo "Unknown argument: $1"; exit 1 ;;
  esac
done

source /etc/os-release

echo "==> Detected: $PRETTY_NAME"

# --- Map distribution to the OpenSUSE Build Service repo ---
CODENAME="${VERSION_CODENAME:-}"
case "$CODENAME" in
  bookworm) DIST="12" ;;
  trixie)   DIST="13" ;;
  *)
    echo "Unsupported release codename: ${CODENAME:-unknown}. Need bookworm (12) or trixie (13)."
    exit 1
    ;;
esac

if [[ "$NAME" == Raspbian* ]]; then
  # 32-bit Raspberry Pi OS
  REPO="Raspbian_${DIST}"
else
  # 64-bit Raspberry Pi OS and other Debian-based systems
  REPO="Debian_${DIST}"
fi

LIST="/etc/apt/sources.list.d/network:Meshtastic:beta.list"
KEY="/etc/apt/trusted.gpg.d/network_Meshtastic_beta.gpg"

echo "==> [1/6] Enabling SPI"
CONFIG_TXT=""
for c in /boot/firmware/config.txt /boot/config.txt; do
  [[ -f "$c" ]] && CONFIG_TXT="$c" && break
done
if [[ -z "$CONFIG_TXT" ]]; then
  echo "!  Could not locate config.txt. Enable SPI manually via raspi-config (Interfaces -> SPI) then re-run."
  exit 1
fi
if grep -q '^dtparam=spi=on' "$CONFIG_TXT"; then
  echo "    SPI already enabled in $CONFIG_TXT"
else
  echo "dtparam=spi=on" >> "$CONFIG_TXT"
  echo "    Added 'dtparam=spi=on' to $CONFIG_TXT (a reboot enables it)"
fi

echo "==> [2/6] Installing meshtasticd"
apt-get update -qq
apt-get install -y curl gnupg ca-certificates
if [[ -f "$LIST" ]]; then
  echo "    meshtasticd repo already configured"
else
  echo "deb http://download.opensuse.org/repositories/network:/Meshtastic:/beta/${REPO}/ /" \
    | tee "$LIST" > /dev/null
  curl -fsSL "https://download.opensuse.org/repositories/network:/Meshtastic:/beta/${REPO}/Release.key" \
    | gpg --dearmor | tee "$KEY" > /dev/null
fi
apt-get update -qq
apt-get install -y meshtasticd python3-venv python3-pip

echo "==> [3/6] Configuring the LoRa radio (RAK13300 / SX1262)"
MESHDIR="/etc/meshtasticd"
mkdir -p "$MESHDIR/config.d"

if [[ -n "$PRESET" ]]; then
  # Use one of the package's own shipped presets.
  SRC="$MESHDIR/available.d/$PRESET"
  if [[ ! -f "$SRC" ]]; then
    echo "!  Preset not found: $SRC"
    echo "   Available LoRa presets:"
    ls "$MESHDIR/available.d" 2>/dev/null | grep -i lora || true
    exit 1
  fi
else
  # Default to the bundled RAK13300 preset for the chosen slot.
  SRC="$SCRIPT_DIR/meshtasticd/config.d/lora-RAK13300-slot${LORA_SLOT}.yaml"
  if [[ ! -f "$SRC" ]]; then
    echo "!  Bundled preset missing: $SRC"
    exit 1
  fi
fi

cp "$SRC" "$MESHDIR/config.d/"
echo "    Copied $(basename "$SRC") -> $MESHDIR/config.d/"
if [[ -z "$PRESET" && "$LORA_SLOT" == "1" ]]; then
  echo "    If your RAK13300 is in Slot 2 instead, re-run: sudo $0 --lora-slot 2"
fi

echo "==> [4/6] Installing the AI bridge"
INSTALL_DIR="/opt/meshtastic-ai-bridge"
mkdir -p "$INSTALL_DIR"
cp -r "$SCRIPT_DIR/bridge/." "$INSTALL_DIR/"
if [[ ! -f "$INSTALL_DIR/.env" && -f "$INSTALL_DIR/.env.example" ]]; then
  cp "$INSTALL_DIR/.env.example" "$INSTALL_DIR/.env"
  echo "    Created $INSTALL_DIR/.env (edit it with your AI API details before use)"
fi
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

if ! id -u meshtasticbridge >/dev/null 2>&1; then
  useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin meshtasticbridge
fi
chown -R meshtasticbridge:meshtasticbridge "$INSTALL_DIR"

echo "==> [5/6] Installing systemd service"
cp "$SCRIPT_DIR/systemd/meshtastic-ai-bridge.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable meshtasticd
systemctl enable meshtastic-ai-bridge

echo "==> [6/6] Starting services"
systemctl restart meshtasticd
sleep 5

# Apply the admin key so the node can be managed from another Meshtastic node.
ADMIN_KEY="$(sed -n 's/^MESHTASTIC_ADMIN_KEY=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1)"
if [[ -n "$ADMIN_KEY" ]]; then
  if "$INSTALL_DIR/venv/bin/meshtastic" --host localhost --set security.admin_key "$ADMIN_KEY" >/dev/null 2>&1; then
    echo "    Applied Meshtastic admin key."
  else
    echo "!   Could not apply the admin key. After meshtasticd is up, run:"
    echo "    $INSTALL_DIR/venv/bin/meshtastic --host localhost --set security.admin_key \"<key>\""
  fi
fi

echo "==> [7/7] Configuring MQTT (off by default)"
MQTT_ENABLED="$(sed -n 's/^MQTT_ENABLED=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1)"
if [[ "$MQTT_ENABLED" == "true" || "$MQTT_ENABLED" == "1" || "$MQTT_ENABLED" == "yes" ]]; then
  MQTT_ARGS=(--set mqtt.enabled true)
  MQTT_ADDR="$(sed -n 's/^MQTT_ADDRESS=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1)"
  MQTT_USER="$(sed -n 's/^MQTT_USERNAME=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1)"
  MQTT_PASS="$(sed -n 's/^MQTT_PASSWORD=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1)"
  MQTT_ROOT="$(sed -n 's/^MQTT_ROOT=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1)"
  MQTT_TLS="$(sed -n 's/^MQTT_TLS_ENABLED=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1)"
  MQTT_ENC="$(sed -n 's/^MQTT_ENCRYPTION_ENABLED=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1)"
  MQTT_JSON="$(sed -n 's/^MQTT_JSON_ENABLED=//p' "$INSTALL_DIR/.env" 2>/dev/null | head -1)"

  [[ -n "$MQTT_ADDR" ]] && MQTT_ARGS+=(--set mqtt.address "$MQTT_ADDR")
  [[ -n "$MQTT_USER" ]] && MQTT_ARGS+=(--set mqtt.username "$MQTT_USER")
  [[ -n "$MQTT_PASS" ]] && MQTT_ARGS+=(--set mqtt.password "$MQTT_PASS")
  [[ -n "$MQTT_ROOT" ]] && MQTT_ARGS+=(--set mqtt.root "$MQTT_ROOT")
  [[ -n "$MQTT_TLS" ]] && MQTT_ARGS+=(--set mqtt.tls_enabled "$MQTT_TLS")
  [[ -n "$MQTT_ENC" ]] && MQTT_ARGS+=(--set mqtt.encryption_enabled "$MQTT_ENC")
  [[ -n "$MQTT_JSON" ]] && MQTT_ARGS+=(--set mqtt.json_enabled "$MQTT_JSON")

  if "$INSTALL_DIR/venv/bin/meshtastic" --host localhost "${MQTT_ARGS[@]}" >/dev/null 2>&1; then
    echo "    Applied MQTT (server: ${MQTT_ADDR:-default})."
  else
    echo "!   Could not apply MQTT. After meshtasticd is up, run:"
    echo "    $INSTALL_DIR/venv/bin/meshtastic --host localhost ${MQTT_ARGS[*]}"
  fi

  if "$INSTALL_DIR/venv/bin/meshtastic" --host localhost --ch-set uplink_enabled true --ch-set downlink_enabled true >/dev/null 2>&1; then
    echo "    Enabled channel uplink + downlink."
  else
    echo "!   Could not enable channel uplink/downlink. Manually: meshtastic --host localhost --ch-set uplink_enabled true --ch-set downlink_enabled true"
  fi
else
  echo "    MQTT disabled. Set MQTT_ENABLED=true and MQTT_ADDRESS in .env to enable."
fi

systemctl restart meshtastic-ai-bridge || true

cat <<EOF

Done. Next steps:
  1. Edit your AI settings:  sudo nano $INSTALL_DIR/.env
     (set AI_API_BASE, AI_API_KEY, and AI_MODEL for your backend)
  2. Reboot so SPI takes effect:  sudo reboot
  3. Verify the radio:  $INSTALL_DIR/venv/bin/meshtastic --host localhost --info
  4. Watch the bridge log:  journalctl -u meshtastic-ai-bridge -f

Send a direct message to this node over Meshtastic and the AI will reply.
EOF
