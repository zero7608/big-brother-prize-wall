# Big Brother Memory Wall

Tap a key on the PN532 reader and the wall of prize photos shuffles for 10
seconds, decelerates, and lands on a winner.

**How a turn goes.** A guest taps their key on the reader; the wall greets them
by name and asks them to push the button. Nothing is decided at that moment —
if they wander off, the wall returns to waiting after
`behaviour.armed_timeout_seconds` (default 45) having spent nothing. When they
push the button the shuffle runs, and only then is the prize chosen and claimed.

Nine prizes sit on the wall. Six are a genuinely random draw. The other three
are tied to specific RFID keys — whoever holds that key wins that prize the
first time they tap in, but the shuffle that gets there is byte-for-byte the
same animation, so it is indistinguishable from a real draw. Tap the same key
again and it behaves like any other key.

Each prize can only be won once a night. When a prize is claimed its photo goes
black and white and is crossed out, exactly the way the memory wall marks an
evicted houseguest, and the shuffle stops visiting it.

---

## 1. Wiring the PN532 (SPI)

**Set the DIP switches on the module to SPI first.** The AITRIP/Elechouse V3
board has a two-position switch block:

| Mode | SET0 (switch 1) | SET1 (switch 2) |
|---|---|---|
| UART / HSU | OFF | OFF |
| I2C | ON | OFF |
| **SPI** | **OFF** | **ON** |

Then wire it up:

| PN532 pin | Pi pin | Pi signal |
|---|---|---|
| VCC | 1 | 3V3 |
| GND | 6 | GND |
| SCK | 23 | GPIO11 / SCLK |
| MISO | 21 | GPIO9 / MISO |
| MOSI | 19 | GPIO10 / MOSI |
| SS (NSS/CS) | 24 | GPIO8 / CE0 |

Pin numbers are the same on a Pi 3 and a Pi 4: same 40-pin header, same SPI0
pins, same GPIO numbering.

The V3 board regulates its own supply and its logic is 3.3V, so use the 3V3
pin, not 5V. If the module's blue LED never lights, check the DIP switches
before anything else — the wrong mode is the usual cause of "PN532 not found".

Enable the SPI bus:

```bash
sudo raspi-config      # Interface Options -> SPI -> Yes
sudo reboot
```

## 1b. Wiring the button

A momentary push switch between **GPIO 17 (pin 11)** and any **GND** pin. No
resistor needed — the internal pull-up is enabled, so the pin idles high and
the press pulls it to ground.

| Switch leg | Pi pin |
|---|---|
| one leg | 11 (GPIO 17) |
| other leg | 9 (GND) |

Same pins on a Pi 3 and a Pi 4.

Change the pin in `config.json` under `button.pin`. If your switch is wired the
other way (to 3V3 rather than ground) set `button.pull_up: false`. Contact
bounce is filtered by `button.bounce_seconds`.

**ENTER on a keyboard always works as the button too.** That is deliberate: if
the switch fails on the night you can still run the show without editing
anything.

## 1c. Running it on a Pi 4

Everything above is unchanged. Nothing in the code is tied to a Pi model:
`board.D8` for the reader's chip select and GPIO 17 for the button are both
resolved by BCM number, which is identical across the Pi 3 and Pi 4. A Pi 4 is
also noticeably faster at the thing this program spends its time on, which is
pushing pixels, so the display will be smoother than on a Pi 3.

Four things differ, none of them code:

- **Micro-HDMI, and two of them.** You need a different cable. If both ports
  are populated the wall opens on the primary display, which is **HDMI0**, the
  port nearest the USB-C power socket.
- **On Bookworm the desktop is Wayland.** The `SDL_VIDEODRIVER=kmsdrm` line in
  the systemd unit below is for booting to a console with no desktop. Running
  inside the desktop session, drop that line and use `graphical.target`.
- **More places for the audio to go.** Both micro-HDMI ports carry audio, plus
  the 3.5mm jack. Same `raspi-config` selection, but more wrong answers to
  pick from, so run `--test-audio` once after you choose.
- **`python3-gpiozero` is already present** on a current Raspberry Pi OS image,
  but the install line below includes it anyway.

