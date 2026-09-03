# Wireless reader unit firmware

Two Arduino sketches for the small battery-powered board that reads the
PN532 and the button and talks to the host (Pi or Android) over Bluetooth.
Wiring is in `../BUILD.md`.

| Sketch | Board | Bluetooth |
|---|---|---|
| `prize_reader_ble/` | ESP32-C6, ESP32-C3, ESP32-S3, or Nice!Nano V2 (nRF52840) | BLE (Nordic UART Service) |
| `prize_reader/` | Older Xtensa ESP32 (WROOM-32, DevKitC) | Bluetooth Classic (serial profile) |

Use `prize_reader_ble` unless you already own an older Xtensa ESP32; current
chips don't have Classic at all.

## Building

```bash
cd firmware
pio run -e esp32c6      # or: pio run -e nicenano
```

PlatformIO builds both boards without the Arduino IDE. `hex2uf2.py` turns a
Nice!Nano build into a `.uf2` for drag-and-drop flashing; full instructions,
including the Arduino IDE path, board manager URLs, and what to do if the
reader isn't found, are in `../pi/README.md` section 1d.

No prebuilt binaries are shipped in this repository; build output goes to
`.pio/`, which is gitignored.
