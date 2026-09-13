# meshtasticd radio presets

Drop exactly one of these into `/etc/meshtasticd/config.d/` to enable a radio.
`setup.sh` does this automatically for the default RAK13300 Slot 1. Only one
`Lora:` block may be active at a time.

| File | Radio | Interface |
|------|-------|-----------|
| `lora-RAK13300-slot1.yaml` | RAK13300 in WisMesh/RAK6421 slot 1 | SPI `spidev0.0` |
| `lora-RAK13300-slot2.yaml` | RAK13300 in slot 2 | SPI `spidev0.1` |
| `lora-RAK13302-slot1.yaml` | RAK13302 in slot 1 (TX gain table) | SPI `spidev0.0` |
| `lora-RAK13302-slot2.yaml` | RAK13302 in slot 2 (TX gain table) | SPI `spidev0.1` |
| `lora-meshstick-1262.yaml` | MeshStick CH341 USB-to-SPI SX1262 | USB (`ch341`) |

The **MeshTadpole** needs no preset: it carries an onboard EEPROM and
meshtasticd (2.6.5+) configures it automatically when you plug it in.

These are copies of the official presets meshtasticd installs to
`/etc/meshtasticd/available.d/`. You can select them there with `--preset`
instead, for example `sudo ./setup.sh --preset lora-RAK6421-13302-slot1`.
