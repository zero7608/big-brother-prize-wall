#!/usr/bin/env python3
"""Big Brother memory wall.

Tap a key on the PN532 and the wall of photos shuffles for 10 seconds, slows
down, and lands on a prize. Won prizes are "evicted" from the wall: their photo
turns black and white and they cannot be won again that night.

Most keys land somewhere genuinely random. Three known keys land on their own
prize the first time they are tapped, using exactly the same shuffle, so there
is nothing to see from the outside. Tap one of those a second time and it
behaves like any other key.

    python3 prize_wall.py              # on the Pi, real reader
    python3 prize_wall.py --simulate   # anywhere, keyboard instead of a reader
"""

import argparse
import datetime
import json
import math
import os
import random
import re
import sys
import time

import pygame

import reader as reader_mod
import visuals as vis

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE_DIR = os.path.join(HERE, "assets", "images")
SOUND_DIR = os.path.join(HERE, "assets", "sounds")
FONT_DIR = os.path.join(HERE, "assets", "fonts")

IDLE, ARMED, SPINNING, LANDED, REVEAL, EXHAUSTED = (
    "idle", "armed", "spinning", "landed", "reveal", "exhausted")

# How much of the spin curve stays linear. A pure ease-out has zero speed at
# the end, which leaves the highlight parked on the winner for the last few
# seconds and gives the game away. Mixing in a little constant drift keeps the
# highlight crawling one tile at a time right up to the reveal.
SPIN_TAIL = 0.15


def hex_colour(value, fallback=(0, 0, 0)):
    if not value:
        return fallback
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def spin_curve(p):
    """0..1 -> 0..1, fast at the start, slow but never stopped at the end."""
    return (1.0 - SPIN_TAIL) * (1.0 - (1.0 - p) ** 4) + SPIN_TAIL * p


def ease_out(p):
    return 1.0 - (1.0 - p) ** 4


def greyscale(surface):
    """Evicted-houseguest black and white."""
    try:
        return pygame.transform.grayscale(surface)
    except AttributeError:
        # pygame < 2.1.4 has no grayscale(); approximate by washing out colour.
        out = surface.copy()
        veil = pygame.Surface(out.get_size(), pygame.SRCALPHA)
        veil.fill((128, 128, 128, 190))
        out.blit(veil, (0, 0))
        return out


