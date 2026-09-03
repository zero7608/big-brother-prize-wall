# Build Guide

Everything needed to build a physical prize wall: parts, wiring, and first
boot.  Read `pi/README.md` (Raspberry Pi build) or `android/README.md`
(phone/tablet/Fire TV build) for the software side once the hardware below
is wired up.  Pick one path, not both, unless you want a spare.

## 1. Choose a build

| | Raspberry Pi | Android |
|---|---|---|
| Display | Any HDMI monitor or TV | The device's own screen |
| Runs the show | `pi/prize_wall.py` (Python) | The `android/` app (Kotlin) |
| RFID reader | Wired directly to the Pi, or wireless | Always wireless (BLE) |
| Good for | A dedicated kiosk box behind the TV | Reusing a spare phone or tablet, or a Fire TV stick |

Both paths need the same wireless reader unit if you go BLE; the Pi path
can skip it entirely and wire the reader straight to the Pi's header instead.

## 2. Parts list

### Always needed

| Part | Notes | Where to look |
|---|---|---|
| PN532 NFC/RFID module (V3, with DIP switches) | The switch block is what selects SPI vs. I2C mode; boards without it can't do both wiring options in this guide | [Search Amazon](https://www.amazon.com/s?k=PN532+NFC+RFID+module+V3) |
| MIFARE Classic 1K key fobs or cards, 13.56MHz | One per guest, plus the ones you're rigging | [Search Amazon](https://www.amazon.com/s?k=RFID+key+fob+13.56mhz+mifare) |
| 30mm arcade push button, momentary, normally-open | Any generic Sanwa/Zippy-style clone works; get the plain (non-illuminated) version unless you want to wire an LED too | [Search Amazon](https://www.amazon.com/s?k=30mm+arcade+push+button) |
| Jumper wires (female-female for header pins, male ends for breadboarding the reader) | | [Search Amazon](https://www.amazon.com/s?k=dupont+jumper+wires) |

### Raspberry Pi build

| Part | Notes | Where to look |
|---|---|---|
| Raspberry Pi 3 or 4 | Not a Pi 5, see `pi/README.md` for why | [Search Amazon](https://www.amazon.com/s?k=raspberry+pi+4) |
| microSD card, 16GB+ | For Raspberry Pi OS | [Search Amazon](https://www.amazon.com/s?k=microsd+card+32gb) |
| HDMI cable + display or TV | Micro-HDMI on the Pi side for a Pi 4 | [Search Amazon](https://www.amazon.com/s?k=micro+hdmi+cable) |
| USB-C power supply, 5V/3A | Official Pi supplies avoid brownouts under load | [Search Amazon](https://www.amazon.com/s?k=raspberry+pi+power+supply+usb-c) |

### Wireless reader unit (optional on Pi, required on Android)

Pick one board:

| Part | Notes | Where to look |
|---|---|---|
| ESP32-C6 DevKit | Best all-around choice: BLE now, WiFi later if BLE proves flaky in a crowded room | [Search Amazon](https://www.amazon.com/s?k=esp32-c6+devkit) |
| Nice!Nano V2 (nRF52840) clone | Smaller, built-in LiPo charging, good if you want it battery-powered and tucked out of sight | [Search Amazon](https://www.amazon.com/s?k=nice+nano+v2+nrf52840) |
| LiPo battery, 500-1000mAh with JST connector (Nice!Nano only) | Verify the charging circuit before connecting one, see `pi/README.md` | [Search Amazon](https://www.amazon.com/s?k=lipo+battery+jst+500mah) |

### Android build only

| Part | Notes | Where to look |
|---|---|---|
| Android phone, tablet, or Fire TV stick | Anything running Android 8+ with Bluetooth LE | (whatever's in a drawer) |

## 3. Wiring: the reader and button

**Set the PN532's DIP switches first.** This is the single most common
"reader not found" cause, checked before anything else:

| Mode | SET0 (switch 1) | SET1 (switch 2) | Used for |
|---|---|---|---|
| SPI | OFF | ON | Reader wired directly to a Pi |
| I2C | ON | OFF | Reader on a wireless unit (ESP32-C6 or Nice!Nano) |

### Option A: reader wired directly to a Raspberry Pi (SPI)

| PN532 pin | Pi pin | Pi signal |
|---|---|---|
| VCC | 1 | 3V3 |
| GND | 6 | GND |
| SCK | 23 | GPIO11 / SCLK |
| MISO | 21 | GPIO9 / MISO |
| MOSI | 19 | GPIO10 / MOSI |
| SS (NSS/CS) | 24 | GPIO8 / CE0 |

Use the 3V3 pin, not 5V; the module's logic is 3.3V.

**Button**, wired straight to the Pi's own GPIO (no separate wireless unit
needed for this path):

| Button terminal | Pi pin |
|---|---|
| one microswitch leg | 11 (GPIO 17) |
| other microswitch leg | 9 (GND) |

A 30mm arcade button's microswitch has two spade terminals marked (or
functionally equivalent to) COM and NO. Either wire goes to either Pi pin,
polarity doesn't matter for a plain switch. No resistor needed, the Pi's
internal pull-up is enabled in software.

### Option B: wireless reader unit (ESP32-C6 or Nice!Nano, both boards over BLE)

```
            ESP32-C6 DevKit                     PN532 V3  (DIP: 1 ON, 2 OFF)
         +---------------------+              +---------------------+
         |                     |              |                     |
    3V3 -| 3V3             GND |- GND    +----| VCC             SDA |----+
         |                     |         |    |                     |    |
         |              GPIO 6 |---------|----|---------------- SCL |--+ |
         |              GPIO 7 |---------|----+                     |  | |
         |                     |         |    |                 GND |--|-|--+
         |              GPIO 4 |---+     |    +---------------------+  | |  |
         |                     |   |     |                             | |  |
         |                 GND |---|-----|-----------------------------|-|--+
         +---------------------+   |     |                             | |
                                   |     |     GPIO 6 -> SDA ----------|-+
                              [ button ] |     GPIO 7 -> SCL ----------+
                                   |     |
                                  GND    3V3
```

| PN532 | ESP32-C6 |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SDA | GPIO 6 |
| SCL | GPIO 7 |

| Button (30mm arcade, microswitch terminals) | ESP32-C6 |
|---|---|
| one leg | GPIO 4 |
| other leg | GND |

Or, on a Nice!Nano V2:

```
             Nice!Nano V2                       PN532 V3  (DIP: 1 ON, 2 OFF)
         +---------------------+              +---------------------+
     RAW |o                   o| 3.3V --------| VCC                 |
     GND |o                   o| GND ---------| GND                 |
     RST |o                   o| P0.17 -------| SDA                 |
     VCC |o                   o| P0.22 -------| SCL                 |
      D1 |o  (P0.06)          o|              +---------------------+
         |   |                 |
         +---|-----------------+
             |
        [ button ]---- GND
```

| PN532 | Nice!Nano | Why this pin |
|---|---|---|
| VCC | 3.3V, not RAW | RAW is battery/USB voltage, about 5V; the PN532's logic is 3.3V |
| GND | GND | |
| SDA | P0.17 (pad marked D15) | |
| SCL | P0.22, not P0.20 | P0.20 held low at boot forces the bootloader into DFU mode; a PN532 pulling the clock line down at power-up would keep the firmware from ever starting |

| Button | Nice!Nano |
|---|---|
| one leg | P0.06 (pad marked D1) |
| other leg | GND |

No resistor on either board; both firmwares enable the internal pull-up.

**Full flashing instructions, library setup, and troubleshooting** (including
what to do if the reader isn't found, the BLE connection drops, or you're
using an older Bluetooth Classic ESP32 instead) are in `pi/README.md`
section 1d, which applies to both the Pi and Android builds since both talk
to the same wireless unit the same way.

## 4. Illuminated arcade buttons (optional)

If you bought the illuminated version instead of the plain one, it adds two
more terminals for the LED, usually rated 5V or 12V (check the listing).
Wire LED+ through a suitable resistor to a spare 3V3 or 5V pin and LED- to
GND for a button that's always lit, or leave it disconnected entirely; the
software doesn't drive the LED, so it's cosmetic either way.

## 5. First power-on checklist

1. Reader's DIP switches match the wiring option you built (Section 3).
2. Run `python3 scan_uid.py` (Pi) or use the debug build's UID reader
   (Android) and confirm each key fob prints a UID when tapped.
3. Push the button by hand and confirm the app sees it (the Pi prints a
   line; the Android debug build has a test button).
4. Only then move on to `pi/README.md` or `android/README.md` for software
   setup: config, prizes, rigged tags, and adding your own photos and sound.

## Attribution

The ESP32-C6 wiring diagram photo referenced in `android/README.md` is
[RISC-V ESP32-C6-WROOM-1.devboard.jpg](https://commons.wikimedia.org/wiki/File:RISC-V_ESP32-C6-WROOM-1.devboard.jpg),
licensed [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). No
freely licensed photos exist for the PN532 module or the Nice!Nano V2, so
those stay diagram-only above.