**A Pi 5 is not a drop-in.** Its GPIO runs through the RP1 chip, `RPi.GPIO`
does not work there, and gpiozero needs the lgpio backend. The Pi 4 has none of
that problem.

## 1d. Moving the reader off the Pi (wireless, optional)

The reader and the button can live on a battery-powered board somewhere else in
the room, talking to the Pi over Bluetooth Low Energy. The Pi keeps the screen
and the sound; the wireless unit does nothing but read tags and watch a button.

**Which board.** This has to be BLE, not Bluetooth Classic, because current
chips do not have Classic:

| Board | Classic | BLE | WiFi |
|---|---|---|---|
| ESP32-C6, C3, S3 | no | **yes** | yes (C6, C3) |
| nRF52840 (Nice!Nano V2) | no | **yes** | no |
| ESP32 WROOM-32 (older, Xtensa) | yes | yes | yes |

Only the last one can do the serial profile. Everything below uses BLE, which
works on all of them.

If you have a choice, use the **ESP32-C6**: a party is a crowded 2.4GHz room,
and if BLE turns out to be unreliable the C6 can be moved to WiFi without new
hardware. Use the **Nice!Nano** if you want it small and battery powered, since
it has LiPo charging built in.

### The PN532 goes in I2C mode for this

Two wires instead of four, which matters on boards with few exposed pins. Set
the DIP switches accordingly, the opposite of the Pi wiring above:

| Mode | SET0 (switch 1) | SET1 (switch 2) |
|---|---|---|
| **I2C** | **ON** | **OFF** |
| SPI | OFF | ON |

### ESP32-C6

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

| Button | ESP32-C6 |
|---|---|
| one leg | GPIO 4 |
| other leg | GND |

No resistor on the button: the internal pull-up is enabled in the firmware, so
the pin idles high and reads low when pressed.

### Nice!Nano V2 (nRF52840)

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
| VCC | **3.3V**, not RAW | |
| GND | GND | |
| SDA | **P0.17** (the pad marked D15) | |
| SCL | **P0.22** | **not P0.20**, see below |

| Button | Nice!Nano |
|---|---|
| one leg | **P0.06** (the pad marked D1) |
| other leg | GND |

**Do not use P0.20 for anything.** Holding it low at boot forces the Nice!Nano
bootloader into DFU mode. It is next to P0.17 and is the obvious choice for the
second I2C wire, but a PN532 that pulls the clock line down while powering up
would stop the firmware ever starting, and the board would look dead rather
than misconfigured. SCL is on P0.22 for that reason alone.

**RAW is battery or USB voltage and will be about 5V.** The PN532's logic is
3.3V. Use the 3.3V pin.

**Pins here are port pins, not board labels.** P0.06 is the pad silkscreened D1
on a Nice!Nano, D11 on an Adafruit Feather, and nothing at all on some other
board. The firmware states port pins and looks up whatever the variant it was
compiled against happens to call them, so the wiring above is correct
regardless of which board definition was used to build it. That matters because
there is no nice!nano definition in the Arduino core or in PlatformIO: it is
built as a Feather nRF52840, whose labels are not the ones printed on your
board.

If a LiPo is wanted, check before connecting one. Measure P0.02's battery pad
(`B+`) to GND with USB plugged in and no cell attached: about 4.2V means there
is a charging circuit, about 5V means there is not and connecting a cell will
overcharge it. A genuine Nice!Nano charges over USB; clones sold as compatible
do not always.

### Flashing the unit

The sketch is `firmware/prize_reader_ble/prize_reader_ble.ino` and covers both
boards: it detects which it is compiling for and uses NimBLE on the ESP32 or
Bluefruit on the nRF52840.

**1. Board support.** In the Arduino IDE, File -> Preferences -> Additional
Board Manager URLs, then Tools -> Board -> Boards Manager:

| Board | URL to add | Package to install |
|---|---|---|
| ESP32-C6 | `https://espressif.github.io/arduino-esp32/package_esp32_index.json` | esp32 by Espressif, **3.0.0 or later** |
| Nice!Nano | `https://www.adafruit.com/package_adafruit_index.json` | Adafruit nRF52 |

The C6 needs esp32 core 3.x. Earlier versions do not know the chip exists.

**2. Libraries.** Tools -> Manage Libraries, install **Adafruit PN532**. On the
ESP32 NimBLE comes with the core; on the nRF52840 Bluefruit comes with the
Adafruit package.