class Prize:
    """One prize: its name, photo and optional sting."""

    # Placeholder tile colours, used until real artwork is dropped in.
    PALETTE = [
        (198, 61, 61), (61, 128, 198), (76, 168, 96), (196, 140, 52),
        (140, 84, 190), (52, 168, 168), (198, 96, 150), (110, 122, 158),
        (168, 120, 72),
    ]

    def __init__(self, index, spec):
        self.id = spec["id"]
        self.name = spec["name"]
        self.weight = float(spec.get("weight", 1))
        self.colour = self.PALETTE[index % len(self.PALETTE)]
        self.image = self._load_image(spec.get("image"))
        self.sound = self._load_sound(spec.get("sound"))
        # Which opener is spoken before this prize's line. The announcer says
        # the guest's name, so the openers are per guest and the prize half is
        # shared; "congrats" for a win, "badnews" for slop.
        self.opener = spec.get("opener", "congrats")
        self._cache = {}

    @staticmethod
    def _load_image(name):
        if not name:
            return None
        path = name if os.path.isabs(name) else os.path.join(IMAGE_DIR, name)
        if not os.path.exists(path):
            print(f"  ! missing image: {path}")
            return None
        return pygame.image.load(path).convert_alpha()

    @staticmethod
    def _load_sound(name, quiet=False):
        if not name:
            return None
        if not pygame.mixer.get_init():
            return None
        path = name if os.path.isabs(name) else os.path.join(SOUND_DIR, name)
        if not os.path.exists(path):
            if not quiet:
                print(f"  ! missing sound: {path}")
            return None
        return pygame.mixer.Sound(path)

    # Wall tile lit, wall tile dim, evicted, and the reveal hero: four sizes
    # is all the layout ever needs. More than that means something is asking
    # for a new size every frame, so drop the lot rather than grow without
    # bound.
    CACHE_LIMIT = 6

    def photo(self, size, evicted=False, shade=None):
        """Photo cropped to completely fill `size`, like a memory wall frame.

        Scaled to cover rather than fit, so every box is full of picture with
        no letterbox bars, and centre-cropped to the box. Cached per size, so
        only ever call this with sizes that hold still — animate by scaling the
        result, not by asking for a new size each frame.
        """
        if self.image is None:
            return None
        key = (size, evicted, shade)
        if key not in self._cache:
            if len(self._cache) >= self.CACHE_LIMIT:
                self._cache.clear()
            iw, ih = self.image.get_size()
            scale = max(size[0] / iw, size[1] / ih)
            big = pygame.transform.smoothscale(
                self.image, (max(1, int(iw * scale)), max(1, int(ih * scale)))
            )
            frame = pygame.Surface(size, pygame.SRCALPHA)
            frame.blit(big, big.get_rect(center=(size[0] // 2, size[1] // 2)))
            if evicted:
                frame = greyscale(frame)
            if shade is not None:
                # Bake the knock-back in, rather than copying and re-shading
                # every tile on every frame.
                frame.fill((shade, shade, shade, 255),
                           special_flags=pygame.BLEND_RGB_MULT)
            self._cache[key] = frame
        return self._cache[key]


class PrizeWall:
    def __init__(self, config, simulate=False, allow_keys=False, windowed=False,
                 show_fps=False):
        self.cfg = config
        self.simulate = simulate
        self.show_fps = show_fps

        dcfg = config["display"]
        # pre_init before pygame.init(): a smaller buffer keeps the cues
        # snappy on a Pi instead of lagging behind the animation.
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        try:
            pygame.mixer.init()
            pygame.mixer.set_num_channels(16)
        except pygame.error as exc:
            print(f"  ! no audio device ({exc}) — the show will run silent")

        self.window_size = (dcfg.get("width", 1280), dcfg.get("height", 720))
        self.fullscreen = bool(dcfg.get("fullscreen", True)) and not windowed
        self._set_mode()
        pygame.display.set_caption(dcfg.get("title", "Big Brother Memory Wall"))
        if dcfg.get("hide_mouse", True):
            pygame.mouse.set_visible(False)

        self.bg = hex_colour(dcfg.get("background"), (4, 16, 28))
        print(f"Display: {self.screen.get_width()}x{self.screen.get_height()}"
              f"{' fullscreen' if self.fullscreen else ' windowed'}")
        self.accent = hex_colour(dcfg.get("accent"), (47, 208, 255))
        self._font_path = self._find_font(dcfg.get("font"))
        if self._font_path:
            print(f"Font: {os.path.basename(self._font_path)}")
        self._fonts = {}
        self._eye_cache = {}
        self._openers = {}
        self._overlay = None
        self._glow = None
        self._reveal_bg = None

        self.theme = config.get("theme", {})
        self.frame_colour = hex_colour(self.theme.get("frame_colour"), (150, 172, 190))
        # "timetrip" is the season 28 look: a lit grid, neon flow and a warp
        # seam.  "pool" is the season 28-and-earlier water tank.
        self.bg_style = str(self.theme.get("background_style", "timetrip")).lower()
        self.warp_streaks = int(self.theme.get("warp_streaks", 20))
        self.warp_seam = bool(self.theme.get("warp_seam", True))
        self.waves = bool(self.theme.get("waves", True))
        self.wave_speed = float(self.theme.get("wave_speed", 1.0))
        self._field = None
        # `wave_detail` sets how fine the caustic net is.  It used to be
        # `wave_bands`, back when the water was drawn as stacked ribbons, so
        # that name still works rather than silently ignoring an old config.
        self._wave_detail = int(self.theme.get(
            "wave_detail", self.theme.get("wave_bands", 5)))
        self.wave_quality = int(self.theme.get("wave_quality", 2))
        self.wave_chroma = bool(self.theme.get("wave_chroma", True))
        self._tile_cache = {}
        self._layout_key = None
        self._layout = []
        self.captions = bool(self.theme.get("captions", True))

        self.prizes = [Prize(i, spec) for i, spec in enumerate(config["prizes"])]
        self.by_id = {p.id: p for p in self.prizes}
        for uid, pid in config["rigged_tags"].items():
            if pid not in self.by_id:
                raise SystemExit(f"rigged_tags: {uid} points at unknown prize id '{pid}'")

        scfg = config["spin"]
        self.spin_seconds = float(scfg.get("seconds", 10.0))
        self.min_ticks = int(scfg.get("min_ticks", 72))
        self.reveal_hold = float(scfg.get("reveal_hold_seconds", 12.0))
        # The reveal waits for whatever it is saying, plus this. Prize lines
        # differ by seconds: a bare announcement runs two, Zingbot's joke runs
        # eleven, and a fixed hold either cuts the long ones off mid-sentence
        # or leaves the short ones sitting in silence.
        self.reveal_tail = float(scfg.get("reveal_tail_seconds", 2.0))
        self.reveal_until = 0.0
        self.snd_start = Prize._load_sound(scfg.get("start_sound"))
        self.snd_loop = Prize._load_sound(scfg.get("loop_sound"))
        self.snd_reveal = Prize._load_sound(scfg.get("reveal_sound"))
        self.snd_tick = Prize._load_sound(scfg.get("tick_sound"))
        self.snd_bell = Prize._load_sound(scfg.get("bell_sound"))
        self.snd_armed = Prize._load_sound(scfg.get("armed_sound"))
        self.land_pause = float(scfg.get("land_pause_seconds", 0.5))
        self.idle_music = scfg.get("idle_music")

        bcfg = config.get("behaviour", {})
        self.once_per_night = bool(bcfg.get("once_per_night", True))
        self.reserve_rigged = bool(bcfg.get("reserve_rigged_prizes", True))
        # How long a key must be OFF the reader before it counts as a new
        # tap. A key stays on the pad for the whole turn, so this is measured
        # from when the key was last seen, not from when it was last used.
        self.lockout = float(bcfg.get("rescan_lockout_seconds", 3.0))
        self.max_fps = int(dcfg.get("max_fps", 60))
        self.log_path = self._resolve(bcfg.get("log_file"))
        self.state_path = self._resolve(bcfg.get("state_file"))

        # Which prize hangs in which frame. The prizes list stays in config
        # order; this maps wall position to prize.
        self.shuffle_wall = bool(self.theme.get("shuffle_wall", False))
        self.apply_order(self.build_order())

        # The night's memory: which prizes are gone, which keys have been used.
        self.claimed = set()
        self.used_keys = set()
        self._load_state()

        self.state = IDLE
        self.state_since = 0.0
        self.now = 0.0
        self.spin_active = []
        self.spin_start_pos = 0
        self.spin_ticks = 0
        self.winner = None
        self.seen_at = {}            # uid -> when the reader last saw it

        self.armed_uid = None
        self.armed_name = ""
        self.speaking_until = 0.0
        self.armed_timeout = float(bcfg.get("armed_timeout_seconds", 45))

        self.clock = pygame.time.Clock()
        self.dt = 0.0
        self._drawn_state = None
        self._settled = False
        self._pop = 1.0            # eased scale-up when the highlight moves
        self._pop_index = None
        # Built together: a wireless unit is one ESP32 sending both the tags
        # and the button over a single link, and opening that link twice would
        # give two half-working connections.
        self.reader, self.button = reader_mod.make_sources(config,
                                                           simulate=simulate)
        # ENTER always works as a stand-in, so a dead switch on the night
        # cannot strand the wall with no way to start a spin.
        self.key_button = reader_mod.KeyboardButton()
        # In simulator mode the reader *is* the keypad. With a real reader
        # attached, --keys adds the same keyboard taps alongside it so the show
        # can be tested on the Pi without hunting for a tag.
        if simulate:
            self.keypad = self.reader
        elif allow_keys:
            self.keypad = reader_mod.KeyboardReader(config["rigged_tags"].keys())
        else:
            self.keypad = None
        self._start_idle_music()
        if not self.open_to_all():
            self.state = EXHAUSTED
        self._banner()

    def _set_mode(self):
        """Open the window, filling the screen whatever size the screen is.

        Passing (0, 0) with FULLSCREEN tells SDL to use the display's own
        current resolution, so the wall fills a 4:3 monitor, a 1080p TV or a
        small Pi touchscreen without the size in config.json having to match.
        Every measurement in the layout is derived from the surface, so the
        grid and the type resize themselves to fit.
        """
        if self.fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            if min(self.screen.get_size()) < 240:
                # Driver could not report a usable desktop size; fall back to
                # the size in config.json rather than run at something absurd.
                print("  ! fullscreen gave "
                      f"{self.screen.get_size()}, falling back to a window")
                self.fullscreen = False
                self.screen = pygame.display.set_mode(self.window_size,
                                                      pygame.RESIZABLE)
        else:
            self.screen = pygame.display.set_mode(self.window_size,
                                                  pygame.RESIZABLE)

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self._set_mode()
        self.on_resize()

    def on_resize(self):
        """Tile sizes changed, so everything cached at the old size is junk."""
        for prize in self.prizes:
            prize._cache.clear()
        self._fonts.clear()
        self._eye_cache.clear()
        self._field = None
        self._tile_cache.clear()
        self._layout_key = None
        self._overlay = None
        self._glow = None
        self._reveal_bg = None
        self._reveal_dim = None

    @staticmethod
    def rss_mb():
        try:
            import resource
            return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        except Exception:
            return 0.0

    def _banner(self):
        left = len(self.open_to_all())
        print("-" * 58)
        mode = "SIMULATOR (no reader)" if self.simulate else "PN532 reader"
        if self.keypad and not self.simulate:
            mode += " + keyboard"
        print(f"  {self.theme.get('house_name', 'BIG BROTHER')} memory wall — {mode}")
        if self.keypad:
            print("  SPACE = random key    1/2/3 = your rigged keys")
        else:
            print("  Tap a tag on the reader. (Keyboard taps: rerun with --keys.)")
        print(f"  {left} of {len(self.prizes)} prize(s) available to an ordinary key")
        if self.shuffle_wall:
            print(f"  Wall order: {' '.join(self.order_ids())}")
        if self.state == EXHAUSTED:
            print("  WALL IS EMPTY — taps will do nothing. Press R for a new night.")
        cues = sum(1 for s in (self.snd_start, self.snd_loop, self.snd_reveal,
                               self.snd_tick, self.snd_bell, self.snd_armed,
                               self.idle_music) if s)
        cues += sum(1 for p in self.prizes if p.sound)
        if not pygame.mixer.get_init():
            print("  AUDIO: no device — running silent")
        elif cues == 0:
            print("  AUDIO: no cues loaded. assets/sounds/ is empty or the "
                  "names in config.json do not match.")
            print("         Run:  python3 make_sounds.py")
        else:
            mix = pygame.mixer.get_init()
            print(f"  AUDIO: {cues} cue(s) loaded, mixer {mix[0]}Hz "
                  f"x{abs(mix[1])}bit x{mix[2]}ch")
        print("  ESC/Q quit    F fullscreen    R reset the night")
        print("-" * 58)

    def hidden_indices(self):
        """Prizes that only their own key can win."""
        rigged = set(self.cfg.get("rigged_tags", {}).values())
        return [i for i, p in enumerate(self.prizes) if p.id in rigged]

    def scattered(self, order):
        """True if no two rigged prizes share a row or a column."""
        cols = int(math.ceil(math.sqrt(len(self.prizes))))
        slots = [order.index(i) for i in self.hidden_indices()]
        rows = [s // cols for s in slots]
        columns = [s % cols for s in slots]
        return len(set(rows)) == len(rows) and len(set(columns)) == len(columns)

    def build_order(self):
        """A wall arrangement: position on the wall -> index into self.prizes.

        A plain shuffle is not good enough. It can just as easily drop all
        three rigged prizes into one row, which is the arrangement we were
        trying to get away from, so keep drawing until they are spread across
        different rows and columns.
        """
        order = list(range(len(self.prizes)))
        if not self.shuffle_wall:
            return order
        for _ in range(500):
            random.shuffle(order)
            if self.scattered(order):
                return order
        print("  ! could not find a scattered arrangement; using the last one")
        return order

    def apply_order(self, order):
        self.order = order
        self.slot = {prize: slot for slot, prize in enumerate(order)}

    def order_ids(self):
        return [self.prizes[i].id for i in self.order]

    @staticmethod
    def _resolve(name):
        if not name:
            return None
        return name if os.path.isabs(name) else os.path.join(HERE, name)

    # ------------------------------------------------------- night's memory

    def _load_state(self):
        """Survive a crash or a power blip mid-party without re-awarding."""
        if not (self.state_path and os.path.exists(self.state_path)):
            return
        with open(self.state_path) as fh:
            saved = json.load(fh)
        if self.once_per_night:
            self.claimed = {p for p in saved.get("claimed", []) if p in self.by_id}
            self.used_keys = set(saved.get("used_keys", []))

        # Keep the same arrangement across a restart, but only if it still
        # describes exactly the prizes now in the config.
        ids = saved.get("wall_order") or []
        if self.shuffle_wall and sorted(ids) == sorted(p.id for p in self.prizes):
            index = {p.id: i for i, p in enumerate(self.prizes)}
            self.apply_order([index[i] for i in ids])
        elif ids and self.shuffle_wall:
            print("  ! saved wall arrangement no longer matches the prizes; "
                  "reshuffling")
        if self.claimed:
            print(f"Resuming night: {len(self.claimed)} prize(s) already won "
                  f"({', '.join(sorted(self.claimed))}). Press R to reset.")

    def _save_state(self):
        if not self.state_path:
            return
        with open(self.state_path, "w") as fh:
            json.dump({"claimed": sorted(self.claimed),
                       "used_keys": sorted(self.used_keys),
                       "wall_order": self.order_ids()}, fh, indent=2)

    def reset_night(self):
        self.claimed.clear()
        self.used_keys.clear()
        if self.state_path and os.path.exists(self.state_path):
            os.remove(self.state_path)
        for prize in self.prizes:
            prize._cache.clear()
        self._tile_cache.clear()
        self.apply_order(self.build_order())
        self.winner = None
        self.state = IDLE
        self.state_since = self.now
        print("Night reset: every prize is back on the wall.")

    def is_evicted(self, prize):
        """Won and finished with, so its photo goes black and white.

        The prize currently being played for is not evicted yet, even though it
        is already claimed, so it stays in colour through its own reveal.
        """
        return prize.id in self.claimed and prize is not self.winner

    def available(self):
        """Prizes still on the wall."""
        return [p for p in self.prizes if p.id not in self.claimed]

    def reserved_ids(self):
        """Rigged prizes being held back for a key that has not been tapped yet.

        Without this, once the six ordinary prizes are gone the fallback would
        start handing the secret prizes to walk-up guests, and whoever holds
        that key would turn up to find their prize already given away.
        """
        if not self.reserve_rigged:
            return set()
        return {pid for uid, pid in self.cfg["rigged_tags"].items()
                if uid not in self.used_keys}

    def open_to_all(self):
        """What an ordinary, unknown key could still win."""
        reserved = self.reserved_ids()
        return [p for p in self.available() if p.id not in reserved]

    # ------------------------------------------------------------ selection

    def pick_random_prize(self):
        """Weighted pick from the random pool, ignoring prizes already won."""
        unreserved = self.open_to_all()
        pool = [p for p in unreserved if p.weight > 0]
        if not pool:
            # Random pool is exhausted; fall back to whatever is left that is
            # not being held for a rigged key, so the game keeps running.
            pool = unreserved
        if not pool:
            return None
        total = sum(p.weight for p in pool) or float(len(pool))
        roll = random.uniform(0, total)
        upto = 0.0
        for prize in pool:
            upto += prize.weight or 1.0
            if roll <= upto:
                return prize
        return pool[-1]

    def key_name(self, uid):
        """The name on this key, for the welcome screen."""
        return (self.cfg.get("key_names", {}).get(uid)
                or self.theme.get("default_guest", "HOUSEGUEST"))

    def poll_keys(self):
        """Drain the reader and return the keys that count as a fresh tap.

        This runs in every state, including during a spin and a reveal. That
        matters more than it looks: a guest puts their key down and leaves it
        there until they have their prize, so the reader can see the same key
        for twenty seconds solid. Polling only while idle meant the wall looked
        down the instant the reveal ended, found a key, and cheerfully gave the
        same guest a second turn.

        So a sighting only counts as a tap once the key has been away from the
        reader for `rescan_lockout_seconds`. A key resting on the pad is seen
        every poll and never qualifies; lift it off and the next guest can go.
        """
        fresh = []
        # Bounded, not `while True`. ThreadedReader hands back a queue that
        # empties, but a reader wired straight through reports the tag on
        # every call for as long as it is there, and an unbounded drain would
        # wedge the render loop solid.
        for _ in range(8):
            uid = self.reader.read()
            if not uid and self.keypad is not None and self.keypad is not self.reader:
                uid = self.keypad.read()
            if not uid:
                break
            if self.now - self.seen_at.get(uid, -1e9) >= self.lockout:
                fresh.append(uid)
            self.seen_at[uid] = self.now

        # Keys nobody has shown in a while are not worth remembering.
        if len(self.seen_at) > 64:
            cutoff = self.now - self.lockout * 10
            self.seen_at = {k: t for k, t in self.seen_at.items() if t > cutoff}
        return fresh

    def arm(self, uid):
        """A key has been tapped. Welcome them and wait for the button."""
        self.armed_uid = uid
        self.armed_name = self.key_name(uid)
        self.state = ARMED
        self.state_since = self.now
        self.button.clear()          # ignore any press from before the tap
        self.key_button.clear()
        # The announcer greets them by name and asks for the button, which is
        # the same thing the screen says. A guest with no clip of their own
        # gets the unnamed welcome, and failing that the plain armed tone.
        cue = self.opener_sound("welcome", self.armed_name) or self.snd_armed
        # The button is ignored until the greeting has finished, so a guest who
        # mashes it does not talk over the announcer. The press is not thrown
        # away though: it is simply not read yet, so it fires the instant she
        # stops speaking.
        self.speaking_until = self.now + (cue.get_length() if cue else 0.0)
        if cue:
            cue.play()
        print(f"{uid}: welcomed {self.armed_name}, waiting for the button")

    def button_pressed(self):
        return self.button.pressed() or self.key_button.pressed()

    def resolve(self, uid):
        """(prize, was_rigged) for this tap, and remember the key was used.

        A rigged key pays out its prize only on its first tap. After that, or
        if its prize has already gone, it falls through to the ordinary random
        draw like every other key.
        """
        rigged_id = self.cfg["rigged_tags"].get(uid)
        first_use = uid not in self.used_keys

        if rigged_id and first_use and rigged_id not in self.claimed:
            self.used_keys.add(uid)
            return self.by_id[rigged_id], True

        # Mark the key used only after picking, so this tap still sees its own
        # prize as reserved and cannot be handed it by the random draw.
        prize = self.pick_random_prize()
        self.used_keys.add(uid)
        return prize, False

    def log(self, uid, prize, rigged):
        line = (
            f"{datetime.datetime.now().isoformat(timespec='seconds')}\t"
            f"{uid}\t{prize.id}\t{prize.name}\t{'rigged' if rigged else 'random'}\t"
            f"{len(self.available())} left"
        )
        print(line)
        if self.log_path:
            with open(self.log_path, "a") as fh:
                fh.write(line + "\n")

    # ----------------------------------------------------------------- spin

    def begin_spin(self, uid):
        # Tiles the highlight can visit: everything still on the wall.
        active = [i for i, p in enumerate(self.prizes) if p.id not in self.claimed]
        prize, rigged = self.resolve(uid)
        if prize is None:
            print(f"{uid}: nothing left on the wall for an ordinary key. "
                  f"Press R (or restart with --reset) to start a new night.")
            self.state = EXHAUSTED
            self.state_since = self.now
            return

        self.winner = prize
        self.spin_active = active
        target = self.prizes.index(prize)

        # The highlight follows a shuffled route rather than marching along the
        # grid. Building the whole route up front — and simply making its last
        # stop the winner — means the landing is exact without the animation
        # having to be predictable.
        self.spin_ticks = self.min_ticks
        self.spin_sequence = self.build_route(active, target, self.spin_ticks)
        self.spin_step_shown = None

        # Claim at the moment the winner is decided, not at the reveal, so a
        # crash during the spin can never hand the same prize out twice.
        if self.once_per_night:
            self.claimed.add(prize.id)
        self._save_state()

        self.state = SPINNING
        self.state_since = self.now
        if self.keypad:
            self.keypad.discard_pending()
        if self.idle_music:
            pygame.mixer.music.fadeout(400)
        if self.snd_start:
            self.snd_start.play()
        if self.snd_loop:
            self.snd_loop.play(loops=-1, fade_ms=200)
        self.log(uid, prize, rigged)

    @staticmethod
    def build_route(active, target, ticks):
        """A shuffled tour of `active` of length ticks+1, ending on `target`.

        Laid out as back-to-back shuffles of the available tiles, so every
        prize still gets visited about equally often — it just never goes
        round in grid order. Consecutive stops are never the same tile, so
        each step is a visible jump.
        """
        route, last = [], None
        while len(route) <= ticks:
            block = active[:]
            random.shuffle(block)
            if len(block) > 1 and block[0] == last:
                block[0], block[-1] = block[-1], block[0]
            route.extend(block)
            last = block[-1]
        route = route[:ticks + 1]
        route[ticks] = target
        if ticks > 0 and route[ticks - 1] == target and len(active) > 1:
            # Guarantee a final jump onto the winner rather than a dead stop.
            route[ticks - 1] = next(i for i in active if i != target)
        return route

    def spin_step(self):
        """Which stop on the route the shuffle has reached."""
        p = min(1.0, (self.now - self.state_since) / self.spin_seconds)
        return min(self.spin_ticks, int(spin_curve(p) * self.spin_ticks))

    def highlight_index(self):
        """Index into self.prizes of the tile lit right now."""
        return self.spin_sequence[self.spin_step()]

    def spin_audio(self):
        """One beep per jump, so the beeping slows down with the shuffle."""
        step = self.spin_step()
        if step != self.spin_step_shown:
            self.spin_step_shown = step
            if self.snd_tick:
                self.snd_tick.play()

    def finish_spin(self):
        """The shuffle stops. Hold on the winner, lit, with the bell.

        Going straight into the zoom made the win read as the prize changing
        after the spin rather than the spin choosing it. This beat lets the
        highlight settle where it landed before anything moves.
        """
        self._reveal_bg = self._reveal_dim = None   # wall changed; recompose
        if self.winner is None:      # nothing was ever decided; do not reveal
            self.back_to_idle()
            return
        self.state = LANDED
        self.state_since = self.now
        if self.snd_loop:
            self.snd_loop.fadeout(200)
        if self.snd_bell:
            self.snd_bell.play()

    @staticmethod
    def name_slug(name):
        """`Alex` -> `autumn`, for finding that guest's opener clip."""
        return re.sub(r"[^a-z0-9]+", "", (name or "").lower()) or "guest"

    def opener_sound(self, family, name):
        """The announcer saying this guest's half of the line.

        A family may have several recordings of the same thing: the welcomes
        are three different greetings per guest, and one is chosen at random so
        a guest who taps twice does not hear the same words twice. Openers that
        only have one recording, like the congratulations, simply always pick
        that one.

        Falls back to the unnamed recordings of the same family, so a key that
        is not on the guest list is still greeted, and to nothing at all if
        there is no recording either way.
        """
        key = (family, self.name_slug(name))
        if key not in self._openers:
            found = []
            for stem in ("%s_%s" % key, family):
                found = [s for s in
                         (Prize._load_sound("%s_%d.wav" % (stem, n), quiet=True)
                          for n in range(1, 10)) if s is not None]
                if not found:
                    one = Prize._load_sound("%s.wav" % stem, quiet=True)
                    found = [one] if one is not None else []
                if found:
                    break
            self._openers[key] = found
        choices = self._openers[key]
        return random.choice(choices) if choices else None

    def begin_reveal(self):
        """Show the winner, and play its cue.

        The cue is in two halves: the announcer says the guest's name, then the
        prize line. Keeping them apart means one clip per guest and one per
        prize rather than one for every combination of the two, and a new guest
        costs a single recording. They are played on one channel with `queue`,
        which starts the second the instant the first ends, so it is heard as
        one sentence rather than two clips.

        A prize with its own line replaces `spin.reveal_sound` rather than
        layering over it: two cues at the same instant only muddy each other.
        """
        self.state = REVEAL
        self.state_since = self.now

        cue = self.winner.sound or self.snd_reveal
        opener = None
        # A prize whose opener is "none" says its own whole line, in its own
        # voice. Zingbot introduces himself; the announcer saying
        # "Congratulations Alex," first would be two people talking over
        # each other.
        if self.winner.sound and self.winner.opener != "none":
            opener = self.opener_sound(self.winner.opener, self.armed_name)

        # However long the winner is on screen, it is at least long enough to
        # finish speaking. Measured from the clips themselves rather than
        # configured, so writing a longer line is all it takes: nothing else
        # has to be adjusted to match.
        spoken = 0.0
        if opener is not None and cue is not None:
            channel = opener.play()
            if channel is not None:
                channel.queue(cue)
                spoken = opener.get_length() + cue.get_length()
            else:                      # no free channel: at least say the prize
                cue.play()
                spoken = cue.get_length()
        elif cue is not None:
            cue.play()
            spoken = cue.get_length()

        self.reveal_until = self.now + max(self.reveal_hold,
                                           spoken + self.reveal_tail)

    def back_to_idle(self):
        self.armed_uid = None
        # Dropping the winner here is what turns its photo black and white.
        self._reveal_bg = self._reveal_dim = None
        # Only the prize just won changes how it looks, so only its art is
        # thrown away. Clearing the whole cache here meant rebuilding all nine
        # frames on the next spin, which showed up as a stutter.
        if self.winner is not None:
            self._forget_tiles(self.winner.id)
        self.winner = None
        self.state = IDLE if self.open_to_all() else EXHAUSTED
        self.state_since = self.now
        if self.idle_music and self.state == IDLE:
            pygame.mixer.music.play(-1)

    # -------------------------------------------------------------- drawing

    @staticmethod
    def _find_font(name):
        """Resolve display.font, which may be a bare filename in assets/fonts."""
        if not name:
            return None
        for candidate in (name, os.path.join(FONT_DIR, name)):
            if os.path.exists(candidate):
                return candidate
        print(f"  ! font not found: {name} (falling back to the system font)")
        return None

    def font(self, px, bold=False):
        key = (px, bold)
        if key not in self._fonts:
            if self._font_path:
                self._fonts[key] = pygame.font.Font(self._font_path, px)
            else:
                self._fonts[key] = pygame.font.SysFont("dejavusans", px, bold=bold)
        return self._fonts[key]

    def fitted(self, text, px, max_width, colour, bold=True):
        """Render `text`, shrinking the type until it fits `max_width`."""
        while px > 8:
            label = self.font(px, bold=bold).render(text, True, colour)
            if label.get_width() <= max_width:
                return label
            px -= 2
        return label

    def _start_idle_music(self):
        if not self.idle_music:
            return
        path = self.idle_music
        if not os.path.isabs(path):
            path = os.path.join(SOUND_DIR, path)
        if os.path.exists(path):
            pygame.mixer.music.load(path)
            pygame.mixer.music.play(-1)
        else:
            print(f"  ! missing idle music: {path}")

    def eye_surface(self, w):
        """The eye, drawn once onto its own small surface and kept.

        This used to allocate and blit a full-screen RGBA surface on every
        frame — twice per frame during the reveal — which is most of a frame's
        budget on a Pi. Only a couple of sizes are ever needed, so build each
        one once and blit it.
        """
        key = int(w)
        surf = self._eye_cache.get(key)
        if surf is not None:
            return surf

        h = w * 0.58
        iris = int(h * 0.92)
        size = int(w * 2.2) | 1          # odd, so there is an exact centre
        cx = cy = size // 2
        steps = 44
        upper = [(cx - w + 2 * w * (i / steps),
                  cy - h * math.sin(math.pi * (i / steps))) for i in range(steps + 1)]
        lower = [(cx - w + 2 * w * (i / steps),
                  cy + h * math.sin(math.pi * (i / steps))) for i in range(steps, -1, -1)]
        lens = [(int(x), int(y)) for x, y in upper + lower]

        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        for r, alpha in ((iris * 2.0, 26), (iris * 1.5, 42), (iris * 1.15, 70)):
            pygame.draw.circle(surf, (*self.accent, alpha), (cx, cy), int(r))
        pygame.draw.polygon(surf, (2, 10, 20, 235), lens)
        pygame.draw.circle(surf, (*self.accent, 255), (cx, cy), iris)
        pygame.draw.circle(surf, (4, 20, 34, 255), (cx, cy), int(iris * 0.62))
        pygame.draw.circle(surf, (0, 0, 0, 255), (cx, cy), int(iris * 0.3))
        pygame.draw.circle(surf, (255, 255, 255, 210),
                           (int(cx - iris * 0.3), int(cy - iris * 0.34)),
                           max(2, int(iris * 0.15)))
        pygame.draw.lines(surf, (*self.accent, 255), True, lens, max(2, int(w * 0.035)))

        if len(self._eye_cache) > 4:
            self._eye_cache.clear()
        self._eye_cache[key] = surf
        return surf

    def draw_eye(self, cx, cy, w, glow=1.0):
        """Blit the cached eye. `glow` fades the whole thing, no redraw."""
        surf = self.eye_surface(w)
        surf.set_alpha(255 if glow >= 1.0 else max(0, int(255 * glow)))
        self.screen.blit(surf, surf.get_rect(center=(int(cx), int(cy))))

    def draw_header(self, caption):
        w, h = self.screen.get_size()
        bar = int(h * 0.135)
        pygame.draw.rect(self.screen, (7, 24, 40), (0, 0, w, bar))
        pygame.draw.line(self.screen, self.accent, (0, bar), (w, bar), 2)
        if self.theme.get("show_eye", True):
            self.draw_eye(int(bar * 0.72), bar // 2, int(bar * 0.36))
        room = int(w * (0.55 if self.keypad else 0.78))
        label = self.fitted(caption, int(bar * 0.42), room, self.accent)
        self.screen.blit(label, label.get_rect(midleft=(int(bar * 1.28), bar // 2)))
        return bar

    def background_field(self):
        """The animated background, built to fit the current screen.

        Rebuilt only when the screen size changes.  The Time Trip field is also
        handed the wall's own frame rectangles, so its glowing alcoves line up
        with the pictures instead of with a grid it guessed for itself.
        """
        size = self.screen.get_size()
        if self._field is None or self._field.size != size:
            if self.bg_style == "pool":
                self._field = vis.WaveField(
                    size, self.bg, self.accent,
                    detail=self._wave_detail, quality=self.wave_quality,
                    speed=self.wave_speed, chroma=self.wave_chroma)
            else:
                self._field = vis.TimeTripField(
                    size, self.bg, self.accent,
                    panels=[frame for frame, _cap in self.layout()],
                    speed=self.wave_speed, streaks=self.warp_streaks,
                    seam=self.warp_seam)
        return self._field

    def draw_background(self):
        if not self.waves:
            self.screen.fill(self.bg)
            return
        self.background_field().draw(self.screen, self.now)

    def layout(self):
        """Frame rects and caption rects, one pair per prize.

        Frames are a fixed 4:3 and centred in their cell, so the wall keeps the
        proportions of the real memory wall whatever shape the screen is; the
        leftover space becomes the gap between them. Cached per screen size.
        """
        size = self.screen.get_size()
        if self._layout_key == size:
            return self._layout

        w, h = size
        n = len(self.prizes)
        cols = int(math.ceil(math.sqrt(n)))
        rows = int(math.ceil(n / cols))
        top = int(h * 0.135)
        bottom = int(h * 0.09)
        margin = int(min(w, h) * 0.03)
        gap = int(min(w, h) * 0.035)
        cap_ratio = 0.20 if self.captions else 0.0

        # Size the frame from the height available to a row, then hold it to
        # 4:3. Width only takes over if the screen is too narrow for that.
        avail_h = h - top - bottom - 2 * margin - gap * (rows - 1)
        avail_w = w - 2 * margin - gap * (cols - 1)
        frame_h = (avail_h / rows) / (1 + cap_ratio)
        frame_w = frame_h * 4 / 3
        if frame_w > avail_w / cols:
            frame_w = avail_w / cols
            frame_h = frame_w * 3 / 4
        cap_h = int(frame_h * cap_ratio)
        block_h = frame_h + cap_h

        # Spread the columns into the leftover width, but never so far apart
        # that the wall stops reading as one group.
        hgap = max(gap, min(frame_w * 0.45,
                            (avail_w - cols * frame_w) / max(1, cols - 1)))
        grid_w = cols * frame_w + hgap * (cols - 1)
        grid_h = rows * block_h + gap * (rows - 1)
        left = (w - grid_w) / 2
        first = top + (h - bottom - top - grid_h) / 2

        out = []
        for i in range(n):
            x = left + (i % cols) * (frame_w + hgap)
            y = first + (i // cols) * (block_h + gap)
            frame = pygame.Rect(int(x), int(y), int(frame_w), int(frame_h))
            caption = pygame.Rect(frame.x, frame.bottom, frame.width, cap_h)
            out.append((frame, caption))

        self._layout_key, self._layout = size, out
        return out

    def tile_rect(self, slot):
        return self.layout()[slot][0]

    def prize_rect(self, index):
        """The frame a given prize is hanging in."""
        return self.layout()[self.slot[index]][0]

    def draw_tile(self, prize, rect, lit, evicted=False, dim=True, scale=1.0):
        """Blit a framed picture, built the first time it is needed."""
        pad = self.shadow_pad(rect)
        tile = self.tile_surface((rect.width, rect.height), prize, lit, evicted, dim)
        if scale == 1.0:
            self.screen.blit(tile, (rect.x - pad, rect.y - pad))
            return
        width = int(tile.get_width() * scale)
        height = int(tile.get_height() * scale)
        grown = pygame.transform.scale(tile, (width, height))
        self.screen.blit(grown, (rect.centerx - width // 2,
                                 rect.centery - height // 2))

    def _forget_tiles(self, prize_id):
        """Drop just one prize's cached art, leaving the rest of the wall built."""
        for key in [k for k in self._tile_cache if k[0] == prize_id]:
            del self._tile_cache[key]

    def tile_surface(self, size, prize, lit, evicted, dim):
        key = (prize.id, size, lit, evicted, dim)
        tile = self._tile_cache.get(key)
        if tile is None:
            if len(self._tile_cache) > 160:
                self._tile_cache.clear()
            tile = self._build_tile(size, prize, lit, evicted, dim)
            self._tile_cache[key] = tile
        return tile

    @staticmethod
    def shadow_pad(rect):
        return max(4, int(min(rect.width, rect.height) * 0.10))

    @staticmethod
    def _supersample(size, prize):
        """How far to oversample a tile of this size before scaling it down.

        Oversampling is what keeps the moulding's edges and corner radius
        smooth, and it costs nothing per frame because the tile is cached. It
        does cost memory, though, and asking for more pixels than the source
        photograph actually has buys nothing at all: a wall tile is a fifth of
        the artwork's height, so tripling it still samples real detail, while
        the reveal hero is already two thirds of it, and tripling that was
        cacheing a 2592x1944 upscale of a 1448x1086 picture. So the factor is
        capped by what the photograph can supply.
        """
        if prize.image is None:
            return vis.SUPERSAMPLE
        source_h = prize.image.get_height()
        return max(1, min(vis.SUPERSAMPLE, source_h // max(1, size[1])))

    def _build_tile(self, size, prize, lit, evicted, dim):
        """One framed picture, drawn once, with a soft shadow behind it.

        Everything here is drawn oversampled and scaled down, so the frame
        edges and the corner radius come out smooth instead of stepped. It
        costs nothing per frame because the result is cached.
        """
        pad = self.shadow_pad(pygame.Rect(0, 0, *size))
        canvas = pygame.Surface((size[0] + pad * 2, size[1] + pad * 2),
                                pygame.SRCALPHA)
        inner = pygame.Rect(pad, pad, size[0], size[1])

        depth = 0 if evicted else (pad * 0.75 if lit else pad * 0.45)
        shadow = vis.soft_shadow(size, radius=int(size[1] * 0.06),
                                 spread=int(pad * 0.9),
                                 alpha=150 if lit else 110)
        canvas.blit(shadow, (inner.x - int(pad * 0.9),
                             inner.y - int(pad * 0.9) + int(depth)))

        base = (self.accent if lit else
                vis.shade(self.frame_colour, 0.45) if evicted else
                self.frame_colour)

        def paint(big, factor):
            box = pygame.Rect(pad * factor, pad * factor,
                              size[0] * factor, size[1] * factor)
            picture = vis.render_frame(big, box, base, lit, factor)
            self._paint_picture(big, picture, prize, lit, evicted, dim, factor)

        art = vis.supersampled((canvas.get_width(), canvas.get_height()), paint,
                               factor=self._supersample(size, prize))
        canvas.blit(art, (0, 0))
        return canvas

    def _paint_picture(self, surf, rect, prize, lit, evicted, dim, factor):
        """The photo itself, inside the moulding."""
        shade_level = 110 if evicted else 150 if (dim and not lit) else None
        photo = prize.photo((rect.width, rect.height), evicted=evicted,
                            shade=shade_level)
        if photo is not None:
            # A PNG with transparency composites onto this rather than onto
            # whatever happens to be under the tile, so a cut-out prize photo
            # sits on a deliberate backing instead of the drop shadow.
            backing = vis.shade(self.bg, 1.25 if lit else 0.9)
            surf.blit(vis.vertical_gradient(
                rect.size, vis.mix(backing, (255, 255, 255), 0.10),
                vis.shade(backing, 0.7)), rect.topleft)
            surf.blit(photo, rect.topleft)
        else:
            base = prize.colour
            if evicted:
                grey = sum(base) // 3
                base = (grey, grey, grey)
            if evicted or (dim and not lit):
                base = vis.shade(base, 0.4 if evicted else 0.62)
            top = vis.mix(base, (255, 255, 255), 0.12)
            surf.blit(vis.vertical_gradient(rect.size, top,
                                            vis.shade(base, 0.78)), rect.topleft)

        # Glass. A little light across the top of the picture sells the depth.
        if not evicted:
            surf.blit(vis.sheen((rect.width, int(rect.height * 0.55)),
                                strength=30 if lit else 18), rect.topleft)

        if evicted:
            pygame.draw.line(surf, (104, 26, 26), rect.topleft, rect.bottomright,
                             max(1, 2 * factor))
            pygame.draw.line(surf, (104, 26, 26), rect.topright, rect.bottomleft,
                             max(1, 2 * factor))

    def draw_caption(self, prize, rect, lit, evicted):
        """The prize name, on the wall under its frame — the photo stays clean."""
        if not self.captions or rect.height <= 0:
            return
        colour = ((104, 116, 126) if evicted else
                  (255, 255, 255) if lit else (176, 198, 214))
        label = self.fitted(prize.name.upper(), int(rect.height * 0.70),
                            rect.width, colour, bold=True)
        self.screen.blit(label, label.get_rect(center=rect.center))

    def draw_grid(self, lit_index, dim=None):
        # Only knock the wall back while something is lit, so the idle wall
        # stays bright and every prize photo is on show.
        if dim is None:
            dim = lit_index >= 0

        # When the highlight jumps, the new frame swells and settles rather
        # than snapping on. Driven by dt, so the weight of it is the same
        # whether the wall is running at 60fps or struggling at 20.
        if lit_index != self._pop_index:
            self._pop_index, self._pop = lit_index, 0.0
        elif self._pop < 1.0:
            self._pop = min(1.0, self._pop + self.dt / 0.16)
        grow = 1.0 + 0.055 * (1.0 - vis.ease_out_cubic(self._pop))

        for slot, index in enumerate(self.order):
            prize = self.prizes[index]
            frame, caption = self.layout()[slot]
            evicted = self.is_evicted(prize)
            lit = index == lit_index
            self.draw_tile(prize, frame, lit, evicted=evicted, dim=dim,
                           scale=grow if lit else 1.0)
            self.draw_caption(prize, caption, lit, evicted)

    def draw_footer(self, text, colour=None, pulse=False):
        w, h = self.screen.get_size()
        if pulse:
            # Breathe rather than blink: an eased sine between 45% and full,
            # so the prompt never drops out of sight at the bottom of the swing.
            k = 0.45 + 0.55 * vis.ease_in_out_sine(
                (math.sin(self.now * 1.9) + 1.0) / 2.0)
            colour = tuple(int(c * k) for c in (colour or self.accent))
        label = self.fitted(text, int(h * 0.062), int(w * 0.9),
                            colour or self.accent)
        self.screen.blit(label, label.get_rect(center=(w // 2, int(h * 0.945))))

    def draw_idle(self):
        self.draw_header(self.theme.get("house_name", "BIG BROTHER"))
        self.draw_grid(-1)
        self.draw_footer(self.theme.get("idle_line", "PLACE YOUR KEY"), pulse=True)
        self.draw_sim_hint()

    def draw_sim_hint(self):
        """Small note in the header bar so the input mode is always visible."""
        if self.keypad is None:
            return
        w, h = self.screen.get_size()
        bar = int(h * 0.135)
        prefix = "SIMULATOR" if self.simulate else "KEYBOARD"
        label = self.fitted(f"{prefix}  -  SPACE RANDOM  -  1/2/3 RIGGED",
                            max(10, int(h * 0.021)), int(w * 0.34),
                            (110, 145, 172), bold=False)
        self.screen.blit(label, label.get_rect(
            midright=(w - int(w * 0.015), bar // 2)))

    def draw_armed(self):
        """Welcome the guest by name and tell them to push the button."""
        w, h = self.screen.get_size()
        self.draw_header(self.theme.get("house_name", "BIG BROTHER"))
        self.draw_grid(-1, dim=True)

        welcome = self.theme.get("welcome_line",
                                 "WELCOME TO THE PRIZE SHACK, {name}")
        welcome = welcome.replace("{name}", self.armed_name.upper())
        prompt = self.theme.get("button_line", "PUSH THE BUTTON TO BEGIN!")

        top = self.fitted(welcome, int(h * 0.062), int(w * 0.78), self.accent)
        # While the announcer is still speaking the button does nothing, so the
        # prompt holds back rather than inviting a press that will be ignored.
        # It fades up as she finishes instead of appearing all at once.
        speaking = max(0.0, self.speaking_until - self.now)
        ready = vis.clamp01(1.0 - speaking / 0.45)
        pulse = 0.55 + 0.45 * vis.ease_in_out_sine(
            (math.sin(self.now * 2.6) + 1.0) / 2.0)
        bottom = self.fitted(prompt, int(h * 0.055), int(w * 0.78),
                             tuple(int(c * pulse * ready) for c in (255, 255, 255)))

        pad = int(h * 0.038)
        panel = pygame.Rect(0, 0,
                            max(top.get_width(), bottom.get_width()) + pad * 2,
                            top.get_height() + bottom.get_height() + pad * 2)
        panel.center = (w // 2, h // 2)
        veil = pygame.Surface(panel.size)
        veil.fill((3, 12, 22))
        veil.set_alpha(232)
        self.screen.blit(veil, panel.topleft)
        pygame.draw.rect(self.screen, self.accent, panel, width=3)

        self.screen.blit(top, top.get_rect(
            midtop=(w // 2, panel.top + pad)))
        self.screen.blit(bottom, bottom.get_rect(
            midbottom=(w // 2, panel.bottom - pad)))

    def draw_landed(self):
        """The winner sitting lit in its frame, exactly as the shuffle left it."""
        self.draw_header(self.theme.get("house_name", "BIG BROTHER"))
        self.draw_grid(self.prizes.index(self.winner))
        self.draw_footer(self.theme.get("reveal_line", "BIG BROTHER HAS SPOKEN"))

    def draw_spinning(self):
        self.draw_header(self.theme.get("house_name", "BIG BROTHER"))
        self.draw_grid(self.highlight_index())
        dots = "." * (int(self.now * 3) % 4)
        self.draw_footer(self.theme.get("spin_line", "BIG BROTHER IS DECIDING") + dots)

    def draw_exhausted(self):
        self.draw_header(self.theme.get("house_name", "BIG BROTHER"))
        self.draw_grid(-1)
        self.draw_footer(
            self.theme.get("exhausted_line", "THE HOUSE IS EMPTY") + "  -  PRESS R",
            colour=(220, 90, 90))
        self.draw_sim_hint()

    def draw_reveal(self):
        w, h = self.screen.get_size()
        p = ease_out(max(0.0, min(1.0, (self.now - self.state_since) / 0.5)))

        small = self.prize_rect(self.prizes.index(self.winner))
        # 4:3, matching the frames on the wall and the artwork itself, so the
        # reveal shows the whole card instead of cropping its top and bottom.
        hero_h = h * 0.60
        hero_w = hero_h * 4 / 3
        if hero_w > w * 0.72:
            hero_w = w * 0.72
            hero_h = hero_w * 3 / 4
        big = pygame.Rect(0, 0, int(hero_w), int(hero_h))
        big.center = (w // 2, int(h * 0.47))
        rect = pygame.Rect(
            int(small.x + (big.x - small.x) * p),
            int(small.y + (big.y - small.y) * p),
            int(small.width + (big.width - small.width) * p),
            int(small.height + (big.height - small.height) * p),
        )

        if self._reveal_bg is None or self._reveal_bg.get_size() != (w, h):
            self.draw_grid(-1)
            self._reveal_bg = self.screen.copy()
            # The zoom lasts half a second; the dimmed wall then holds for the
            # rest of the reveal. Compose that steady state once so almost
            # every reveal frame is a single opaque blit with no alpha blending.
            self._reveal_dim = self._reveal_bg.copy()
            veil = pygame.Surface((w, h))
            veil.fill((2, 8, 16))
            veil.set_alpha(232)
            self._reveal_dim.blit(veil, (0, 0))

        if p >= 1.0:
            self.screen.blit(self._reveal_dim, (0, 0))
        else:
            self.screen.blit(self._reveal_bg, (0, 0))
            if self._overlay is None or self._overlay.get_size() != (w, h):
                self._overlay = pygame.Surface((w, h))
            self._overlay.fill((2, 8, 16))
            self._overlay.set_alpha(int(232 * p))
            self.screen.blit(self._overlay, (0, 0))
        self.draw_header(self.theme.get("reveal_line", "BIG BROTHER HAS SPOKEN"))

        if self.theme.get("show_eye", True):
            self.draw_eye(w // 2, int(h * 0.48), int(w * 0.3), glow=0.16 * p)

        # Only once the zoom has settled, so this is a fixed size and can be
        # built once instead of every frame of the animation.
        if p >= 1.0:
            if self._glow is None or self._glow.get_size() != (big.width + 40,
                                                              big.height + 40):
                self._glow = pygame.Surface((big.width + 40, big.height + 40),
                                            pygame.SRCALPHA)
                pygame.draw.rect(self._glow, (*self.accent, 60),
                                 self._glow.get_rect(), border_radius=8)
            self.screen.blit(self._glow, (rect.x - 20, rect.y - 20))
        # Built once at the size it is growing towards, then scaled while it
        # travels. At rest it lands on its own pixels, so it stays crisp.
        pad = self.shadow_pad(big)
        hero = self.tile_surface((big.width, big.height), self.winner,
                                 True, False, False)
        if rect.size == big.size:
            self.screen.blit(hero, (rect.x - pad, rect.y - pad))
        else:
            scaled = pygame.transform.scale(
                hero, (rect.width + pad * 2, rect.height + pad * 2))
            self.screen.blit(scaled, (rect.x - pad, rect.y - pad))
        self.draw_footer(self.winner.name.upper(),
                         colour=tuple(int(c * p) for c in (255, 255, 255)))

    def is_static(self):
        """True when the screen cannot change, so the frame can be skipped.

        The reveal settles after its half second zoom: the wall behind it is a
        frozen composite, the winner has stopped growing and the caption has
        finished fading. Redrawing that six hundred times is heat and nothing
        else.
        """
        return (self.state == REVEAL
                and (self.now - self.state_since) > 0.62)

    def draw(self):
        self.draw_background()
        if self.state == IDLE:
            self.draw_idle()
        elif self.state == ARMED:
            self.draw_armed()
        elif self.state == SPINNING:
            self.draw_spinning()
        elif self.state == LANDED:
            self.draw_landed()
        elif self.state == EXHAUSTED:
            self.draw_exhausted()
        else:
            self.draw_reveal()
        pygame.display.flip()

    # ----------------------------------------------------------------- loop

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.VIDEORESIZE and not self.fullscreen:
                self.window_size = (event.w, event.h)
                self.on_resize()
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    return False
                if event.key == pygame.K_f:
                    self.toggle_fullscreen()
                if event.key == pygame.K_r and self.state in (IDLE, EXHAUSTED):
                    self.reset_night()
                else:
                    if self.state == ARMED:
                        self.key_button.feed(event.key)
                    if self.keypad and self.state in (IDLE, ARMED, EXHAUSTED):
                        self.keypad.feed(event.key)
        return True

    def run(self):
        running = True
        frames, slowest, since, state_at_start = 0, 0.0, 0.0, self.state
        while running:
            previous = self.now
            self.now = pygame.time.get_ticks() / 1000.0
            # Every animation below is driven by elapsed time rather than by
            # frame count, so a stalled frame changes nothing about where
            # things end up. dt drives the highlight pop and nothing else.
            self.dt = min(0.1, max(0.0, self.now - previous))

            # Always drain the reader, whatever the state. See poll_keys: a
            # key rests on the pad for the whole turn, and the wall has to keep
            # watching it to know when it finally leaves.
            fresh = self.poll_keys()

            if self.state in (IDLE, ARMED, EXHAUSTED):
                if fresh:
                    self.arm(fresh[0])     # a new key can step in at any time

                if self.state == ARMED:
                    if self.now < self.speaking_until:
                        pass           # let the greeting finish
                    elif self.button_pressed():
                        self.begin_spin(self.armed_uid)
                    elif self.now - self.state_since >= self.armed_timeout:
                        print(f"{self.armed_uid}: no button press, back to idle")
                        self.back_to_idle()
                else:
                    self.button_pressed()   # drain, so a stray press cannot queue
            elif self.state == SPINNING:
                if self.now - self.state_since >= self.spin_seconds:
                    self.finish_spin()
                else:
                    self.spin_audio()
            elif self.state == LANDED:
                if self.now - self.state_since >= self.land_pause:
                    self.begin_reveal()
            elif self.now >= self.reveal_until:
                self.back_to_idle()

            running = self.handle_events()
            if self.state != self._drawn_state:
                self._drawn_state = self.state
                self._settled = False
            if self.is_static() and self._settled:
                self.clock.tick(self.max_fps)
                continue
            t0 = time.perf_counter()
            self.draw()
            if self.is_static():
                self._settled = True
            if self.show_fps:
                ms = (time.perf_counter() - t0) * 1000
                frames += 1
                slowest = max(slowest, ms)
                # A single very slow frame is what a freeze actually looks
                # like, so call it out the moment it happens.
                if ms > 250:
                    print(f"  SLOW FRAME {ms:7.1f} ms during {self.state}",
                          flush=True)
                if self.now - since >= 2.0:
                    fps = frames / max(0.001, self.now - since)
                    print(f"  [{self.now:7.1f}s] {self.state:<9} "
                          f"{fps:5.1f} fps  worst {slowest:6.1f} ms  "
                          f"rss {self.rss_mb():5.0f} MB", flush=True)
                    frames, slowest, since = 0, 0.0, self.now
                    state_at_start = self.state
            self.clock.tick(self.max_fps)

        self.reader.close()
        self.button.close()
        pygame.quit()


def test_audio(wall):
    """Play every configured cue in turn, so you can hear what is wired up."""
    import time

    if not pygame.mixer.get_init():
        print("No audio device. Nothing to test.")
        return
    mix = pygame.mixer.get_init()
    print(f"Mixer: {mix[0]}Hz x{abs(mix[1])}bit x{mix[2]}ch. "
          f"Playing each cue — you should hear all of them.\n")

    cues = [("spin.armed_sound", wall.snd_armed),
            ("spin.start_sound", wall.snd_start),
            ("spin.tick_sound", wall.snd_tick),
            ("spin.loop_sound", wall.snd_loop),
            ("spin.bell_sound", wall.snd_bell),
            ("spin.reveal_sound", wall.snd_reveal)]

    # Every guest's spoken clips. These are loaded on demand while the show is
    # running, so nothing lists them: they have to be gathered the same way the
    # wall gathers them, by asking for each guest by name.
    guests = sorted(set(wall.cfg.get("key_names", {}).values()))
    guests.append(wall.cfg.get("theme", {}).get("default_guest", "HOUSEGUEST"))
    for guest in guests:
        for family in ("welcome", "congrats", "badnews"):
            clips = wall._openers.get((family, wall.name_slug(guest)))
            if clips is None:
                wall.opener_sound(family, guest)      # populates the cache
                clips = wall._openers.get((family, wall.name_slug(guest)), [])
            for i, snd in enumerate(clips, start=1):
                tag = f" {i}" if len(clips) > 1 else ""
                cues.append((f"{family} '{guest}'{tag}", snd))

    cues += [(f"prize '{p.name}'", p.sound) for p in wall.prizes if p.sound]

    played = 0
    for label, snd in cues:
        if snd is None:
            print(f"  -- {label}: not set or file missing")
            continue
        print(f"  >> {label}: {snd.get_length():.2f}s", flush=True)
        chan = snd.play()
        played += 1
        # The whole clip, not the first two seconds of it. The spoken lines run
        # to seven seconds and cutting them off defeats the point of listening.
        time.sleep(snd.get_length() + 0.25)
        snd.stop()
        if chan is None:
            print("     ! no free channel — nothing was played")

    if wall.idle_music:
        path = wall.idle_music
        if not os.path.isabs(path):
            path = os.path.join(SOUND_DIR, path)
        if os.path.exists(path):
            print(f"  >> spin.idle_music: {os.path.basename(path)}")
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            time.sleep(3.0)
            pygame.mixer.music.stop()
            played += 1
        else:
            print(f"  -- spin.idle_music: missing {path}")

    print(f"\n{played} cue(s) played. If you heard nothing, the problem is the "
          f"audio output, not the app.")
    if sys.platform.startswith("linux"):
        print("On a Pi, force the output with:  sudo raspi-config  "
              "-> System Options -> Audio")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=os.path.join(HERE, "config.json"))
    ap.add_argument("--simulate", action="store_true",
                    help="no PN532: SPACE = random tag, 1-9 = rigged tags")
    ap.add_argument("--windowed", action="store_true",
                    help="force a window even if config.json asks for fullscreen")
    ap.add_argument("--keys", action="store_true",
                    help="also accept keyboard taps while a real reader is connected")
    ap.add_argument("--fps", action="store_true",
                    help="print real frame rates per state, to find slow hardware")
    ap.add_argument("--test-audio", action="store_true",
                    help="play every configured sound cue, then exit")
    ap.add_argument("--reset", action="store_true",
                    help="clear the night's won-prize state before starting")
    args = ap.parse_args()

    with open(args.config) as fh:
        config = json.load(fh)

    wall = PrizeWall(config, simulate=args.simulate or args.test_audio,
                     allow_keys=args.keys,
                     windowed=args.windowed or args.test_audio,
                     show_fps=args.fps)
    if args.test_audio:
        test_audio(wall)
        return
    if args.reset:
        wall.reset_night()
    wall.run()


if __name__ == "__main__":
    sys.exit(main())
