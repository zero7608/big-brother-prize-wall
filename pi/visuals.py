"""Rendering helpers for the memory wall.

Three ideas carry all of this on a Raspberry Pi:

1. Anything that does not change every frame is drawn once into a surface and
   blitted thereafter.  A blit is a memory copy; a rounded rect with a bevel,
   a shadow and a sheen is a few dozen draw calls.
2. Because that art is built once, it can be drawn at several times the final
   size and scaled down with `smoothscale`.  That is real anti-aliasing on
   every edge for no per-frame cost, and it is what stops the wall looking
   blocky.
3. The water is two pre-rendered caustic layers scrolling in opposite
   directions over a baked gradient.  The interference between them reads as
   moving water, the offsets come from elapsed time so it is frame-rate
   independent, and drawing it costs three blits and a fill rather than a
   per-pixel simulation.

Building those layers takes a moment on a Pi, so it happens on a worker thread
while a plain gradient stands in.
"""

import math
import random
import threading
import time

import pygame

# The caustic fields are generated with numpy where it is available, which is
# the difference between a tenth of a second and several seconds on a Pi.  It
# is not a hard requirement: without it the same field is built at a quarter of
# the resolution in pure Python and the upscale hides the coarseness.
try:
    import numpy as _np
except ImportError:                                    # pragma: no cover
    _np = None

# How much larger cached art is drawn before being scaled down.  Three is the
# sweet spot: visibly smooth edges, still quick enough to build a full wall of
# tiles at startup.
SUPERSAMPLE = 3


# --------------------------------------------------------------------- easing

def ease_out_cubic(p):
    return 1.0 - (1.0 - p) ** 3


def ease_out_quart(p):
    return 1.0 - (1.0 - p) ** 4


def ease_in_out_sine(p):
    return -(math.cos(math.pi * p) - 1.0) / 2.0


def ease_out_back(p, overshoot=1.9):
    """Overshoots slightly then settles.  Good for something snapping into place."""
    q = p - 1.0
    return 1.0 + (overshoot + 1.0) * q ** 3 + overshoot * q ** 2


def clamp01(v):
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def mix(a, b, amount):
    """Blend two colours.  Works for 3 or 4 component colours."""
    amount = clamp01(amount)
    return tuple(int(x + (y - x) * amount) for x, y in zip(a, b))


def shade(colour, factor):
    return tuple(min(255, max(0, int(c * factor))) for c in colour[:3])


# ------------------------------------------------------------------- surfaces

def vertical_gradient(size, top, bottom, curve=None):
    """A smooth vertical gradient.

    `curve` remaps the 0..1 position, so a sine curve gives a band of light
    through the middle with the edges falling away.
    """
    width, height = size
    surf = pygame.Surface(size)
    for y in range(height):
        t = y / max(1, height - 1)
        if curve is not None:
            t = curve(t)
        surf.fill(mix(top, bottom, t), (0, y, width, 1))
    return surf


def supersampled(size, draw, factor=SUPERSAMPLE):
    """Run `draw` at `factor` times `size`, then scale down for anti-aliasing.

    `draw` is called with the oversized surface and the factor, so it can scale
    its own measurements.  Everything it draws comes back with smooth edges.
    """
    width, height = int(size[0]), int(size[1])
    if width < 1 or height < 1:
        return pygame.Surface((max(1, width), max(1, height)), pygame.SRCALPHA)
    big = pygame.Surface((width * factor, height * factor), pygame.SRCALPHA)
    draw(big, factor)
    return pygame.transform.smoothscale(big, (width, height))


def soft_shadow(size, radius, spread, alpha=110):
    """A blurred drop shadow.

    Drawn small and scaled up, which is a cheap and convincing blur: the
    interpolation does the softening, so there is no per-pixel filter.
    """
    width, height = int(size[0] + spread * 2), int(size[1] + spread * 2)
    if width < 4 or height < 4:
        return pygame.Surface((max(4, width), max(4, height)), pygame.SRCALPHA)

    small = max(8, int(min(width, height) / 6))
    scale = small / max(width, height)
    tiny = pygame.Surface((max(4, int(width * scale)), max(4, int(height * scale))),
                          pygame.SRCALPHA)
    inset = max(1, int(spread * scale))
    pygame.draw.rect(
        tiny, (0, 0, 0, alpha),
        pygame.Rect(inset, inset,
                    max(1, tiny.get_width() - inset * 2),
                    max(1, tiny.get_height() - inset * 2)),
        border_radius=max(1, int(radius * scale)))
    return pygame.transform.smoothscale(tiny, (width, height))


def sheen(size, strength=26):
    """A diagonal highlight, the way light sits on glass."""
    width, height = int(size[0]), int(size[1])
    surf = pygame.Surface((width, height), pygame.SRCALPHA)
    for i in range(height):
        t = i / max(1, height - 1)
        a = int(strength * (1.0 - t) ** 2)
        if a > 0:
            pygame.draw.line(surf, (255, 255, 255, a), (0, i), (width, i))
    return surf


# ----------------------------------------------------------------- the water