**3. Board selection.** ESP32-C6: Tools -> Board -> ESP32C6 Dev Module. The
Nice!Nano is not in the list under that name; choose **Nordic nRF52840 DK** or
Adafruit Feather nRF52840 Express, both of which work.

**4. Upload.** The Nice!Nano has no USB-serial chip and appears as a drive:
double-tap the reset button, wait for `NICENANO` to mount, and the IDE will
upload to it. The C6 is a normal USB upload.

**5. Or skip the Arduino IDE entirely.** `pio run` in `firmware/` builds both
boards without it. For the Nice!Nano, `firmware/hex2uf2.py` turns the build
into a `.uf2`; double-tap reset until a drive named `NICENANO` appears, then
copy that file onto it. The board reboots into the firmware as soon as the
copy finishes; the drive vanishing part-way through is normal.

**6. Check it worked.** Open the serial monitor at 115200. It should say:

```
BLE up as "PrizeShack"
PN532 firmware 1.6
```

If the built-in LED blinks steadily and it says the PN532 was not found, the
DIP switches are still on SPI. That is the usual cause.

### Setting up the Pi

```bash
pip3 install bleak
```

Then in `config.json`:

```json
"reader": {
  "type": "ble",
  "name": "PrizeShack",
  "address": "",
  "reconnect_seconds": 3.0
},
"button": { "type": "wireless" }
```

Leave `address` empty. The Pi finds the unit by the **service it offers**
rather than by its name, which matters more than it sounds: the board puts its
name in the BLE scan response, and plenty of adapters do not resolve that. On
one laptop here the unit appeared with an empty name and only its service
uuid, so a search by name would have missed a device sitting right in front of
it. The service uuid is also what actually makes it this device, where a name
is a string anyone can change.

`address` is accepted but rarely useful: the board advertises with a random
privacy address, and BlueZ will only connect to one it has just seen in a scan,
so a remembered MAC tends to fail with "device not found".

No pairing step: BLE here is a plain connection, not a bonded one.

**Check the Pi can see it** before running the show:

```bash
bluetoothctl scan on          # look for PrizeShack, ctrl-C when you see it
```

Then start the wall normally. It prints `Wireless unit over BLE (PrizeShack)`
when it connects and `wireless unit: PrizeShack pn532 ble` when the unit
identifies itself.

### If it does not connect

| Symptom | Cause |
|---|---|
| `BLE needs bleak` | `pip3 install bleak` |
| `no unit offering the Nordic UART Service` | unit not powered, out of range, or **already connected to something else** — a phone holding the connection makes it invisible to everything, since it accepts one central at a time |
| `wireless unit reports: pn532 not found` | the unit is fine, its reader is not: check the DIP switches are on I2C |
| Connects, then `unit has gone quiet` | out of range, or the unit reset. It reconnects on its own every few seconds |
| Tags read but the button does nothing | `button.type` is not `wireless` |
| Nothing at all, no errors | `reader.type` is still `pn532` |

The wall never dies because the unit is missing: if it cannot connect it simply
never sees a tag, and says so on stdout rather than refusing to start.

### What it sends

Plain text lines, so it can be read with any BLE debugging app:

```
V PrizeShack pn532 ble     once, on connect
T 04:A1:B2:C3              a tag is on the reader
B                          the button was pressed
E pn532 not found...       something is wrong; the unit keeps talking anyway
H                          heartbeat, once a second
```

A unit that cannot find its reader still connects, still sends heartbeats, and
says what is wrong, rather than going quiet. Without that, a broken reader and
a unit out of range look identical from the Pi.

**Lines are sent in twenty byte pieces and reassembled on the newline.** A BLE
notification carries the MTU less three, and the default MTU is twenty-three,
so anything longer is silently cut off. A MIFARE Classic 1K has a four byte
UID and its line is fourteen bytes, comfortably inside one packet; a seven byte
UID, which is what NTAG and Ultralight and the Classic EV1 carry, comes to
twenty-three and would arrive short. A short UID matches no rigged key and
looks exactly like a flaky reader.

