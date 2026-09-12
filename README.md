# Meshtastic AI Bridge

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
- An **SPI LoRa radio HAT** wired to the Pi. Confirmed SPI hats include the
  **Adafruit RFM9x**, **Elecrow RFM95 IOT**, and **RAK** SPI boards. UART hats and
  LoRaWAN (SX1302/SX1303) hats do not work with meshtasticd.
- An AI endpoint: OpenAI, a local [Ollama](https://ollama.com/) server, LM Studio,
  vLLM, Groq, OpenRouter, or anything speaking the `/v1/chat/completions` protocol.

## One-Line Install

```bash
sudo ./setup.sh
```

That is the whole install. It enables SPI, installs meshtasticd, copies the
matching LoRa radio preset, installs the bridge, and starts both systemd
services. It pauses only to ask which radio preset matches your HAT.

To pick the preset in advance (non-interactive):

```bash
sudo ./setup.sh --preset lora-MeshAdv-900M30S
```

## Then

1. Edit the AI settings:

   ```bash
   sudo nano /opt/meshtastic-ai-bridge/.env
   ```

   Set `AI_API_BASE`, `AI_API_KEY`, and `AI_MODEL` for your backend. Example
   presets live in `.env.example`.

2. Reboot so SPI is enabled, then check the radio:

   ```bash
   sudo reboot
   /opt/meshtastic-ai-bridge/venv/bin/meshtastic --host localhost --info
   ```

   (Or install the CLI separately with `pip install meshtastic`.)

3. Send a **direct message** to the Pi's node from a Meshtastic phone or app.
   The bridge replies with the AI's answer. Broadcast channel messages are
   ignored unless `REPLY_TO_BROADCAST=true` is set in `.env`.

## How It Works

| Part | Role |
|------|------|
| **meshtasticd** | Native Meshtastic daemon owning the SPI radio. Listens for clients on TCP port 4403. |
| **bridge.py** | Connects to meshtasticd over TCP, watches for `TEXT_MESSAGE_APP`, calls the AI API, and posts the reply back. |
| **systemd** | Keeps both `meshtasticd.service` and `meshtastic-ai-bridge.service` running across reboots. |

The bridge uses the Meshtastic Python library (`TCPInterface`) against the local
daemon, so no extra hardware or serial port is needed. Long replies are split
into Meshtastic-sized chunks automatically.

### Radio preset

meshtasticd ships ready-made pinouts at `/etc/meshtasticd/available.d/`. The
installer copies one into `/etc/meshtasticd/config.d/` to enable your radio:

```bash
ls /etc/meshtasticd/available.d    # list LoRa presets
sudo cp /etc/meshtasticd/available.d/lora-<your-hat>.yaml /etc/meshtasticd/config.d/
sudo systemctl restart meshtasticd
```

If no shipped preset matches your HAT, check the
[meshtasticd hardware page](https://meshtastic.org/docs/meshtasticd/hardware/)
for pin details.

## Configuration

All bridge settings are environment variables in `/opt/meshtastic-ai-bridge/.env`:

| Variable | Default | Meaning |
|----------|---------|---------|
| `MESHTASTIC_HOST` | `localhost` | meshtasticd host |
| `MESHTASTIC_PORT` | `4403` | meshtasticd TCP API port |
| `AI_API_BASE` | `https://api.openai.com/v1` | OpenAI-compatible base URL |
| `AI_API_KEY` | | API key (use `ollama` or any string for local servers) |
| `AI_MODEL` | `gpt-4o-mini` | Model name |
| `AI_SYSTEM_PROMPT` | (see `.env.example`) | System prompt |
| `AI_MAX_TOKENS` | `300` | Max reply length |
| `AI_TEMPERATURE` | `0.7` | Sampling temperature |
| `REPLY_TO_BROADCAST` | `false` | Also answer channel broadcasts |
| `REPLY_MAX_CHARS` | `180` | Chunk size used to split replies |

### Local Ollama example

```bash
ollama pull llama3.1
```

```dotenv
AI_API_BASE=http://127.0.0.1:11434/v1
AI_API_KEY=ollama
AI_MODEL=llama3.1
```

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
| No radio | SPI not enabled: reboot, then `ls /dev/spidev*` |
| `meshtastic --host localhost` fails | meshtasticd not running: `systemctl status meshtasticd` |
| No AI reply | `.env` has a valid key; `journalctl -u meshtastic-ai-bridge` |
| Replies only to DMs | Expected. Set `REPLY_TO_BROADCAST=true` for channel replies |
| Short replies | `AI_MAX_TOKENS` too low, or replies split by `REPLY_MAX_CHARS` |

## Files

```text
setup.sh                           one-line installer
bridge/bridge.py                   Meshtastic <-> AI daemon
bridge/requirements.txt            Python deps
bridge/.env.example                configuration template
meshtasticd/config.yaml.example    optional Web server settings
systemd/meshtastic-ai-bridge.service
```

## References

- [meshtasticd docs](https://meshtastic.org/docs/meshtasticd/)
- [meshtasticd installation](https://meshtastic.org/docs/meshtasticd/installation/)
- [meshtasticd usage](https://meshtastic.org/docs/meshtasticd/usage/)
- [meshtasticd hardware](https://meshtastic.org/docs/meshtasticd/hardware/)
- [Meshtastic Python library](https://github.com/meshtastic/python)