class WaveField:
    """The pool the wall stands in.

    The reference is the Big Brother title sequence: a deep violet tank with a
    fine tile grid on the wall behind it, a net of pool caustics rippling over
    everything, a churn of white water along the bottom, and bubbles rising
    through it.  The whole shot slowly changes hue, indigo through violet to
    magenta and back.

    None of that can be computed per pixel per frame in Python, so it is built
    the way a game engine builds it:

    * A bright neutral gradient with the tile grid and the bottom pool baked
      into it, drawn once.  The colour of the scene does not live in this
      surface, which is why it never has to be rebuilt.
    * The colour of the scene is one multiply over that gradient.  SDL has no
      fast path for a blended fill, so rather than paying 11 ms of it every
      frame, a background thread repaints a spare copy a couple of times a
      second and the render loop swaps to it.  On a 96 second cycle that is
      far finer than the eye can follow, and it costs the render loop nothing.
    * Two caustic layers, each a field of interference filaments generated
      once, wider than the screen by a whole period so it scrolls seamlessly,
      and taller by a margin so it can drift vertically.  They are plain RGB
      surfaces added onto the scene, which is both what light actually does and
      the cheapest blend SDL has: no per-pixel alpha to read.  The two scroll
      at different speeds in opposite directions, and it is the interference
      between them that reads as water moving rather than wallpaper sliding.
    * Bubbles, a handful of small sprites on sine paths.

    Everything expensive happens once, on a worker thread.  A drawn frame is
    one opaque blit, two additive ones and the bubbles.

    The water keeps its own palette rather than following `display.background`
    and `display.accent`: those are a near black and a cyan, and the show's
    tank is neither.  `deep` and `accent` are still accepted so the call site
    does not have to care, and are deliberately unused.
    """

    #: Vertical slack built into every layer so drifting it never uncovers a
    #: strip of screen the background has not painted.
    DRIFT_MARGIN = 0.035

    #: The gradient the scene is lit through, before it is tinted.  Bright and
    #: close to neutral, because the multiply that follows can only darken.
    STOPS = ((0.00, (72, 86, 128)),
             (0.30, (128, 104, 152)),
             (0.60, (176, 150, 190)),
             (0.85, (226, 208, 236)),
             (1.00, (238, 228, 244)))

    #: The hue cycle, as multiply tints.  Sampled from the reference: it sits
    #: in indigo, warms through violet into magenta, and cools back through
    #: blue.  One is applied per frame; two neighbours are interpolated.
    TINTS = ((160, 150, 230),      # indigo
             (200, 132, 238),      # violet
             (255, 120, 180),      # magenta
             (205, 138, 235),      # violet again, coming back
             (135, 168, 250))      # blue

    #: Seconds for one trip around that cycle.
    HUE_SECONDS = 96.0

    CAUSTIC_TINT = (150, 172, 235)
    SPLASH_TINT = (176, 196, 240)

    BUBBLES = 16

    def __init__(self, size, deep, accent, detail=5, quality=1, speed=1.0,
                 chroma=True):
        self.size = size
        self.detail = max(2, detail)
        self.quality = max(1, quality)
        self.speed = speed
        self.chroma = chroma

        self.base = None
        self.lit = None
        self._spare = None
        self._clock = 0.0
        self.caustic = None
        self.splash = None
        self.bubbles = []
        self.bubble_art = []
        self.caustic_period = 1
        self.splash_period = 1
        self.caustic_top = 0
        self.splash_top = 0
        self.margin = max(4, int(size[1] * self.DRIFT_MARGIN))

        self._converted = False

        self.fallback = self._base(size, grid=False).convert()

        self.ready = threading.Event()
        self._thread = threading.Thread(target=self._build, daemon=True,
                                        name="wave-build")
        self._thread.start()

    # -- the tank ------------------------------------------------------------

    def _stop(self, t):
        """The gradient colour at 0..1, interpolated between the stops."""
        t = clamp01(t)
        stops = self.STOPS
        for i in range(len(stops) - 1):
            lo, low = stops[i]
            hi, high = stops[i + 1]
            if t <= hi:
                span = hi - lo
                return mix(low, high, ease_in_out_sine((t - lo) / span) if span else 0)
        return stops[-1][1]

    def _base(self, size, grid=True):
        """The gradient, the tile wall and the pool of light at the bottom.

        Built bright and almost colourless.  Everything that makes the scene
        violet or magenta happens later, in one multiply, so this surface is
        correct for every point of the hue cycle and is built exactly once.
        """
        width, height = size
        surf = pygame.Surface(size)
        for y in range(height):
            surf.fill(self._stop(y / max(1, height - 1)), (0, y, width, 1))
        if not grid:
            return surf

        # The tile wall.  Grout lines darker than the wall, fading out toward
        # the bottom where the water is brightest and the tiles wash out.
        cell = max(18, int(height / 22))
        veil = pygame.Surface(size, pygame.SRCALPHA)
        for x in range(0, width + cell, cell):
            pygame.draw.line(veil, (24, 28, 60, 52), (x, 0), (x, height))
        for y in range(0, height + cell, cell):
            fade = 1.0 - clamp01(y / height) ** 1.4
            a = int(62 * fade)
            if a > 1:
                pygame.draw.line(veil, (26, 30, 62, a), (0, y), (width, y))
                pygame.draw.line(veil, (255, 255, 255, a // 3),
                                 (0, y + 1), (width, y + 1))
        surf.blit(veil, (0, 0))

        # The pool: the water is thinnest and brightest where it meets the
        # floor, so the bottom of the frame lifts toward white.
        glow = pygame.Surface(size, pygame.SRCALPHA)
        for y in range(int(height * 0.62), height):
            t = clamp01((y - height * 0.62) / max(1.0, height * 0.38))
            a = int(52 * ease_in_out_sine(t) ** 1.7)
            if a > 1:
                pygame.draw.line(glow, (236, 244, 255, a), (0, y), (width, y))
        surf.blit(glow, (0, 0))

        # A vignette.  The wall of frames sits in the middle of the screen and
        # the captions sit under it, so the edges are pulled down to keep the
        # eye on the middle and to give the bottom caption something to read
        # against.  Drawn small and scaled up, which is what makes it smooth.
        edge = pygame.Surface((16, 12), pygame.SRCALPHA)
        edge.fill((10, 12, 34, 96))
        pygame.draw.ellipse(edge, (10, 12, 34, 0), pygame.Rect(-5, -4, 26, 19))
        surf.blit(pygame.transform.smoothscale(edge, size), (0, 0))
        floor = pygame.Surface(size, pygame.SRCALPHA)
        for y in range(int(height * 0.76), height):
            t = clamp01((y - height * 0.76) / max(1.0, height * 0.24))
            a = int(132 * t ** 1.6)
            if a > 1:
                pygame.draw.line(floor, (14, 16, 40, a), (0, y), (width, y))
        surf.blit(floor, (0, 0))
        return surf

    # -- the caustics --------------------------------------------------------

    #: Columns generated at a time.  Chunking keeps the numpy working set to a
    #: couple of megabytes instead of the seventy the whole layer would need,
    #: which matters on a Pi 3 with a gigabyte of RAM.
    CHUNK = 512

    def _field(self, x0, nx, y0, ny, step, span, period, detail, spread,
               sharp, seed):
        """One block of a caustic net, as a 0..1 intensity field.

        Caustics are the bright curves where refracted light folds onto itself,
        so they are the *zero set* of a wave field rather than its peaks.  Four
        interfering sines are summed, the domain is warped by four more so the
        curves bend organically instead of forming a lattice, and the result is
        turned inside out: bright where the sum is near zero, dark elsewhere.
        `spread` sets how thin the filaments are and `sharp` how hard they are.

        Every coefficient on the horizontal term is a whole number of cycles
        across one period, which is what lets the finished layer scroll forever
        without a seam.  They are deliberately not multiples of one another:
        at 5, 7, 12 and 21 cycles they beat against each other over the whole
        period, where 3, 6 and 9 would visibly repeat three times inside it.
        The warp is held to one and two cycles for the same reason: a warp at
        three printed a three column repeat straight through the finished net.

        `nx` columns from `x0` and `ny` rows from `y0`, every `step` pixels, out
        of a field `span` rows tall.  The caller works a slice at a time so the
        working set stays small, skips rows it is going to throw away, and at
        reduced quality samples every second or third pixel rather than
        generating them all and discarding most.
        """
        tau = math.tau
        sin = _np.sin
        u = ((x0 + _np.arange(nx, dtype=_np.float32) * step) / period)[:, None]
        v = ((y0 + _np.arange(ny, dtype=_np.float32) * step) / span)[None, :]
        wx = (u + 0.115 * sin(tau * (1.30 * v + 0.31 + seed))
              + 0.070 * sin(tau * (2 * u + 1.10 + seed))
              + 0.045 * sin(tau * (1 * u + 2.30 * v + 0.55 + seed)))
        wy = (v + 0.090 * sin(tau * (2 * u + 0.20 + seed))
              + 0.060 * sin(tau * (0.70 * v + 0.63 + seed))
              + 0.035 * sin(tau * (1 * u - 1.70 * v + 1.40 + seed)))
        d = detail
        g = (sin(tau * (d * wx + 3.10 * wy + seed))
             + 0.78 * sin(tau * ((d + 2) * wx - 5.30 * wy + 0.37))
             + 0.52 * sin(tau * ((2 * d + 2) * wx + 7.90 * wy + 1.71))
             + 0.24 * sin(tau * ((4 * d + 1) * wx - 11.30 * wy + 2.55)))
        g = _np.abs(g * _np.float32(1.0 / 2.54))
        core = _np.clip(1.0 - g * spread, 0.0, 1.0) ** sharp
        halo = _np.clip(1.0 - g * (spread * 0.22), 0.0, 1.0) ** 2.0
        return _np.clip(core + halo * 0.14, 0.0, 1.0)

    @staticmethod
    def _band(envelope, floor=0.015):
        """The rows of a layer the envelope actually lights.

        A layer confined to the bottom of the frame has no reason to be built,
        stored or blitted at full height.  Sampling the envelope is exact
        enough and keeps this honest if the envelope is ever changed.
        """
        lit = [i for i in range(257) if envelope(i / 256.0) > floor]
        if not lit:
            return 0.0, 1.0
        return lit[0] / 256.0, min(1.0, (lit[-1] + 1) / 256.0)

    def _layer(self, size, period, detail, spread, sharp, seed, tint, peak,
               envelope):
        """Turn one caustic field into a scrolling RGB surface.

        The surface carries no alpha.  It is added onto the scene, which is
        what light does, and an additive blit of a 24 bit surface is the
        cheapest blend SDL offers: nothing to read back per pixel, and black
        contributes exactly nothing, so the dark water between filaments is
        free.

        With numpy this is generated at 1:1 unless `wave_quality` says
        otherwise, because the filaments are thin and an upscale turns them to
        smoke.  The generation is vectorised and happens once, so the cost is a
        fraction of a second rather than the seconds a per-pixel Python loop
        would take.
        """
        span, full_h = size[0] + period, size[1]
        low, high = self._band(envelope)
        top, bottom = int(low * full_h), int(high * full_h)

        if _np is None:
            surf, band = self._layer_slow(span, full_h, top, bottom, period,
                                          detail, spread, sharp, seed, tint,
                                          peak, envelope)
            return surf, band

        step = max(1, self.quality)
        width = -(-span // step)                       # round up: cover the edge
        height = -(-(bottom - top) // step)
        env = _np.array([envelope((top + r * step) / full_h)
                         for r in range(height)], dtype=_np.float32)[None, :]
        gain = [c * peak / 255.0 for c in tint]

        surf = pygame.Surface((width, height))
        for x0 in range(0, width, self.CHUNK):
            nx = min(self.CHUNK, width - x0)
            field = self._field(x0 * step, nx, top, height, step, full_h,
                                period, detail, spread, sharp, seed)
            lit = _np.clip(field * env, 0.0, 1.0)
            rgb = _np.empty((nx, height, 3), dtype=_np.uint8)
            for c in range(3):
                rgb[:, :, c] = lit * gain[c]
            chunk = pygame.Surface((nx, height))
            pygame.surfarray.blit_array(chunk, rgb)
            surf.blit(chunk, (x0, 0))

        if step > 1:
            surf = pygame.transform.smoothscale(surf, (span, bottom - top))
        return surf, (top, bottom)

    def _layer_slow(self, span, full_h, top, bottom, period, detail, spread,
                    sharp, seed, tint, peak, envelope):
        """The same layer without numpy: coarse, then leaned on by the upscale.

        Only reached if numpy is missing.  A quarter of the resolution in each
        direction keeps it to a fraction of a second; the smoothscale that
        follows turns the coarseness into softness.
        """
        tau = math.tau
        # A quarter of the resolution cannot hold a filament four pixels wide:
        # it samples across the gaps and the net comes out dotted.  Widening
        # the filaments to suit the grid is the honest trade, and the upscale
        # then reads them as soft rather than broken.
        spread *= 0.40
        sw, sh = max(8, span // 4), max(8, (bottom - top) // 4)
        small = pygame.Surface((sw, sh))
        for j in range(sh):
            y = top + j * 4
            env = envelope(y / full_h)
            v = y / full_h
            for i in range(sw):
                u = i * 4.0 / period
                wx = (u + 0.115 * math.sin(tau * (1.30 * v + 0.31 + seed))
                      + 0.070 * math.sin(tau * (2 * u + 1.10 + seed))
                      + 0.045 * math.sin(tau * (1 * u + 2.30 * v + 0.55 + seed)))
                wy = (v + 0.090 * math.sin(tau * (2 * u + 0.20 + seed))
                      + 0.060 * math.sin(tau * (0.70 * v + 0.63 + seed))
                      + 0.035 * math.sin(tau * (1 * u - 1.70 * v + 1.40 + seed)))
                g = abs((math.sin(tau * (detail * wx + 3.10 * wy + seed))
                         + 0.78 * math.sin(tau * ((detail + 2) * wx - 5.30 * wy + 0.37))
                         + 0.52 * math.sin(tau * ((2 * detail + 2) * wx + 7.90 * wy + 1.71))
                         + 0.24 * math.sin(tau * ((4 * detail + 1) * wx - 11.30 * wy + 2.55)))
                        / 2.54)
                core = max(0.0, 1.0 - g * spread) ** sharp
                halo = max(0.0, 1.0 - g * spread * 0.22) ** 2.0
                lit = min(1.0, (core + halo * 0.14) * env)
                small.set_at((i, j), tuple(int(c * peak / 255.0 * lit)
                                           for c in tint))
        return (pygame.transform.smoothscale(small, (span, bottom - top)),
                (top, bottom))

    # -- bubbles -------------------------------------------------------------

    def _bubble(self, diameter):
        """One bubble: a dark rim, a bright edge and a specular dot."""
        def draw(surface, factor):
            r = diameter * factor / 2
            centre = (r, r)
            pygame.draw.circle(surface, (58, 74, 108, 255), centre, r)
            pygame.draw.circle(surface, (150, 178, 220, 255), centre, r,
                               max(1, int(r * 0.18)))
            pygame.draw.circle(surface, (205, 224, 255, 255),
                               (r * 0.66, r * 0.58), max(1.0, r * 0.18))
        return supersampled((diameter, diameter), draw, factor=2)

    def _seed_bubbles(self):
        rng = random.Random(20402)
        self.bubble_art = [self._bubble(d) for d in (5, 7, 10, 13, 17)]
        self.bubbles = []
        for _ in range(self.BUBBLES):
            self.bubbles.append((
                rng.random(),                       # x, as a fraction of width
                rng.random(),                       # phase up the screen
                rng.uniform(0.020, 0.055),          # rise, screens per second
                rng.uniform(0.004, 0.013),          # sway, fraction of width
                rng.uniform(0.0, math.tau),         # sway phase
                rng.randrange(len(self.bubble_art)),
            ))

    # -- construction, off the render thread --------------------------------

    def _build(self):
        try:
            width, height = self.size
            full_h = height + self.margin * 2
            # One full period is a whole screen wide, so the eye is never
            # shown two copies of the pattern at once.  It costs one extra
            # screen of surface and nothing per frame: only the visible window
            # is ever blitted.
            self.caustic_period = width
            self.splash_period = max(240, int(width * 0.72))

            self.base = self._base(self.size)

            # The main net: broad cells, strongest against the tile wall and
            # thinning out into the bright water at the bottom.
            self.caustic, offset = self._layer(
                (width, full_h), self.caustic_period,
                detail=self.detail, spread=9.0, sharp=1.0, seed=0.0,
                tint=self.CAUSTIC_TINT, peak=150,
                envelope=lambda t: 0.16 + 0.84 * (1.0 - t) ** 1.15)

            # The churn along the floor: finer, harder, brighter, and confined
            # to a band near the bottom.  Running it the other way is what
            # makes the two nets interfere instead of travelling together.
            self.splash, band = self._layer(
                (width, full_h), self.splash_period,
                detail=self.detail * 2 + 1, spread=13.0, sharp=0.95, seed=1.37,
                tint=self.SPLASH_TINT, peak=88,
                envelope=lambda t: math.exp(-((t - 0.93) / 0.115) ** 2))

            # Each layer knows which rows of the field it occupies, and is
            # only as tall as that.  `caustic_top` and `splash_top` put them
            # back where they belong when they are drawn.
            self.caustic_top = offset[0]
            self.splash_top = band[0]

            self._seed_bubbles()

            # Converting to the display's pixel format removes a software
            # translation SDL would otherwise redo on every blit. Some builds
            # refuse it off the main thread, so failure is not fatal: draw()
            # retries once on the render thread.
            self._try_convert()
            self.lit = self.base
            self.ready.set()
            if self.chroma:
                self._spare = self.base.copy()
                threading.Thread(target=self._tint_loop, daemon=True,
                                 name="wave-tint").start()
        except Exception as exc:                       # never take the show down
            print(f"  ! wave build failed ({exc}); using a plain background")
            self.ready.set()

    def _try_convert(self):
        try:
            self.base = self.base.convert()
            if self.lit is not None:
                self.lit = self.lit.convert()
            self.caustic = self.caustic.convert()
            self.splash = self.splash.convert()
            self.bubble_art = [b.convert_alpha() for b in self.bubble_art]
            self._converted = True
        except pygame.error:
            self._converted = False

    # -- the hue cycle -------------------------------------------------------

    def _tint_loop(self):
        """Repaint the scene's colour on a background thread.

        Tinting is a multiply-blended fill over the whole framebuffer, and SDL
        has no fast path for that: measured at 11.2 ms per pass on a desktop,
        which is a frame and a half wasted on a colour that moves by one level
        every few seconds.  So it happens here instead, into a spare surface
        that `draw` swaps to when it is finished.  pygame releases the GIL for
        fills and blits, so this costs the render thread nothing but the swap.
        """
        last = None
        while True:
            colour = self._tint(self._clock)
            if colour != last:
                last = colour
                spare = self._spare
                spare.blit(self.base, (0, 0))
                spare.fill(colour, special_flags=pygame.BLEND_RGB_MULT)
                self._spare, self.lit = self.lit, spare
            time.sleep(0.45)

    # -- drawing, on the render thread --------------------------------------

    def _tint(self, t):
        """Where the hue cycle stands, as a multiply colour."""
        stops = self.TINTS
        p = (t / self.HUE_SECONDS) % 1.0 * len(stops)
        i = int(p)
        return mix(stops[i], stops[(i + 1) % len(stops)],
                   ease_in_out_sine(p - i))

    def draw(self, screen, elapsed):
        """Blit the water for time `elapsed` in seconds."""
        if not self.ready.is_set() or self.lit is None:
            screen.blit(self.fallback, (0, 0))
            return
        if not self._converted:
            self._try_convert()

        t = elapsed * self.speed
        self._clock = t
        width, height = self.size

        # `lit` is the gradient with the current point of the hue cycle already
        # multiplied into it, repainted by `_tint_loop` off the render thread.
        screen.blit(self.lit, (0, 0))

        # Each net drifts on a slow sine as well as scrolling, so the two never
        # settle into a visible period.  Both are blit offsets, so the motion
        # is free; the margin built into the layers is what stops the drift
        # uncovering a strip of screen.
        deep_y = (math.sin(t * 0.061) * height * 0.013
                  + math.sin(t * 0.207 + 0.7) * height * 0.0035)
        near_y = (math.sin(t * 0.094 + 1.9) * height * 0.009
                  + math.sin(t * 0.331 + 2.1) * height * 0.0025)

        screen.blit(self.caustic,
                    (-int((t * 11.0) % self.caustic_period),
                     -self.margin + int(deep_y) + self.caustic_top),
                    special_flags=pygame.BLEND_RGB_ADD)
        # The churn is confined to a band near the floor, so the layer is only
        # as tall as that band.  Building and blitting it full height would
        # spend most of its cost on rows the envelope has faded to black.
        screen.blit(self.splash,
                    (-self.splash_period + int((t * 19.0) % self.splash_period),
                     -self.margin + int(near_y) + self.splash_top),
                    special_flags=pygame.BLEND_RGB_ADD)

        for x, phase, rise, sway, swing, art in self.bubbles:
            up = (phase + t * rise) % 1.15
            if up > 1.02:                    # off the top, waiting to come round
                continue
            bx = (x + sway * math.sin(t * 0.9 + swing)) * width
            by = height * (1.02 - up)
            screen.blit(self.bubble_art[art], (int(bx), int(by)),
                        special_flags=pygame.BLEND_RGB_ADD)


# ------------------------------------------------------------- the time trip

#: Sub-pixel bits.  Streak positions are `pixels << SUB`, so a light can move
#: at a fractional pixel rate without a float anywhere in the frame loop.
SUB = 8

#: A power-of-two sine table, so the index masks with `& SIN_MASK` instead of
#: taking a modulo.  Built once; read as an array index thereafter.
SIN_BITS = 10
SIN_SIZE = 1 << SIN_BITS
SIN_MASK = SIN_SIZE - 1
SIN_TABLE = [int(127.5 + 127.4 * math.sin(i * math.tau / SIN_SIZE))
             for i in range(SIN_SIZE)]

#: Extra fractional bits on a panel's position in that table.  Whole steps per
#: millisecond is far too coarse: the slowest pulse it can express is a full
#: cycle per second, which reads as a flicker rather than as ambience.
PHASE_FRAC = 8

#: Brightness steps each panel glow is pre-rendered at, and the dimmest one a
#: panel may fall to.  Pulsing by picking a pre-rendered level is an integer
#: index and a blit; pulsing by recolouring would be per-pixel work every
#: frame.  The floor keeps the alcoves breathing rather than blinking out.
GLOW_LEVELS = 16
GLOW_FLOOR = 5

#: How much larger than its frame an alcove glow is drawn.  This has to be
#: generous: the frame itself is opaque, so everything inside it is hidden, and
#: only the ring outside is ever seen.  At 1.3x that ring was the dimmest tail
#: of the falloff and the glows were invisible on the wall.
GLOW_SCALE = 1.85


def _plot_scaled(tiny, size):
    """Scale a small hand-plotted template up to its final size.

    This is what lets every soft gradient here be built without numpy and
    without a per-pixel Python loop at full resolution.  A falloff is plotted
    into a 48x34 surface, about sixteen hundred `set_at` calls, and smoothscale
    does the interpolation in C.  Plotting the same gradient directly at
    1920x1080 would be two million Python iterations.
    """
    return pygame.transform.smoothscale(tiny, (max(1, int(size[0])),
                                               max(1, int(size[1]))))


class _Streak:
    """One flowing light, in fixed point.

    `x` and `vx` are in 1/256ths of a pixel and `vx` is per millisecond, which
    is the unit the clock already deals in, so advancing a streak is one
    integer multiply and one integer add.
    """

    __slots__ = ("x", "y", "vx", "sprite", "rect")

    def __init__(self, x, y, vx, sprite):
        self.x = x
        self.y = y
        self.vx = vx
        self.sprite = sprite
        self.rect = sprite.get_rect(topleft=(x >> SUB, y))

    def advance(self, dt_ms, wrap_at, respawn_at):
        self.x += self.vx * dt_ms
        if (self.x >> SUB) > wrap_at:
            self.x = respawn_at
        self.rect = self.sprite.get_rect(topleft=(self.x >> SUB, self.y))


class TimeTripField:
    """The season 28 background: a lit grid, neon flow, and a warp seam.

    Deep space navy under a digital grid, soft cyan and magenta panels glowing
    in the alcoves the prize frames sit in, neon lights drifting left to right,
    and a warp seam that sweeps across every so often.

    It is built to the same rule as everything else here: nothing is computed
    per frame that can be copied instead.  The plate, every streak and every
    brightness of every panel glow are built once at startup and then only
    blitted.  The glows are 24 bit surfaces added onto the scene, which is both
    what light does and the cheapest blend SDL has, and it means the black
    around a sprite contributes nothing and costs nothing.

    The standalone `timetrip_wall.py` also does dirty rectangles, which are
    worth better than two to one there.  They are deliberately not used here:
    the wall composites nine tiles, their captions and a header over this every
    frame, so the screen is never still enough for them to pay.
    """

    def __init__(self, size, deep, accent, panels=None, speed=1.0,
                 streaks=20, seam=True, seed=28):
        self.size = size
        self.width, self.height = size
        self.deep = tuple(deep[:3])
        self.accent = tuple(accent[:3])
        self.speed = max(0.05, speed)
        self.rng = random.Random(seed)

        self._clock_ms = None            # last elapsed time draw() was given

        self.background = self._plate()

        # A small pool of shared sprites: twenty streaks drawn from six
        # surfaces means six in memory, not twenty, each already in the
        # display's pixel format.
        self.streak_sprites = []
        for i in range(6):
            length = int(self.width * (0.16 + 0.07 * (i % 3)))
            thickness = max(3, int(self.height * (0.007 + 0.004 * (i % 2))))
            colour = self.STREAK_COLOURS[i % len(self.STREAK_COLOURS)]
            self.streak_sprites.append(self._streak_sprite(
                length, thickness, colour,
                peak=190 if colour is self.CYAN else 150))

        # One streak per horizontal band with a little jitter.  Twenty uniform
        # random numbers reliably leave bald patches and clumps; stratifying
        # keeps the flow even without anything having to look at the others.
        self.streaks = []
        band = self.height / max(1, streaks)
        for i in range(streaks):
            sprite = self.streak_sprites[i % len(self.streak_sprites)]
            y = min(int(band * i + self.rng.uniform(0, band * 0.8)),
                    self.height - sprite.get_height())
            pps = self.rng.uniform(40.0, 170.0) * self.speed
            vx = max(1, int(pps * (1 << SUB) / 1000.0))
            x = self.rng.randrange(-sprite.get_width(), self.width) << SUB
            self.streaks.append(_Streak(x, y, vx, sprite))
        self.longest = max((s.get_width() for s in self.streak_sprites),
                           default=1)

        self._make_panels(panels or [])

        self.seam = None
        if seam:
            seam_w = max(10, int(self.width * 0.075))
            self.seam = self._seam_sprite(seam_w, self.height)
            self.seam_x = -seam_w << SUB
            self.seam_vx = max(1, int(220.0 * self.speed * (1 << SUB) / 1000.0))
            self.seam_rect = self.seam.get_rect(topleft=(self.seam_x >> SUB, 0))

        # Nothing here is built off the main thread, so the field is usable the
        # moment it exists.  The event is kept for interface parity with the
        # pool field, which does build on a worker.
        self.ready = threading.Event()
        self.ready.set()

    # -- palette -------------------------------------------------------------

    CYAN = (0, 232, 255)
    MAGENTA = (255, 44, 168)
    PURPLE = (150, 84, 255)
    SPACE_TOP = (3, 5, 16)
    SPACE_MID = (10, 19, 52)
    SPACE_BOT = (5, 8, 26)
    GRID_MINOR = (14, 26, 58)
    GRID_MAJOR = (24, 46, 96)
    STREAK_COLOURS = (CYAN, CYAN, CYAN, PURPLE, PURPLE, MAGENTA)

    # -- the static plate ----------------------------------------------------

    def _plate(self):
        """Gradient, digital grid and vignette, built once.

        `.convert()` at the end is not decoration.  It stores the surface in
        the display's own pixel format, so the once-per-frame background blit
        is a straight memory copy rather than a per-pixel format translation.
        """
        width, height = self.size
        surf = pygame.Surface(self.size)

        # Filling one row at a time is a C memset per row, so a thousand of
        # them at startup costs nothing and avoids all per-pixel work.
        for y in range(height):
            t = y / max(1, height - 1)
            if t < 0.5:
                k, a, b = t * 2.0, self.SPACE_TOP, self.SPACE_MID
            else:
                k, a, b = (t - 0.5) * 2.0, self.SPACE_MID, self.SPACE_BOT
            k = k * k * (3.0 - 2.0 * k)          # smoothstep, so it cannot band
            surf.fill(mix(a, b, k), (0, y, width, 1))

        cell = max(16, height // 22)
        for x in range(0, width + cell, cell):
            major = (x // cell) % 4 == 0
            pygame.draw.line(surf, self.GRID_MAJOR if major else self.GRID_MINOR,
                             (x, 0), (x, height))
        for y in range(0, height + cell, cell):
            major = (y // cell) % 4 == 0
            pygame.draw.line(surf, self.GRID_MAJOR if major else self.GRID_MINOR,
                             (0, y), (width, y))

        # Vignette, so the middle of the wall reads as the lit area.
        tiny = pygame.Surface((24, 14), pygame.SRCALPHA)
        tiny.fill((0, 0, 0, 190))
        pygame.draw.ellipse(tiny, (0, 0, 0, 0), pygame.Rect(-7, -5, 38, 24))
        surf.blit(_plot_scaled(tiny, self.size), (0, 0))
        return surf.convert()

    # -- sprites -------------------------------------------------------------

    def _streak_sprite(self, length, thickness, colour, peak):
        """A neon streak: a bright head with a long, soft tail."""
        tw, th = 64, 16
        tiny = pygame.Surface((tw, th))
        mid = (th - 1) / 2.0
        for ty in range(th):
            dy = abs(ty - mid) / mid
            across = max(0.0, 1.0 - dy * dy) ** 2
            for tx in range(tw):
                u = tx / (tw - 1)
                along = (u ** 0.55) * (1.0 - u) * 4.0
                v = across * min(1.0, along) * peak / 255.0
                tiny.set_at((tx, ty), (int(colour[0] * v), int(colour[1] * v),
                                       int(colour[2] * v)))
        return _plot_scaled(tiny, (length, thickness)).convert()

    def _glow_levels(self, size, colour, peak):
        """A lit alcove, pre-rendered at every brightness it will ever use."""
        tw, th = 48, 34
        tiny = pygame.Surface((tw, th))
        cx, cy = (tw - 1) / 2.0, (th - 1) / 2.0
        for ty in range(th):
            for tx in range(tw):
                # A squircle rather than a circle: fourth powers square off the
                # middle and leave the corners round, so this reads as a lit
                # panel behind a frame instead of a ball of light.
                # A squircle rather than a circle: fourth powers square off
                # the middle and leave the corners round.  The plateau covers
                # the area the frame will hide, so the falloff happens entirely
                # in the ring that is actually visible around it.
                dx, dy = abs(tx - cx) / cx, abs(ty - cy) / cy
                d = (dx ** 4 + dy ** 4) ** 0.25
                v = clamp01((1.0 - d) / (1.0 - 1.0 / GLOW_SCALE)) ** 2
                v *= peak / 255.0
                tiny.set_at((tx, ty), (int(colour[0] * v), int(colour[1] * v),
                                       int(colour[2] * v)))
        full = _plot_scaled(tiny, size)

        levels = []
        for i in range(GLOW_LEVELS):
            k = int(255 * (i + 1) / GLOW_LEVELS)
            step = full.copy()
            step.fill((k, k, k), special_flags=pygame.BLEND_RGB_MULT)
            levels.append(step.convert())
        return levels

    def _seam_sprite(self, width, height):
        """The warp seam: a hot core inside a wide, much dimmer halo."""
        tiny = pygame.Surface((24, 4))
        for tx in range(24):
            u = tx / 23.0
            halo = max(0.0, 1.0 - abs(u - 0.5) * 2.0) ** 2.4
            core = max(0.0, 1.0 - abs(u - 0.5) * 9.0) ** 2
            colour = (int(self.PURPLE[0] * halo * 0.15 + 255 * core * 0.16),
                      int(self.CYAN[1] * halo * 0.18 + 255 * core * 0.30),
                      int(self.CYAN[2] * halo * 0.24 + 255 * core * 0.40))
            pygame.draw.line(tiny, colour, (tx, 0), (tx, 3))
        return _plot_scaled(tiny, (width, height)).convert()

    # -- the alcoves ---------------------------------------------------------

    def _make_panels(self, rects):
        """One glowing alcove behind each frame the wall is going to draw.

        The rectangles come from the wall's own layout, so the light lines up
        with the pictures rather than with a grid guessed here.  Frames are all
        one size, so a single ladder of pre-rendered brightnesses serves every
        panel; a mixed layout builds one ladder per distinct size.
        """
        self.panels = []
        self._glow_cache = {}
        tints = (self.CYAN, self.PURPLE, self.MAGENTA)
        peaks = (170, 158, 148)
        # Picked at random rather than by index.  `i % 3` on a three column
        # wall gives every column one colour, which reads as deliberate
        # striping; the seeded generator keeps it mixed but reproducible.
        for i, rect in enumerate(rects):
            size = (int(rect.width * GLOW_SCALE),
                    int(rect.height * GLOW_SCALE))
            pick = self.rng.randrange(len(tints))
            key = (size, pick)
            glows = self._glow_cache.get(key)
            if glows is None:
                glows = self._glow_levels(size, tints[pick], peaks[pick])
                self._glow_cache[key] = glows
            glow_rect = glows[0].get_rect(center=rect.center)
            phase = self.rng.randrange(SIN_SIZE << PHASE_FRAC)
            period_ms = self.rng.randrange(5000, 9000)
            rate = max(1, int((SIN_SIZE << PHASE_FRAC) / period_ms / self.speed))
            self.panels.append([glow_rect, glows, phase, rate])

    def set_panels(self, rects):
        """Re-place the alcoves, after a resize or a reshuffle of the wall."""
        self._make_panels(list(rects))

    # -- drawing -------------------------------------------------------------

    def _advance(self, dt_ms):
        respawn = -self.longest << SUB
        for streak in self.streaks:
            streak.advance(dt_ms, self.width, respawn)

        wrap = (SIN_SIZE << PHASE_FRAC) - 1
        for panel in self.panels:
            panel[2] = (panel[2] + panel[3] * dt_ms) & wrap

        if self.seam is not None:
            self.seam_x += self.seam_vx * dt_ms
            if (self.seam_x >> SUB) > self.width:
                # A long pause between sweeps, so it reads as an event rather
                # than a metronome.
                self.seam_x = -(self.width * 3) << SUB
            self.seam_rect = self.seam.get_rect(topleft=(self.seam_x >> SUB, 0))

    def draw(self, screen, elapsed):
        """Paint the background for time `elapsed` in seconds.

        The motion is driven by the change in `elapsed`, so it stays
        frame-rate independent and survives the wall skipping frames while a
        reveal sits still.  A long gap is clamped rather than applied, or
        everything would teleport when the wall wakes up again.
        """
        now_ms = int(elapsed * 1000.0)
        if self._clock_ms is None:
            self._clock_ms = now_ms
        dt_ms = now_ms - self._clock_ms
        self._clock_ms = now_ms
        if dt_ms > 0:
            self._advance(min(dt_ms, 100))

        screen.blit(self.background, (0, 0))

        add = pygame.BLEND_RGB_ADD
        for rect, glows, phase, _rate in self.panels:
            level = GLOW_FLOOR + (
                (SIN_TABLE[phase >> PHASE_FRAC] * (GLOW_LEVELS - GLOW_FLOOR)) >> 8)
            screen.blit(glows[level], rect, special_flags=add)

        if self.seam is not None:
            screen.blit(self.seam, self.seam_rect, special_flags=add)

        for streak in self.streaks:
            screen.blit(streak.sprite, streak.rect, special_flags=add)


# ---------------------------------------------------------------- the frames

def render_frame(surface, rect, base, lit, factor=1):
    """Draw a moulded picture frame onto `surface`, inside `rect`.

    Called from inside `supersampled`, so `factor` scales the measurements and
    every edge comes back anti-aliased.
    """
    band = max(2, int(min(rect.width, rect.height) * 0.055))
    highlight = mix(base, (255, 255, 255), 0.42)
    shadow = shade(base, 0.38)

    # The moulding, as a stack of rings from dark outside to light inside,
    # which is what gives it a rounded rather than a flat profile.
    steps = max(3, band // max(1, factor))
    for i in range(steps):
        t = i / max(1, steps - 1)
        ring = rect.inflate(-2 * i * (band / steps), -2 * i * (band / steps))
        colour = mix(shade(base, 0.72), highlight, ease_in_out_sine(t))
        pygame.draw.rect(surface, colour, ring,
                         width=max(1, int(band / steps) + 1),
                         border_radius=max(1, int(band * 0.7)))

    # Light from the top left: the near edges catch it, the far ones fall away.
    pygame.draw.line(surface, mix(highlight, (255, 255, 255), 0.5),
                     (rect.left + band, rect.top + max(1, factor)),
                     (rect.right - band, rect.top + max(1, factor)), max(1, factor))
    pygame.draw.line(surface, shadow,
                     (rect.left + band, rect.bottom - max(1, factor)),
                     (rect.right - band, rect.bottom - max(1, factor)), max(1, factor))

    # The lip where the moulding drops away to the picture.
    inner = rect.inflate(-2 * band, -2 * band)
    pygame.draw.rect(surface, shadow, inner.inflate(2 * factor, 2 * factor),
                     width=max(1, factor), border_radius=max(1, int(band * 0.3)))
    if lit:
        pygame.draw.rect(surface, mix(base, (255, 255, 255), 0.65), rect,
                         width=max(1, int(band * 0.45)),
                         border_radius=max(1, int(band * 0.7)))
    return inner