**`T` repeats about seven times a second while a tag sits on the reader, and
that is deliberate.** A key stays on the pad for the whole turn, and the wall
decides a key has been taken away by not having seen it for
`behaviour.rescan_lockout_seconds`. That is what stops one guest taking a
second turn while their key lies there. A unit that announced each tag once
would make the wall hand out extra prizes, and it would look like a game bug
rather than a protocol one.

### Bluetooth Classic instead

If you have an older Xtensa ESP32 (WROOM-32, DevKitC), there is a second sketch
at `firmware/prize_reader/` using Bluetooth Classic and the serial profile.
Wire the PN532 in SPI mode to GPIO 18/19/23/5, pair it, bind it, and set
`reader.type` to `bluetooth` with `port` pointing at the rfcomm device:

```bash
bluetoothctl                       # scan on, pair <MAC>, trust <MAC>
sudo rfcomm bind 0 <MAC> 1         # creates /dev/rfcomm0
```

It needs `pyserial` rather than `bleak`. There is no advantage to it unless you
already own the board.

## 2. Install on the Pi

```bash
sudo apt update
sudo apt install -y python3-pygame python3-pip python3-gpiozero git
pip3 install --break-system-packages adafruit-circuitpython-pn532 adafruit-blinka
```

Check the reader can see the board and read a tag:

```bash
python3 scan_uid.py
```

Hold each of your three special tags on the reader and write down the UIDs it
prints — you need them in the next step.

## 3. Configure

Everything lives in `config.json`. Copy the example to get started:

```bash
cp config.example.json config.json
```

`config.json` itself isn't tracked in this repo (it's in `.gitignore`), since
it ends up holding your guests' names and their RFID key UIDs.

**Where a prize sits on the wall** is simply its position in the `prizes`
array: the first three entries are the top row, the next three the middle, the
last three the bottom. Nothing else depends on the order, so you can rearrange
them freely.

**Scatter the rigged prizes.** Keep the three `weight: 0` prizes apart in the
array rather than listing them together at the end. If they sit in a block, two
things give the game away: their frames are the only ones never lit by an
honest win, and once six guests have drained the random pool the leftovers form
an obvious cluster. The shipped order puts them at positions 2, 6 and 7, so no
two share a row or a column:

```
p1  s1  p2
p3  p4  s2
s3  p5  p6
```

Rearranging is safe. `rigged_tags` and the saved night state both refer to
prizes by `id`, never by position, so moving an entry never breaks the link
between a key and its prize. Renaming an `id` does.

**Or let it arrange itself.** Set `theme.shuffle_wall: true` and the wall picks
a fresh arrangement each night, so you never have to think about placement:

```json
"theme": { "shuffle_wall": true }
```

It is not a plain shuffle. A plain shuffle can just as easily drop all three
rigged prizes into one row, which is the arrangement you were trying to avoid,
so it keeps drawing until no two of them share a row or a column. Over 400
arrangements it produced 398 distinct walls and never once clustered them.

The chosen arrangement is written to `night_state.json`, so restarting
mid-party puts every prize back in the frame the guests already saw. **R** and
`--reset` start a new night and deal a new wall. If the saved arrangement no
longer matches the prizes in the config, because you added or renamed one, it
is discarded and reshuffled rather than applied half-right. With
`shuffle_wall` on, the startup banner prints the order it used.

**Prizes.** Nine entries. `weight` controls the random draw: the six with
`weight: 1` are the random pool, and the three secret prizes have `weight: 0`
so they can *never* come up by chance — they only appear via their key. They
still sit on the wall and still get highlighted during the shuffle, which is
what makes the rig invisible. Change a weight to make a prize rarer or more
common (`0.5` is half as likely as `1`).

**Names on the keys.** `key_names` maps a UID to the name shown on the welcome
screen. Anyone not listed is greeted with `theme.default_guest`
("HOUSEGUEST"), so unknown keys still work:

```json
"key_names": {
  "04:A1:B2:C3:D4:E5:F6": "Alex"
}
```

The welcome wording lives in `theme.welcome_line` and `theme.button_line`;
`{name}` in the welcome line is replaced with the guest's name.

**Rigged tags.** Paste in the UIDs from `scan_uid.py`:

```json
"rigged_tags": {
  "04:A1:B2:C3:D4:E5:F6": "s1",
  "04:11:22:33:44:55:66": "s2",
  "04:AA:BB:CC:DD:EE:FF": "s3"
}
```

