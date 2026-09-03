# Big Brother Memory Wall

Tap an RFID key, push a button, and a wall of prize photos shuffles for ten
seconds before landing on a winner. Nine prizes on the wall: most a genuine
random draw, a couple secretly tied to specific keys so the right person
walks away with the right prize, with a shuffle animation that's
byte-for-byte identical either way.

Built for one family's annual finale watch party as a rigged-but-invisible
prize draw for the youngest guests. Two ways to run it:

- **`pi/`**, a Raspberry Pi and a screen, the reader wired straight to the
  Pi or connected wirelessly.
- **`android/`**, the same show as an Android app, for reusing a spare
  phone, tablet, or Fire TV instead of dedicating a Pi to it.

Both talk to the same small battery-powered wireless reader unit if you
want the RFID pad untethered from the display, an ESP32-C6 or a Nice!Nano
V2 running the firmware in `firmware/`.

## Start here

1. **[`BUILD.md`](BUILD.md)** — parts list with purchase links, wiring
   diagrams, first power-on.
2. **[`pi/README.md`](pi/README.md)** or **[`android/README.md`](android/README.md)**
   — software setup: config, prizes, rigged tags, timing, sound.
3. **[`DISCLAIMER.md`](DISCLAIMER.md)** — the legal fine print, read before
   you show this to anyone outside your own living room.

## No media included

This repository ships code only. No prize photos, no sound cues, no show
typeface, no app icons. Every image and audio folder has its own short
README explaining what goes there and how the app behaves before you add
anything (short version: it runs fine half-finished, with plain colored
boxes and no sound, so you can wire the hardware and test the game logic
before touching art at all). Two reasons for leaving it out:

- None of it is licensed for redistribution: it's photos of prizes I
  bought, recordings of my own family, and one voice-cloning experiment
  that used real reference audio.
- It keeps this repository small and makes clear that the code is the
  reusable part. Your prize wall should have your prizes on it.

## Repository layout

| Path | What's in it |
|---|---|
| `pi/` | The Raspberry Pi build: Python show, wiring, config |
| `android/` | The Android build: Kotlin app, same wiring and config shape |
| `firmware/` | Arduino sketches for the wireless RFID/button unit |
| `BUILD.md` | Parts, purchase links, wiring diagrams |
| `DISCLAIMER.md` | Not affiliated with CBS or the show; personal-use only |
| `LICENSE` | MIT for the code; see DISCLAIMER.md for everything else |

## License

Code is MIT-licensed, see [`LICENSE`](LICENSE). Show references (prize
names, on-screen wording) are cosmetic config values you're free to change;
see [`DISCLAIMER.md`](DISCLAIMER.md) for why they're not covered by that
license.
