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
   ollama pull qwen2.5:1.5b-instruct-q4_K_M
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

## How It Works

| Part | Role |
|------|------|
| **meshtasticd** | Native Meshtastic daemon owning the SPI radio. Listens for clients on TCP port 4403. |
| **bridge.py** | Connects to meshtasticd over TCP, watches for `TEXT_MESSAGE_APP`, calls the AI API, and posts the reply back. |
| **systemd** | Keeps both `meshtasticd.service` and `meshtastic-ai-bridge.service` running across reboots. |

The bridge uses the Meshtastic Python library (`TCPInterface`) against the local
daemon, so no extra hardware or serial port is needed. Long replies are split
into Meshtastic-sized chunks automatically.

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
| `AI_API_BASE` | `http://127.0.0.1:11434/v1` | OpenAI-compatible base URL |
| `AI_API_KEY` | `ollama` | API key (any string for a local server) |
| `AI_MODEL` | `qwen2.5:1.5b-instruct-q4_K_M` | Model name |
| `AI_SYSTEM_PROMPT` | (see `.env.example`) | System prompt |
| `AI_MAX_TOKENS` | `300` | Max reply length |
| `AI_TEMPERATURE` | `0.7` | Sampling temperature |
| `REPLY_TO_BROADCAST` | `false` | Also answer channel broadcasts |
| `REPLY_MAX_CHARS` | `180` | Chunk size used to split replies |

### Local model (default)

The bridge defaults to a small model served by [Ollama](https://ollama.com/)
on the same Pi. Install it and pull the model:

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:1.5b-instruct-q4_K_M
```

Pick a model sized for your Pi 4B RAM:

| Model | ~Size | Pi 4 RAM | Use |
|-------|-------|----------|-----|
| `qwen3.5:0.8b` | ~0.5-0.8 GB | 2 GB+ | Best starting point |
| `lfm2.5:1.2b-instruct` | ~0.7 GB | 2-4 GB+ | Efficiency experiment |
| `llama3.2:1b-instruct-q4_K_M` | ~0.7 GB | 2-4 GB+ | Comparison model |
| `qwen2.5:1.5b-instruct-q4_K_M` | ~1 GB | 4 GB+ | **Safe default** |
| `qwen3:1.7b-instruct-q4_K_M` | ~1.4 GB | 4 GB+ | Higher-quality experiment |
| `qwen2.5:3b-instruct-q4_K_M` | ~1.8 GB | 8 GB | Upper-end experiment |

Tag names change over time, so confirm with `ollama search <name>` before pulling.

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
bridge/bridge.py                   Meshtastic <-> AI daemon
bridge/requirements.txt            Python deps
bridge/.env.example                configuration template
meshtasticd/config.d/              radio presets (RAK13300 / RAK13302 / MeshStick) + README
meshtasticd/config.yaml.example    optional Web server settings
systemd/meshtastic-ai-bridge.service
```

## References

- [meshtasticd docs](https://meshtastic.org/docs/meshtasticd/)
- [meshtasticd installation](https://meshtastic.org/docs/meshtasticd/installation/)
- [meshtasticd usage](https://meshtastic.org/docs/meshtasticd/usage/)
- [meshtasticd hardware](https://meshtastic.org/docs/meshtasticd/hardware/)
- [Meshtastic Python library](https://github.com/meshtastic/python)