The key is the UID exactly as printed; the value is a prize `id`. Any tag not
listed here gets a random prize. UIDs are usually 4 or 7 bytes.

**The shuffle.** The highlight does not march along the grid — it follows a
shuffled route built fresh for each spin, so it jumps around unpredictably and
lands on the winner as its final stop. Every prize still gets visited about
equally often. `spin.tick_sound` fires once per jump, so the beeping starts
frantic and slows with the animation; measured beeps per second over the ten
seconds are 23, 16, 11, 8, 5, 4, 2, 1, 1, 1. The last jump is the big one: the
route pauses on the runner-up for about nine tenths of a second before landing
on the winner, and that final jump is the bell rather than a beep.

**The landing.** When the shuffle stops it holds on the winner for
`spin.land_pause_seconds` (default `0.5`) — lit in its frame, exactly where it
landed, with the bell — before the picture zooms up. Without that beat the win
reads as the prize changing after the spin rather than the spin choosing it.
Raise it for a longer stare, or set it to `0` to go straight to the reveal.

**Timing.** `spin.seconds` is the 10-second shuffle. `spin.min_ticks` is roughly
how many tiles it steps through — raise it for a faster, more frantic spin,
lower it for a slower one. `reveal_hold_seconds` is the *minimum* time the winner
stays up. The reveal actually waits for whatever it is saying to finish, plus
`reveal_tail_seconds`, whichever is longer. Prize lines differ by seconds, from
a two second announcement to Zingbot's ten second joke, and a fixed hold either
cut the long ones off mid-sentence or left the short ones sitting in silence.
Writing a longer line now needs no other change.

**One turn per key.** A guest puts their key on the reader and leaves it there
until they have their prize, so the reader sees that same key for the whole
turn. `behaviour.rescan_lockout_seconds` (default 3) is how long a key must be
**off** the reader before it counts as a new tap. A key resting on the pad is
seen on every poll and never qualifies, so the wall will not give the same
guest a second turn the moment the reveal ends; lift it off and the next guest
can go. A brief wobble in the field is shorter than the lockout and is ignored,
so a flickery read cannot hand out a free turn either.

**One win per prize.** `behaviour.once_per_night: true` is the rule that each
prize can only be won once. Set it to `false` for an unlimited-draws mode where
nothing is ever evicted.

**How the night runs out.** Six guests with ordinary keys will take the six
random prizes, and the wall then shows *THE HOUSE IS EMPTY* — but your three
plants can still walk up and claim theirs. That is `reserve_rigged_prizes:
true`: a secret prize is held back for its key and can never be handed to a
walk-up guest, so a plant who arrives late still gets their prize. If you would
rather every prize be winnable by anyone once the random six are gone, set it
to `false` — but then a late plant may find their prize already given away.

## 4. Adding pictures and music later

Nothing needs to change in the code — just drop files in and name them in
`config.json`.

**Pictures** go in `assets/images/`. PNG or JPG. PNG is fine in every
flavour: plain RGB, 8-bit palette, greyscale, and RGBA with transparency.
A transparent PNG is composited onto a dark backing inside the frame, so a
cut-out product shot sits on something deliberate rather than showing the
frame's shadow through it.

Put the filename in that prize's `image` field:

```json
{ "id": "p1", "name": "Spa Weekend", "image": "spa.png", "sound": "spa.wav", "weight": 1 }
```

That is the whole job. The filename is relative to `assets/images/`, so
`"image": "spa.png"` means `assets/images/spa.png`. An absolute path works too.
Until you add a photo, a prize shows a plain coloured box with its name under
it, so the wall runs fine half-finished.

Each photo is scaled up until it completely fills its frame and then
centre-cropped, so there are never letterbox bars whatever shape the file is.
**The frames are 4:3 landscape**, so a 4:3 photo keeps everything and anything
much taller or wider loses its edges. Crop to 4:3 yourself if a particular
picture matters. Around 1000px on the long edge is plenty; larger files cost
memory and look no better.

**Changing the label under a picture** means changing that prize's `name`:

```json
{ "id": "p1", "name": "Spa Weekend For Two", ... }
```

