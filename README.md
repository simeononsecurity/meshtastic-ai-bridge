# Local Meshtastic AI Bridge

Run an AI assistant on a Raspberry Pi 4B and make it reachable over a
[Meshtastic](https://meshtastic.org/) mesh via an SPI LoRa radio. Anyone on the
mesh sends a direct message to the Pi's node; the Pi runs
[`meshtasticd`](https://meshtastic.org/docs/meshtasticd/) and this bridge replies
with an answer from any OpenAI-compatible AI backend.

```text
Mesh node (phone/app) --LoRa--> meshtasticd (Pi, SPI radio) --TCP:4403--> bridge.py --> AI API
                                                                           <-- reply --
```

## What You Need

- Raspberry Pi 4B, Raspberry Pi OS (Bookworm/Trixie, 32-bit or 64-bit), SD card.
- A **RAK13300** (Semtech SX1262) LoRa module, typically mounted on the **RAK6421**
  WisBlock base board for Raspberry Pi. The installer ships official presets for
  Slot 1 (`spidev0.0`) and Slot 2 (`spidev0.1`).
- An AI model: by default a small local model served by [Ollama](https://ollama.com/)
  on the Pi, but any OpenAI-compatible endpoint works (OpenAI, LM Studio, vLLM,
  Groq, OpenRouter).

## One-Line Install

```bash
sudo ./setup.sh
```

That is the whole install. It enables SPI, installs meshtasticd, copies the
matching LoRa radio preset, installs the bridge, and starts both systemd
services. It pauses only to ask which radio preset matches your HAT.

The installer defaults to the RAK13300 in Slot 1. For Slot 2:

```bash
sudo ./setup.sh --lora-slot 2
```

## Then

1. Install Ollama and pull the default local model:

   ```bash
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull qwen3.5:0.8b
   ```

2. Edit the AI settings if needed (the defaults already point at Ollama):

   ```bash
   sudo nano /opt/meshtastic-ai-bridge/.env
   ```

3. Reboot so SPI is enabled, then check the radio:

   ```bash
   sudo reboot
   /opt/meshtastic-ai-bridge/venv/bin/meshtastic --host localhost --info
   ```

   (Or install the CLI separately with `pip install meshtastic`.)

4. Send a **direct message** to the Pi's node from a Meshtastic phone or app.
   The bridge replies with the AI's answer. Broadcast channel messages are
   ignored unless `REPLY_TO_BROADCAST=true` is set in `.env`.

## Using the assistant

Direct messages can contain a normal question. On a channel, prefix a request
with `!bot` (or the configured `BOT_PREFIX`) so ordinary conversation is not
answered. Useful examples include:

```text
!bot help
!bot wiki water purification
!bot wiki heat exhaustion
!bot wiki food preservation
!bot wiki solar power
!bot weather <city or postal code>
!bot news <topic>
```

The offline knowledge bundles are reference material, not a substitute for a
doctor, emergency service, electrician, or other qualified professional. Ask
for a source when accuracy matters, and treat medical or emergency answers as
general information rather than a diagnosis or guaranteed treatment.

The bridge exposes retrieval routes to the model through labeled `Retrieved
context`, not native model function calling. `wiki <subject>` searches the
local Kiwix corpus first, then the local SQLite index, then public Wikipedia if
enabled. `weather <location>` uses wttr.in, and `news <topic>` uses Google News
RSS when public retrieval is enabled. Retrieved material is treated as
untrusted reference text and is never treated as instructions.

### Preliminary model benchmark

On a 2 GB Raspberry Pi 4 with swap enabled, a one-sentence test using
`qwen2.5:0.5b` completed in about 16.6 seconds and produced 28 output tokens.
An initial multi-prompt comparison saturated memory and swap; the
`qwen3.5:0.8b` test did not complete within the short diagnostic window. Use
one model request at a time and keep `AI_MAX_TOKENS` modest for mesh use. The
smaller model is currently the safer responsiveness choice; benchmark again
after changing hardware, prompt length, or model settings.

During setup, the installer offers an offline knowledge-bundle menu. The
selection is stored in `.env` and downloads resumable Kiwix ZIM files rather
than expanding them into a duplicate database:

| Bundle | Contents | Approximate download |
|--------|----------|----------------------:|
| `medical` | WikiMed, WikEM, NHS Medicines, CDC Travelers' Health | 0.7 GB |
| `food` | Food preparation, public-domain recipes, and FOSS Cooking | 0.2 GB |
| `networking` | Computer and network-engineering references | 0.6 GB |
| `reference` | Simple English Wikipedia compact reference corpus | 0.5 GB |

Configure the selection before running setup:

```dotenv
KIWIX_BUNDLES=medical,food,networking
KIWIX_INTERACTIVE=false
KIWIX_MAX_DOWNLOAD_GB=4
```

Use `KIWIX_BUNDLES=prompt` to choose interactively, `none` to skip downloads,
or `all` for all supported compact bundles. The installer refuses selections
above `KIWIX_MAX_DOWNLOAD_GB`; downloads resume safely if setup is interrupted.

### Optional local Wikipedia RAG

Wiki requests prefer a local SQLite FTS5 index when one is installed. This
keeps the lookup private and avoids running an embedding model or vector
database on the Pi. The index is intentionally generated data and is ignored
by Git; provide a curated or licensed JSONL export rather than committing a
Wikipedia dump to the repository.

Each JSONL line must contain `title` and either `text` or `extract`, with an
optional `url`:

```json
{"title":"Raspberry Pi","text":"A family of single-board computers.","url":"https://en.wikipedia.org/wiki/Raspberry_Pi"}
```

Build and install the index on the node:

```bash
sudo install -d -o meshtasticbridge -g meshtasticbridge /opt/meshtastic-ai-bridge/data
sudo /opt/meshtastic-ai-bridge/venv/bin/python \
  /opt/meshtastic-ai-bridge/scripts/build_local_wiki_index.py \
  /path/to/wiki.jsonl /opt/meshtastic-ai-bridge/data/wiki.sqlite3
sudo chown meshtasticbridge:meshtasticbridge /opt/meshtastic-ai-bridge/data/wiki.sqlite3
sudo systemctl restart meshtastic-ai-bridge
```

Set `LOCAL_WIKI_ENABLED=true` and `LOCAL_WIKI_INDEX` in `.env`. A request such
as `!bot wiki Raspberry Pi` searches the local index first. If there is no local
match and `WEB_RETRIEVAL_ENABLED=true`, the bridge falls back to Wikipedia's
public API. Set `WEB_RETRIEVAL_ENABLED=false` for a fully local wiki-only
deployment.

## How It Works

| Part | Role |
|------|------|
| **meshtasticd** | Native Meshtastic daemon owning the SPI radio. Listens for clients on TCP port 4403. |
| **bridge.py** | Connects to meshtasticd over TCP, watches for `TEXT_MESSAGE_APP`, calls the AI API, and posts the reply back. |
| **systemd** | Keeps both `meshtasticd.service` and `meshtastic-ai-bridge.service` running across reboots. |

The bridge uses the Meshtastic Python library. Three connection modes are
supported:

| Mode | `.env` | Connects to |
|------|--------|-------------|
| Local daemon (default) | `MESHTASTIC_CONNECTION=tcp`, `MESHTASTIC_HOST=localhost` | meshtasticd on this Pi (TCP 4403) |
| **Remote TCP node** | `MESHTASTIC_CONNECTION=tcp`, `MESHTASTIC_HOST=<node-ip>` | a Meshtastic device exposing its API on TCP 4403 (WiFi node or remote meshtasticd) |
| USB serial node | `MESHTASTIC_CONNECTION=serial`, `MESHTASTIC_SERIAL_PORT=/dev/ttyACM0` | a standalone node over USB CDC (RAK4631 / RAK4630 / RAK19713) |

Long replies are split into Meshtastic-sized chunks automatically.

### Radio module (RAK13300 / SX1262)

The installer enables the **RAK13300** by default and copies the matching
official preset into `/etc/meshtasticd/config.d/`. Slot 1 and Slot 2 use
different chip-selects and pins:

| | Slot 1 | Slot 2 |
|---|---|---|
| SPI device | `spidev0.0` (CE0) | `spidev0.1` (CE1) |
| IRQ | GPIO 22 | GPIO 18 |
| Reset | GPIO 16 | GPIO 24 |
| Busy | GPIO 24 | GPIO 19 |
| Enable pins | 12, 13 | 26, 23 |

Choose the slot with `--lora-slot`:

```bash
sudo ./setup.sh --lora-slot 2
```

Slot 2 needs both chip-selects present. If `/dev/spidev0.1` does not exist,
add `dtoverlay=spi0-2cs` to `/boot/firmware/config.txt` and reboot.

For any other SPI radio, pass a preset name and the installer copies it from
meshtasticd's own `/etc/meshtasticd/available.d/` instead:

```bash
sudo ./setup.sh --preset lora-Adafruit-RFM9x
```

Additional presets ship in `meshtasticd/config.d/` (see its README): **RAK13302**
for the 13302 module and the **MeshStick** USB radio. The **MeshTadpole** USB
radio needs no preset, since its onboard EEPROM auto-configures meshtasticd 2.6.5+.

## Configuration

All bridge settings are environment variables in `/opt/meshtastic-ai-bridge/.env`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `MESHTASTIC_HOST` | `localhost` | meshtasticd host |
| `MESHTASTIC_PORT` | `4403` | meshtasticd TCP API port |
| `MESHTASTIC_CONNECTION` | `tcp` | `tcp` = meshtasticd, `serial` = standalone USB node |
| `MESHTASTIC_SERIAL_PORT` | `/dev/ttyACM0` | USB CDC port when `MESHTASTIC_CONNECTION=serial` |
| `AI_API_BASE` | `http://127.0.0.1:11434/v1` | OpenAI-compatible base URL |
| `AI_API_KEY` | `ollama` | API key (any string for a local server) |
| `AI_MODEL` | `qwen3.5:0.8b` | Model name |
| `AI_SYSTEM_PROMPT` | (see `.env.example`) | System prompt |
| `AI_MAX_TOKENS` | `300` | Max reply length |
| `AI_TEMPERATURE` | `0.7` | Sampling temperature |
| `REPLY_TO_BROADCAST` | `false` | Also answer channel broadcasts |
| `REPLY_MAX_CHARS` | `180` | Chunk size used to split replies |
| `WEB_RETRIEVAL_ENABLED` | `true` | Allow public weather/news/Wikipedia retrieval |
| `LOCAL_WIKI_ENABLED` | `true` | Search the local SQLite Wikipedia index first |
| `LOCAL_WIKI_INDEX` | `/opt/meshtastic-ai-bridge/data/wiki.sqlite3` | Local FTS5 index path |
| `LOCAL_KIWIX_ENABLED` | `true` | Search the local full-text Kiwix corpus first |
| `LOCAL_KIWIX_URL` | `http://127.0.0.1:8766` | Loopback Kiwix search service |
| `KIWIX_BUNDLES` | `prompt` | Offline bundle selection |
| `KIWIX_MAX_DOWNLOAD_GB` | `4` | Maximum estimated bundle download |

### Local model (default)

The bridge defaults to a small model served by [Ollama](https://ollama.com/)
on the same Pi. Install it and pull the model:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3.5:0.8b
```

Pick a model sized for your Pi 4B RAM:

| Model | ~Size | Pi 4 RAM | Use |
|-------|-------|----------|-----|
| `qwen3.5:0.8b` | ~0.5-0.8 GB | 2 GB+ | **Default** (best starting point) |
| `lfm2.5:1.2b-instruct` | ~0.7 GB | 2-4 GB+ | Efficiency experiment |
| `llama3.2:1b-instruct-q4_K_M` | ~0.7 GB | 2-4 GB+ | Comparison model |
| `qwen2.5:1.5b-instruct-q4_K_M` | ~1 GB | 4 GB+ | Safe mature choice |
| `qwen3:1.7b-instruct-q4_K_M` | ~1.4 GB | 4 GB+ | Higher-quality experiment |
| `qwen2.5:3b-instruct-q4_K_M` | ~1.8 GB | 8 GB | Upper-end experiment |

Tag names change over time, so confirm with `ollama search <name>` before pulling.

### MQTT (optional, off by default)

meshtasticd can bridge the mesh to an MQTT broker over the Pi's internet
connection. It is disabled by default. To enable it, set in `.env`:

```dotenv
MQTT_ENABLED=true
MQTT_ADDRESS=your-broker.example
```

Optionally set `MQTT_USERNAME` / `MQTT_PASSWORD`, or flip `MQTT_TLS_ENABLED`,
`MQTT_ENCRYPTION_ENABLED`, and `MQTT_JSON_ENABLED`. The installer applies these
to meshtasticd and enables channel uplink/downlink so packets flow to and from
the broker.

### Radio & channel settings

Beyond the radio drop-in, the mesh layer is configured from `.env` and applied
by `scripts/configure_mesh.sh` (run by `setup.sh` and by the dashboard).

| Variable | Default | Effect |
|----------|---------|--------|
| `LORA_RADIO_PRESET` | `RAK13300-slot1` | Which `meshtasticd/config.d/lora-<name>.yaml` enables the radio |
| `MESHTASTIC_SHORT_NAME` | `BOT` | Four-character node short name |
| `MESHTASTIC_LONG_NAME` | `Mesh Assistant` | Node long name |
| `MESHTASTIC_REGION` | `UNSET` | LoRa region (`US`, `EU_868`, ...) |
| `LORA_MODEM_PRESET` | `LONG_FAST` | Global modem preset (`LONG_FAST`, `SHORT_FAST`, ...) |
| `CHANNEL_0_NAME` | `LongFast` | Primary channel name |
| `CHANNEL_0_PSK` | | base64 key; empty keeps the default |
| `CHANNEL_0_UPLINK` / `CHANNEL_0_DOWNLINK` | `false` | Bridge channel 0 to/from MQTT |
| `CHANNELS_EXTRA` | | Extra channels as `name:psk[:role]` separated by `;` |

Example with a short-range preset plus two custom channels:

```dotenv
LORA_MODEM_PRESET=SHORT_FAST
CHANNELS_EXTRA=Work:<base64-key>:SECONDARY;Family:<base64-key>:SECONDARY
```

## Remote TCP Node (WiFi device or remote meshtasticd)

The bridge can also talk to a Meshtastic node over the network instead of USB
or a local daemon. Any node that exposes the Meshtastic **TCP API on port
4403** works: a meshtasticd daemon on another machine, or a WiFi-connected
device (for example an ESP32 joined to your network with WiFi client mode and
its API/TCP server enabled).

1. Confirm the node's TCP API is reachable from the Pi:

   ```bash
   meshtastic --host <node-ip> --info
   ```

2. Point the bridge at it in `.env`:

   ```dotenv
   MESHTASTIC_CONNECTION=tcp
   MESHTASTIC_HOST=<node-ip>
   MESHTASTIC_PORT=4403
   ```

3. Restart the bridge:

   ```bash
   sudo systemctl restart meshtastic-ai-bridge
   ```

Notes:

- `MESHTASTIC_CONNECTION=tcp` with `MESHTASTIC_HOST=localhost` is the default
  local-daemon setup; only `MESHTASTIC_HOST` changes for a remote node.
- The device must have WiFi (client mode) and its API/TCP server enabled first;
  see the Meshtastic [configuration docs](https://meshtastic.org/docs/configuration/).
- Only the primary node's traffic reaches the bridge, so point it at whichever
  node acts as your mesh gateway. Multiple bridges can share one node, but each
  bridge should use a distinct local identity if they answer on the same mesh.

## Standalone Serial Node (RAK4630 / RAK19713)

If you do not have a raw SPI LoRa HAT, use a Meshtastic device that carries its
own radio and MCU instead: a **RAK4631 WisBlock Core**, a bare **RAK4630**, or
a **RAK19713** (a RAK4630 on a mini-PCIe card). These run Meshtastic themselves,
so the Pi does not run meshtasticd at all; it talks to the node over USB CDC.

```text
Mesh node (phone/app) --LoRa--> RAK4630 node (Meshtastic) --USB CDC--> bridge.py --> AI API
                                                                       <-- reply --
```

1. Flash Meshtastic onto the node at <https://flasher.meshtastic.org/> and
   select **RAK4631** (WisBlock). The node then enumerates on the Pi as
   `/dev/ttyACM0`.
2. Point the bridge at it in `.env`:

   ```dotenv
   MESHTASTIC_CONNECTION=serial
   MESHTASTIC_SERIAL_PORT=/dev/ttyACM0
   ```

3. Configure the node (region, channels, MQTT, admin key) the same way, but use
   `--port /dev/ttyACM0` instead of `--host localhost`:

   ```bash
   /opt/meshtastic-ai-bridge/venv/bin/meshtastic --port /dev/ttyACM0 --set region US
   /opt/meshtastic-ai-bridge/venv/bin/meshtastic --port /dev/ttyACM0 --info
   ```

Hardware notes:

- A **RAK4631 core** has its own Micro-USB port; plug it straight into the Pi.
- A bare **RAK4630 module** needs a WisBlock base board (RAK5005-O or RAK19007)
  to expose USB and the antenna connector.
- The **RAK19713** is a RAK4630 on a mini-PCIe card, and its host link is USB or
  UART. Mount it on a mini-PCIe-to-USB adapter. Do not leave it in a LoRaWAN
  concentrator socket (SenseCAP M1 / Seeed WM1302 HAT), which wires SPI for an
  SX1302 and does not route USB, so the node is unreachable there.

The RAK19713 uses standard mini-PCIe pins for its host link:

| Mini-PCIe pin | RAK19713 signal |
|---------------|-----------------|
| 36 | USB D− |
| 38 | USB D+ |
| 31 | UART2 RX |
| 33 | UART2 TX |
| 22 | `PERST#` reset |
| 2 / 24 / 39 / 41 / 52 | 3.3 V (power) |
| 4, 9, 15, 18, 21, 26, 27, 29, 34, 35, 37, 40, 43, 50 | GND |

A mini-PCIe-to-USB adapter routes pins 36/38 (plus power and ground), which is
all the node needs to flash and to enumerate as `/dev/ttyACM0`.

To hand-wire it instead (no adapter), connect a cut USB 2.0 cable:

| USB cable wire | Colour | mPCIe pin |
|----------------|--------|-----------|
| D− | white | **36** |
| D+ | green | **38** |
| GND | black | any GND pin (4, 9, 15, 18, 21, 26, 27, 29, 34, 35, 37, 40, 43, 50) |

Pins 36 and 38 are both on the even-numbered (solder-side) row of the edge
connector. Power already comes from the socket's 3.3 V pins, so no cable power
lead is needed. Confirm white/green with a continuity meter (some cables swap
them), and attach an antenna to the IPEX connector **before the first
transmit**. Running the SX1262 without an antenna can damage its PA. If the Pi
still does not show the node in `lsusb`, the nRF52840 may be waiting for 5 V VBUS
sense; use an adapter (which handles VBUS) rather than wiring 5 V to a GPIO.

This offloads all radio processing to the node's own MCU, which is lighter than
meshtasticd for a 2 GB Pi.

## Dashboard

A local web dashboard runs on port **8080** (via `http://<pi-ip>:8080`) and lets you:

- **start / stop / restart** the `meshtasticd`, `meshtastic-ai-bridge`, and `ollama` services
- **monitor bot responses** (the bridge logs every AI question/answer)
- **switch the active model** live, or **pull** a new Ollama model
- **view the mesh**: node info, nodes, and channels
- **re-apply `.env`** to the node without re-running `setup.sh`

It runs as root (so it can control systemd) and is unauthenticated, so keep it
on a trusted LAN. `DASHBOARD_HOST` / `DASHBOARD_PORT` in `.env` change the bind.

## Managing Services

```bash
sudo systemctl status meshtasticd
sudo systemctl status meshtastic-ai-bridge
journalctl -u meshtastic-ai-bridge -f      # follow bridge logs
sudo systemctl restart meshtastic-ai-bridge
```

## Troubleshooting

| Symptom | Check |
|---------|-------|
| No radio | SPI not enabled: reboot, then `ls /dev/spidev*`. For Slot 2, add `dtoverlay=spi0-2cs` if `spidev0.1` is missing |
| `meshtastic --host localhost` fails | meshtasticd not running: `systemctl status meshtasticd` |
| No AI reply | Ollama running, model pulled, `.env` correct; `journalctl -u meshtastic-ai-bridge` |
| Replies only to DMs | Expected. Set `REPLY_TO_BROADCAST=true` for channel replies |
| Short replies | `AI_MAX_TOKENS` too low, or replies split by `REPLY_MAX_CHARS` |

## Files

```text
setup.sh                           one-line installer
bridge/bridge.py                   Meshtastic <-> AI daemon (logs to responses.jsonl, reads live_config.json)
bridge/requirements.txt            Python deps
bridge/.env.example                configuration template
scripts/build_local_wiki_index.py  builds the optional local Wikipedia FTS5 index
scripts/configure_mesh.sh          applies .env -> meshtastic (region, channels, MQTT, admin key)
scripts/install_offline_knowledge.sh downloads selected Kiwix offline bundles
scripts/start_kiwix.sh             starts the local Kiwix HTTP service
dashboard/                         local Flask web dashboard (port 8080)
meshtasticd/config.d/              radio presets (RAK13300 / RAK13302 / MeshStick) + README
meshtasticd/config.yaml.example    optional Web server settings
systemd/meshtastic-ai-bridge.service
systemd/meshtastic-kiwix.service
systemd/meshtastic-dashboard.service
```

## References

- [meshtasticd docs](https://meshtastic.org/docs/meshtasticd/)
- [meshtasticd installation](https://meshtastic.org/docs/meshtasticd/installation/)
- [meshtasticd usage](https://meshtastic.org/docs/meshtasticd/usage/)
- [meshtasticd hardware](https://meshtastic.org/docs/meshtasticd/hardware/)
- [Meshtastic Python library](https://github.com/meshtastic/python)
