# Local Meshtastic AI Bridge

Run an AI assistant on a small ARM computer and make it reachable over a
[Meshtastic](https://meshtastic.org/) mesh via an SPI LoRa radio. Anyone on the
mesh sends a direct message to the Pi's node; the Pi runs
[`meshtasticd`](https://meshtastic.org/docs/meshtasticd/) and this bridge replies
with an answer from any OpenAI-compatible AI backend.

```text
Mesh node (phone/app) --LoRa--> meshtasticd (ARM host, SPI radio) --TCP:4403--> bridge.py --> AI API
                                                                           <-- reply --
```

## What You Need

- A supported ARM Linux computer. **2 GB RAM is the minimum; 4–8 GB is the
  recommended production range.** The Raspberry Pi 4B used during the proof of
  concept is not the preferred production platform for local AI plus offline
  knowledge workloads.
- A **RAK13300** (Semtech SX1262) LoRa module, typically mounted on the **RAK6421**
  WisBlock base board for Raspberry Pi. The installer ships official presets for
  Slot 1 (`spidev0.0`) and Slot 2 (`spidev0.1`).
- An AI model: by default a small local model served by [Ollama](https://ollama.com/)
  on the Pi, but any OpenAI-compatible endpoint works (OpenAI, LM Studio, vLLM,
  Groq, OpenRouter). See [MODELS.md](MODELS.md) for which model to run.

### Hardware recommendations: proof of concept versus production

The Pi 4B is useful for proving the radio, bridge, Ollama, and retrieval
architecture. It is not a comfortable production host for running an AI model,
Kiwix content, the dashboard, and system services at the same time. Expect
model loading delays, swap pressure, and less room for future features.

| Platform | Recommended RAM | Role in this project | Strengths | Tradeoffs |
|----------|----------------:|---------------------|-----------|-----------|
| Raspberry Pi 4B | 2 GB minimum; 4 GB preferred | Proof of concept or very small deployment | Mature ecosystem, low power, easy Meshtastic GPIO/SPI integration | Limited memory and CPU; 2 GB is not a good local-AI production target |
| Raspberry Pi 5 | 4 GB preferred; 8 GB ideal | Best-supported general production choice | Much faster CPU, strong community support, PCIe/USB storage options, straightforward Raspberry Pi OS support | Higher peak power and thermal requirements than Pi 4B; use active cooling for sustained inference |
| Orange Pi 5 / 5B | 8 GB preferred; 16 GB useful for larger local services | Strong performance-per-dollar alternative | RK3588S-class CPU, abundant RAM options, fast storage, useful onboard acceleration hardware | Smaller ecosystem; verify Linux, Ollama, GPIO/SPI, and supported Meshtastic radio drivers before standardizing |
| NVIDIA Jetson Nano | 4 GB | Legacy GPU/edge-AI experiment | CUDA/TensorRT ecosystem and low-power modes | Older platform with limited CPU/RAM; not recommended for a new production purchase or larger language models |
| Jetson Orin Nano / Orin Nano Super | 8 GB preferred | Higher-performance local-AI production node | Much stronger GPU/AI acceleration and better headroom for local models | Higher cost, power draw, cooling, and software complexity; size the solar system accordingly |
| Small x86 mini-PC | 8–16 GB preferred | Fixed-site or high-capacity deployment | Broadest model/runtime compatibility and easy SSD expansion | Usually higher idle power; less attractive for a small solar-powered field node |

**Recommended default:** Raspberry Pi 5 with 8 GB RAM, active cooling, and a
USB 3 or PCIe/NVMe SSD. Choose an Orange Pi 5 with 8–16 GB when performance and
RAM-per-dollar matter more than ecosystem simplicity. Choose Jetson Orin Nano
when GPU-accelerated inference is a primary requirement. Treat the original
Jetson Nano as an existing-hardware option, not the target for a new build.

For an off-grid or solar-capable installation, the computer is only part of
the system. Use a protected LiFePO4 battery, a properly sized solar charge
controller, adequate 5 V regulation, and a panel sized for the local winter
duty cycle. A panel alone cannot provide stable computer power through clouds
or nighttime. Keep the SX1262 radio and antenna physically separated from
noisy power converters and USB devices where possible.

Use 64-bit Linux, durable storage, active cooling for sustained inference, and
an external SSD when storing Kiwix bundles. Measure idle, receive, transmit,
and inference power at the completed installation; solar operation is a design
goal, not a guarantee from a board specification.

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
   ollama pull qwen2.5:0.5b
   ```

   That default is the only registry model measured as both fast and stable on
   the documented 2 GB minimum. On a 4-8 GB host, pick from the table in
   [MODELS.md](MODELS.md).

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
`!bot weather <city or postal code>`
!bot news <topic>
```

The built-in `help` and `status` commands are deterministic and do not use the
AI model. `status` reports compact mesh, model, queue, and broadcast state.

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
Weather context includes the requested location, conditions, temperature,
feels-like temperature, humidity, wind direction/speed, and source. The answer
may be split into multiple radio chunks when necessary.

### Retrieval adapters

Retrieval is implemented through small adapters rather than one large retrieval
function:

- **Weather adapter** — `weather <city or ZIP>` using wttr.in when public retrieval is enabled.
- **News adapter** — `news <topic>` using Google News RSS when public retrieval is enabled.
- **Medical/reference adapter** — `medical <topic>`, `health <topic>`, or `reference <topic>` using the local Kiwix/SQLite corpus.
- **Sensor adapter** — optional local JSON endpoint configured with `SENSOR_URL`; disabled when blank.

Adapters return labeled reference context and do not execute model instructions.
Leave `SENSOR_URL` blank unless the endpoint is local, trusted, and protected by
the host network policy.

### Local AI model

The bridge answers with a small local model served by [Ollama](https://ollama.com/);
any OpenAI-compatible endpoint works instead. Pick on **instruction compliance
first and speed second**. Across the models measured, only two obey the reply
contract close to always (35 of 36 samples each, measured on two different hosts):

| Host RAM | Model | Comply |
|---------:|-------|-------:|
| 4 GB | `lfm2.5-1.2b` - needs the one-time import below; `gemma2:2b` if you would rather not import | ~97% |
| 8 GB | `lfm2.5-1.2b` (15.5 tok/s on a Pi 5) or the registry build `gemma3:1b` (11.2 tok/s) | ~97% / ~94% |
| 8 GB with a CUDA GPU | `gemma3:1b` - 87.5 tok/s | ~97% |
| 2 GB | `lfm2.5-230m` - the best model that fits, but expect roughly 1 reply in 5 to breach | ~78% |

The two leaders swap first place between hosts, so on compliance there is nothing
to choose between them; `gemma2:2b` (1.6 GB, 58/60 over 60 samples) matches them
and installs with a plain `ollama pull`, so it is the best choice if you want a
compliant model without the one-time GGUF import. The Gemma family places at or
near the top of every size class measured.

The default `qwen2.5:0.5b` is retained only because it is the one registry build
measured as stable on the documented 2 GB minimum; it complied on about half of its
samples. Note that **no model that fits a 2 GB host complies reliably** - the
compliant models need roughly 1 GB of model resident. Both LFM2.5 models need the
one-time GGUF import described in [MODELS.md](MODELS.md).

Two failure modes are worth knowing before you deploy:

- **Reasoning models answer with an empty message.** `qwen3`, `qwen3.5`,
  `minicpm5`, `ling-3.0` and `spark-x2.5` emit a hidden reasoning block by default.
  With a mesh-sized `AI_MAX_TOKENS` that reasoning consumes the whole budget, so the
  bridge posts nothing. Prefer a non-thinking model, or send `think: false` when your
  client supports it.
- **An oversized model can reset the node.** On a Raspberry Pi with the hardware
  watchdog armed, memory starvation reboots the board instead of returning an error.

Models are only recommended if they also **obey the reply contract**: three bullets
or fewer, under 90 words, plain text, a finished sentence, and grounded in the
retrieved context with its source named. Speed alone does not qualify a model,
because a rambling answer costs airtime on a shared channel and an empty one looks
like an outage.

[**MODELS.md**](MODELS.md) is the full guide: measured load, time-to-first-token,
tokens/second and peak memory for 20+ models on a 2 GB Pi 4 and an 8 GB Pi 5, the
`scripts/benchmark_models.sh` harness, verdicts on published Pi 5 benchmarks, how to
install models that are not in the Ollama registry, and how to cap the Ollama
service so an oversized model fails cleanly.

### Offline knowledge bundles

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
| `AI_MODEL` | `qwen2.5:0.5b` | Model name |
| `SENSOR_URL` | empty | Optional local JSON sensor adapter endpoint |
| `AI_SYSTEM_PROMPT` | (see `.env.example`) | System prompt |
| `AI_MAX_TOKENS` | `220` | Max generated tokens; truncated answers receive a short continuation |
| `AI_COMPLETION_ATTEMPTS` | `3` | Maximum completion/continuation attempts before an incomplete answer is discarded |
| `AI_QUEUE_MAX` | `8` | Maximum queued AI requests |
| `PACKET_DEDUPE_SECONDS` | `120` | Duplicate packet suppression window |
| `AI_TEMPERATURE` | `0.7` | Sampling temperature |
| `REPLY_TO_BROADCAST` | `false` | Also answer channel broadcasts |
| `REPLY_MAX_CHARS` | `180` | Maximum chunk size; prefer sentence boundaries, then words |
| `REPLY_CHANNELS` | empty | Optional comma-separated channel allowlist |
| `REPLY_ON_LONGFAST` | `false` | Permit replies when channel 0 is named LongFast |
| `CHUNK_DELAY_SECONDS` | `1.0` | Delay between reply chunks |
| `DIRECT_RETRY_COUNT` | `2` | Additional direct-send attempts after failure |
| `DIRECT_RETRY_DELAY_SECONDS` | `2.0` | Delay between direct-send attempts |
| `AI_QUEUE_TIMEOUT_SECONDS` | `180` | Expire queued requests after this time |
| `BOT_LOOP_MARKERS` | `m@i,~ai` | Prefixes from other AI bots to ignore |
| `BOT_LOOP_WINDOW_SECONDS` | `300` | Recent-response loop suppression window |
| `WEB_RETRIEVAL_ENABLED` | `true` | Allow public weather/news/Wikipedia retrieval |
| `LOCAL_WIKI_ENABLED` | `true` | Search the local SQLite Wikipedia index first |
| `LOCAL_WIKI_INDEX` | `/opt/meshtastic-ai-bridge/data/wiki.sqlite3` | Local FTS5 index path |
| `LOCAL_KIWIX_ENABLED` | `true` | Search the local full-text Kiwix corpus first |
| `LOCAL_KIWIX_URL` | `http://127.0.0.1:8766` | Loopback Kiwix search service |
| `KIWIX_BUNDLES` | `prompt` | Offline bundle selection |
| `KIWIX_MAX_DOWNLOAD_GB` | `4` | Maximum estimated bundle download |

Model selection and sizing are covered in [MODELS.md](MODELS.md).

Direct replies use Meshtastic reliable delivery with ACK/NAK tracking. Broadcast
replies are logged as queued transmissions because a broadcast has no single
recipient from which to request a delivery acknowledgement. Bridge logs include
the packet ID, destination, channel, and delivery response when available.
The local health endpoint at `http://127.0.0.1:8765/health` reports structured
receive, queue, reply, and delivery state without exposing node data or secrets.

### Local MCP tools

The bridge includes an optional local-only MCP-compatible JSON-RPC endpoint. It
reuses the bridge-owned Meshtastic connection and never opens a second radio
session. It is disabled by default:

```dotenv
MCP_ENABLED=false
MCP_HOST=127.0.0.1
MCP_PORT=8767
MCP_AUTH_TOKEN=
MCP_ALLOW_WRITE=false
```

When enabled, read-only tools include `mesh_health`, `mesh_snapshot`,
`mesh_nodes`, `mesh_channels`, `recent_interactions`, and `offline_search`.
Write access requires both `MCP_ALLOW_WRITE=true` and a bearer token. Keep the
endpoint bound to loopback unless a separately protected reverse proxy is
required. The dashboard reports MCP enabled state, call count, denied calls,
and the last tool name without displaying the token.

The endpoint accepts JSON-RPC 2.0 at `http://127.0.0.1:8767`. Read-only MCP
access can use a bearer token for consistent client behavior; write access must
never be enabled without a non-empty token. The default production setting is
disabled.

### Self-checks

Run the offline repository checks before deploying. They do not require a radio,
AI backend, network access, or secrets:

```bash
./scripts/self_check.sh
```

The checks parse every project Python file (discovered, not hand-listed), run
`bash -n` against every shell script, validate the Markdown links, anchors and
tables, detect duplicate `.env.example` keys, verify that MCP remains disabled by
default, and validate the Docker Compose file when Docker is installed. A failed
check returns a non-zero exit status, so it can be used from CI or a deployment
script.

Unit tests cover the logic that decides what goes on the air - reply chunking,
channel gating, loop suppression, delivery classification, retrieval-adapter
routing, the local FTS5 index round trip, and the model-compliance gate:

```bash
python3 -m unittest discover -s tests -v
```

Tests that import the bridge need its requirements installed and skip without
them; set `REQUIRE_BRIDGE_DEPS=1` to turn a missing dependency into a failure.
`.github/workflows/ci.yml` runs both suites plus `shellcheck` on every push and
pull request, and needs no secrets.

### Optional Docker services

The radio daemon, bridge, and dashboard remain native systemd services because
they need host access to the SPI radio, Meshtastic TCP endpoint, and local files.
Docker Compose is provided for the two services that benefit most from isolation:
the local Ollama model server and the Kiwix HTTP server. Both bind only to
loopback by default.

Start either service, or both:

```bash
docker compose --profile ollama up -d
docker compose --profile kiwix up -d
# or: docker compose --profile all up -d
```

The Ollama model cache is kept in the named `ollama_models` volume. Kiwix reads
ZIM files from `./zim` (or `KIWIX_DATA_DIR=/absolute/path/to/zim`) read-only.
After starting Ollama, pull the configured model inside the container:

```bash
docker compose exec ollama ollama pull qwen2.5:0.5b
```

The host bridge can use the containerized Ollama endpoint with the existing
default `AI_API_BASE=http://127.0.0.1:11434/v1`. For containerized Kiwix, keep
the host bridge's `LOCAL_KIWIX_URL=http://127.0.0.1:8766`. Docker is optional;
the existing native Ollama and systemd Kiwix paths remain supported and are
preferable on a constrained Pi when container overhead or image storage matters.

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

A local web dashboard runs on port **8080** and binds to `127.0.0.1` by default.
Set `DASHBOARD_HOST=0.0.0.0` only when LAN access is intentionally required,
and protect it with network controls because the dashboard is unauthenticated.
It lets you:

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
| Empty AI reply | A reasoning model (`qwen3`, `qwen3.5`, `minicpm5`, `ling-3.0`, `spark-x2.5`) may be spending the whole budget on hidden reasoning. Prefer a non-thinking model; see [MODELS.md](MODELS.md) |
| Node reboots or hangs under load | The model is larger than the host's RAM. Cap the Ollama service as described in [MODELS.md](MODELS.md) |

## Files

```text
MODELS.md                          AI model guide: sizing, measured benchmarks, GGUF imports
setup.sh                           one-line installer
bridge/bridge.py                   Meshtastic <-> AI daemon (logs to responses.jsonl, reads live_config.json)
bridge/requirements.txt            Python deps
bridge/.env.example                configuration template
scripts/build_local_wiki_index.py  builds the optional local Wikipedia FTS5 index
scripts/benchmark_models.py        measures candidate models on the node (cold load, TTFT, tok/s, memory)
scripts/benchmark_models.sh        wrapper for benchmark_models.py
scripts/configure_mesh.sh          applies .env -> meshtastic (region, channels, MQTT, admin key)
scripts/install_offline_knowledge.sh downloads selected Kiwix offline bundles
scripts/start_kiwix.sh             starts the local Kiwix HTTP service
tests/                             unit tests for reply, retrieval, index and model-scoring logic
dashboard/                         local Flask web dashboard (port 8080)
meshtasticd/config.d/              radio presets (RAK13300 / RAK13302 / MeshStick) + README
meshtasticd/config.yaml.example    optional Web server settings
systemd/meshtastic-ai-bridge.service
systemd/meshtastic-kiwix.service
systemd/meshtastic-dashboard.service
.github/workflows/ci.yml           CI: repository self-checks, unit tests, shellcheck
```

## References

- [meshtasticd docs](https://meshtastic.org/docs/meshtasticd/)
- [meshtasticd installation](https://meshtastic.org/docs/meshtasticd/installation/)
- [meshtasticd usage](https://meshtastic.org/docs/meshtasticd/usage/)
- [meshtasticd hardware](https://meshtastic.org/docs/meshtasticd/hardware/)
- [Meshtastic Python library](https://github.com/meshtastic/python)