The `name` is what shows on the wall beneath the frame, and it is also the big
caption at the reveal. It is drawn in capitals whatever case you type, and
shrinks to fit its frame width, so long names still fit but get small. Two or
three words reads best.

Do **not** change the `id`. That is what `rigged_tags` points at, and renaming
one breaks the link between a key and its prize. The `id` is internal and never
appears on screen.

To drop the labels entirely and match the real memory wall, set
`theme.captions: false`. The winner is still named in full at the reveal.

**Sounds** go in `assets/sounds/`, as WAV or OGG (pygame's MP3 support is
patchy — convert with `ffmpeg -i in.mp3 out.ogg`).

**There is no audio until sound files exist.** The folder starts empty, so the
show runs silent out of the box and prints `AUDIO: no cues loaded` at startup.
For a starter set of placeholder cues:

```bash
python3 make_sounds.py            # writes the three spin/reveal cues
python3 prize_wall.py --test-audio  # plays every cue in turn, then exits
```

`make_sounds.py` synthesises plain placeholder cues with nothing but the
standard library. Replace them with your own recordings using the same
filenames whenever you are ready; it will not overwrite existing files unless
you pass `--force`.

If `--test-audio` says it played cues and you still hear nothing, the problem
is the Pi's audio output, not the app — pick the right one with
`sudo raspi-config` → System Options → Audio (HDMI vs the headphone jack).

Eight cues, all optional:

| Setting | When it plays |
|---|---|
| `spin.start_sound` | once, the instant a card is tapped |
| `spin.armed_sound` | a key is tapped and the guest is welcomed |
| `spin.tick_sound` | one beep per jump — so the beeping slows with the shuffle |
| `spin.loop_sound` | loops for the whole 10-second shuffle (off by default) |
| `spin.bell_sound` | the shuffle lands on the winner |
| `spin.reveal_sound` | the winner zooms up |
| `spin.reveal_tail_seconds` | quiet held after the line finishes (default 2) |
| `spin.idle_music` | loops on the attract screen, fades out on tap |
| a prize's own `sound` | that specific prize wins — **instead of** `reveal_sound` |

**Spoken cues.** The announcer greets each guest by name, asks for the button,
and announces what they have won. All of it was synthesised for the original build with a private
voice-cloning setup that isn't part of this repository (it needs real
reference recordings of real people, which aren't something to publish).
Record your own lines instead, using the two-clips-per-line pattern below,
or any TTS tool of your choice.

Most lines are two clips played back to back on one channel, so the wall never
stores a recording of every guest crossed with every prize:

```
congrats_autumn.wav  +  prize_veto.wav
"Congratulations Alex,"   "you have won the Golden Power of Veto."
```

Adding a guest costs five clips rather than one per prize. Each guest also gets
three different greetings, and one is picked at random when they tap, so
someone who uses their key twice does not hear the same words twice.

A prize's own line replaces `spin.reveal_sound` rather than layering over it:
two cues at the same instant only muddy each other. Prizes without one still
fall back to the general reveal sound.

**The button waits for the announcer.** A guest who mashes the button during
the greeting does not talk over it. The press is not thrown away either, so it
fires the instant she stops, and the on-screen PUSH THE BUTTON prompt fades up
over the last half second rather than inviting a press that will be ignored.

**A prize can speak for itself instead of the announcer.** Set that prize's
`opener` to `"none"` and give it its own `sound`, and it plays alone with no
greeting line in front of it. The original build used this for Zingbot's
joke, voiced separately from the general announcer.

**The reference voices are clones of real people**, so these files are fine for
a wall in a private house and are not fine for anything published or
redistributed. None of it is the show's own audio, which belongs to CBS and is
not legally downloadable anywhere.

A missing file is reported on stdout at startup and then ignored, so the show
still runs with a half-finished sound pack. The startup banner always says how
many cues loaded, and if the Pi has no audio device at all the show runs silent
rather than refusing to start.

**Look.** `display.background` and `display.accent` are hex colours; `accent`
drives the eye, the highlight border and the captions. Neither colours the
background, which keeps its own palette. `display.font` can point
at a .ttf in `assets/fonts/` for a themed typeface.

