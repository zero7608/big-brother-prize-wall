# Big Brother Memory Wall: Android Edition

An Android port of the Raspberry Pi build in `../pi/`: tap an RFID key,
push a button, and a wall of prize photos shuffles for ten seconds before
landing on a winner. This version replaces the Raspberry Pi with a phone,
tablet, or Fire TV device. The only separate hardware is the same small
battery-powered wireless unit described in `../BUILD.md`, watching the RFID
reader and the button and talking to the app over Bluetooth Low Energy.

## How it works

- **The wireless unit** (an ESP32-C6 or a Nice!Nano V2) reads a PN532 RFID
  module and a push button, and advertises itself as `PrizeShack` over BLE
  using the Nordic UART Service. No pairing step; the app finds it by name
  and connects.
- **The Android app** owns everything else: the state machine (arm, spin,
  reveal, evict), the animated wall, the audio, and night persistence.
  Nothing about the show depends on a specific screen; a phone, a tablet,
  and a Fire TV all work, since every real interaction comes from the
  wireless unit, not touch.

## Project layout

| Path | Purpose |
|---|---|
| `app/src/main/kotlin/org/prizeandroid/wall/ble/` | The BLE link to the wireless unit |
| `app/src/main/kotlin/org/prizeandroid/wall/game/` | The state machine, night persistence, scan log |
| `app/src/main/kotlin/org/prizeandroid/wall/audio/` | Cue loading and playback |
| `app/src/main/kotlin/org/prizeandroid/wall/ui/` | The Canvas renderer |
| `app/src/main/assets/config.json` | Prizes, rigged tags, timing, colours, sound cues |
| `app/src/main/assets/images/`, `assets/sounds/` | Prize photos and audio (empty, see the README inside each folder) |

## Building

```bash
cd android
./gradlew installDebug     # with a device connected over USB, debugging on
```

A debug build keeps a small corner strip of testing controls (simulate a
tag tap or button press, read the last tag UID for setting up `key_names`).
A release build strips all of it for a clean kiosk screen.

Before building, replace `key_names` and `rigged_tags` in
`app/src/main/assets/config.json` with your own guests and UIDs (read them
off with the debug build's UID display, or `scan_uid.py` from the Pi build
if you have a spare reader wired up), and drop prize photos and sound cues
into `assets/images/` and `assets/sounds/`. The app builds and runs without
any of that filled in, just with plain colour boxes and no audio.

## Setup guide

Wiring diagrams and parts, from bare boards to first night, are in
[`../BUILD.md`](../BUILD.md). Everything about prizes, rigged tags, timing,
and the wireless unit's firmware is shared with the Raspberry Pi build and
documented in [`../pi/README.md`](../pi/README.md); only the install step
above differs.

## Attribution

The ESP32-C6 photo referenced in the setup guide is
[RISC-V ESP32-C6-WROOM-1.devboard.jpg](https://commons.wikimedia.org/wiki/File:RISC-V_ESP32-C6-WROOM-1.devboard.jpg),
licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).
No freely licensed photos exist for the PN532 module or the Nice!Nano V2,
so those stay diagram-only.
