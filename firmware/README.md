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

## Over the air

The `prize_reader_ble` firmware carries the Nordic DFU service on the
Nice!Nano, so once it is on the board it can be updated without a cable: build
the package (`pio run -e nicenano` writes `firmware.zip` under
`.pio/build/nicenano/`), open **nRF Connect** on a phone, connect to
`PrizeShack`, tap **DFU**, and pick the zip. The host must not be connected to
the unit at the same time.

## Robustness notes

The nRF52 build includes fixes that only showed up on a real Fire TV and a
real night of use:

- Each line goes out in a single notification with its newline, since Android 7
  can overwrite a shared characteristic value when two notifications land
  together.
- The UART service is registered before DFU. The reverse order moves the UART
  handles and a host that cached the old layout drops the unit every ten
  seconds.
- A stuck PN532 is recovered by clocking the I2C bus free and retrying every
  three seconds.
- A software timer restarts the board if the main loop stalls for five
  seconds, and the unit reports `E restarted after a stall` once reconnected.
  It is a timer, not the hardware watchdog, so it cannot interrupt an update.

Never open the serial port while a USB flash is running; a flash interrupted
that way can leave the board in its bootloader. Reflashing with
`adafruit-nrfutil dfu serial` recovers it.