**Frames.** Every photo sits in a moulded 4:3 frame with a bevelled edge and a
bright inner ring, modelled on the real memory wall. `theme.frame_colour` sets
the metal (a muted teal by default); the lit frame switches to
`display.accent` so the shuffle is obvious. Frames are sized from the height
available to a row and then held to 4:3, so they keep their proportions on any
screen, and the leftover width becomes the gap between them.

**Captions.** Prize names sit on the wall *under* each frame rather than across
the photo, so the pictures stay clean like the real thing. The text comes from
each prize's `name` (see *Adding pictures and music* above). Set
`theme.captions: false` to drop them entirely for the closest match.

**The background.** `theme.background_style` picks one of two, and
`theme.waves: false` turns the background off entirely for a flat fill.

`"timetrip"` is the default and the season 28 look: deep space navy under a
digital grid, a glowing alcove behind every prize frame pulsing slowly in cyan,
purple or magenta, neon lights drifting left to right, and a warp seam that
sweeps across every so often.

The alcoves are placed from the wall's **own layout**, so the light lines up
with the pictures rather than with a grid the background guessed for itself.
Change the number of prizes or the screen size and they follow.

| Setting | Effect |
|---|---|
| `background_style` | `"timetrip"` or `"pool"` |
| `warp_streaks` | how many neon lights drift across (default 20) |
| `warp_seam` | `false` drops the sweeping warp seam |
| `wave_speed` | animation rate for either style, `1.0` is normal |
| `waves` | `false` turns the background off entirely |

`"pool"` is the earlier water tank, worked from the show's title sequence: a
violet pool with a tile wall, a net of caustics, churn along the floor and a
hue that drifts from indigo to magenta on a 96 second cycle. It has its own
settings, ignored by the Time Trip style: `wave_detail` for how fine the
caustic net is, `wave_chroma` for the hue cycle, and `wave_quality` for the
resolution the caustics are generated at. `wave_quality` affects build time and
memory only, never the frame rate.

**How both stay cheap.** Neither computes anything per pixel per frame. Every
gradient, streak, caustic net and glow is built once at startup and thereafter
only blitted, and the glows are 24 bit surfaces added onto the scene with
`BLEND_RGB_ADD`, which is the cheapest blend SDL has and is also what light
actually does, so the black around a sprite costs nothing. Time Trip goes
further: positions are integers in 1/256ths of a pixel advanced by the
millisecond delta the clock already returns, pulses come from a 1024 entry sine
table indexed with a masked integer, and each glow is pre-rendered at 16
brightnesses so a pulse is an array index rather than a recolour.

Measured at 1920x1080 on a desktop, Time Trip against the pool: idle 3.0ms a
frame against 3.8ms, a spin 3.1ms against 4.1ms, the reveal 9.3ms against
10.5ms, and about 25MB less resident. Startup 0.11s against 0.28s. **Desktop
numbers under SDL's dummy driver. Nothing here has been measured on a Pi.**

**On dirty rectangles.** `timetrip_wall.py` is a standalone preview of this
background, and there the same scene runs better than twice as fast by
repainting only what moved. That is deliberately *not* done in the wall: the
wall composites nine tiles, their captions and a header over the background on
every frame, so the screen is never still enough for it to pay. Run
`python3 timetrip_wall.py --bench 600` to see the difference on its own.

**numpy.** Only the `"pool"` style needs it, and only to generate its caustic
fields, which is the difference between a fifth of a second and several. It is
in `requirements.txt`, but without it that style falls back to a quarter
resolution pure Python build. Time Trip does not use numpy at all.

**Wording.** The `theme` block holds every line of on-screen text, so you can
rename the house or rewrite the captions without touching the code:

| Setting | Where it appears |
|---|---|
| `house_name` | the header bar, next to the eye |
| `idle_line` | under the wall while it waits for a key |
| `spin_line` | under the wall during the 10-second shuffle |
| `reveal_line` | the header bar during the winner reveal |
| `exhausted_line` | when there is nothing left for a walk-up guest |
| `show_eye` | set `false` to drop the eye logo |

## 5. Running

```bash
python3 prize_wall.py                 # fullscreen, real reader only
python3 prize_wall.py --simulate      # no reader needed at all
python3 prize_wall.py --keys          # real reader AND keyboard taps
python3 prize_wall.py --windowed      # force a window instead of fullscreen
python3 prize_wall.py --reset         # start a fresh night, wall full again
python3 prize_wall.py --test-audio    # play every sound cue, then exit
python3 prize_wall.py --fps           # print real frame rates, to find slow spots
```

**If the display stutters on the Pi**, run with `--fps`. It prints the real
frame rate and the worst frame for each stage, which says whether the problem
is the wall, the shuffle or the reveal. Lowering `display.max_fps` to `30` or setting
`theme.waves: false` cut the work substantially. Note that `theme.wave_quality`
is not a frame-rate knob: the water costs the same three blits a frame whatever
it is set to.

**Fullscreen fills whatever screen it finds.** It asks the display for its own
current resolution rather than using `display.width`/`height`, so it fills a
1080p TV, a 4:3 monitor or a small Pi touchscreen without you configuring
anything. The whole layout is measured from the surface, so the grid, the
photos and the type all resize to fit. `width`/`height` are only used for the
window when `display.fullscreen` is `false` or you pass `--windowed`, and **F**
switches between the two at any time.

**SPACE only works if you asked for it.** Plain `python3 prize_wall.py` listens
to the PN532 and nothing else, so that nobody can win a prize by leaning on the
keyboard during the party. To fire taps from the keyboard, add `--simulate`
(no reader needed, for building the show on any machine) or `--keys` (real
reader still live, for testing on the Pi without hunting for a tag).

With either flag: **SPACE** fires a random unknown key, and **1**/**2**/**3**
fire your rigged keys in the order they appear in `config.json`. The screen
says `SIMULATOR` or `KEYBOARD ENABLED` under the header whenever keyboard taps
are live, so you can always tell which mode you are in.

On startup the app prints a banner giving the mode, how many prizes are still
available, and the keys it accepts. If something is not responding, read that
banner first — it will say either that keyboard taps are off, or that the wall
is empty.

Keys while running: **ENTER** acts as the button, **ESC** or **Q** quits,
**F** toggles fullscreen, **R** resets the night and puts every prize back on
the wall. These always work,
whether or not keyboard taps are enabled.

Every scan is printed and appended to `scans.log` with a timestamp, the UID,
what was won, whether it was random or rigged, and how many prizes are left —
handy for checking the night went the way you meant it to.

**The night survives a restart.** Which prizes are gone is written to
`night_state.json` the instant a winner is decided, not when the reveal
finishes, so a crash or a power blip mid-spin can never hand the same prize out
twice. Restarting picks up where it left off and says so. To start a genuinely
new night, press **R**, run with `--reset`, or delete `night_state.json`.

## 6. Autostart on boot (optional)

```bash
sudo tee /etc/systemd/system/prizewall.service >/dev/null <<'UNIT'
[Unit]
Description=RFID Memory Wall
After=multi-user.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/RFIDPrize
ExecStart=/usr/bin/python3 /home/pi/RFIDPrize/prize_wall.py
Environment=SDL_VIDEODRIVER=kmsdrm
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl enable --now prizewall
```

Adjust `User` and the paths if you did not put the project in `/home/pi`.
Drop the `SDL_VIDEODRIVER` line and use `graphical.target` if you would rather
run it inside the desktop session.

## Files

| File | Purpose |
|---|---|
| `config.example.json` | copy to `config.json` and edit: prizes, rigged tags, timing, colours, sound cues |
| `prize_wall.py` | the show: shuffle, reveal, display, audio |
| `visuals.py` | the animated background and the framed tiles |
| `reader.py` | every tag source: the wired PN532, the wireless unit over BLE or Classic, and the keyboard stand-ins |
| `scan_uid.py` | prints tag UIDs so you can fill in `rigged_tags` |
| `make_sounds.py` | writes placeholder audio cues into `assets/sounds/` |
| `timetrip_wall.py` | standalone preview and benchmark of the background |
| `firmware/prize_reader_ble/` | wireless unit for an ESP32-C6 or nRF52840 (BLE) |
| `firmware/prize_reader/` | wireless unit for an older ESP32 (Bluetooth Classic) |
| `tests/run_all.py` | the whole test suite; four files, no hardware needed |
| `night_state.json` | which prizes are gone (written at runtime, not in this repo) |
| `assets/images/` | prize photos (empty, add your own) |
| `assets/sounds/` | cues and spoken lines (empty, add your own) |
