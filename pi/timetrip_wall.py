#!/usr/bin/env python3
"""Big Brother 28 "Time Trip" memory wall background, for a Raspberry Pi.

Self contained: no images, no fonts, no numpy.  Everything on screen is drawn
with pygame primitives at startup and then moved around.

The whole design follows one rule: a Pi 3 can copy memory quickly and can do
arithmetic slowly, so nothing is *computed* per frame that can be *copied*
instead.  Concretely:

1.  The gradient, the grid and every glowing sprite are built once, during
    startup, and never touched again.  A frame is blits.
2.  The glows are plain 24 bit surfaces added onto the scene with
    BLEND_RGB_ADD.  Additive is the cheapest blend SDL has (nothing to read
    back per pixel) and it is also what light physically does, so black areas
    of a sprite contribute exactly nothing and cost nothing.
3.  Only the parts of the screen that actually changed are redrawn and handed
    to `display.update()`.  The wall is mostly still, so this is the single
    biggest win: measure it yourself with the D key.
4.  Positions are integers in 1/256ths of a pixel, advanced by the integer
    millisecond delta the clock already gives us.  No floats in the frame loop.
5.  Pulses come from a 1024 entry sine table indexed with a masked integer, and
    each panel's brightness is quantised to one of 16 pre rendered levels, so a
    pulsing glow is a lookup and a blit rather than a recolour.

This is the standalone preview and benchmark.  The version the prize wall
actually runs is `TimeTripField` in visuals.py, which is the same design with
the alcoves placed from the wall's own layout and the dirty rectangle path
removed, because the wall composites tiles over the background every frame.
The two are deliberately separate so this one stays a single file with no
project imports: change the look here and you must change it there too.

Controls:  ESC or Q quit.  F toggles fullscreen.  D toggles dirty rectangles
(so you can see what they are worth).  S toggles the on screen stats.

    python3 timetrip_wall.py
    python3 timetrip_wall.py --size 1920x1080
    python3 timetrip_wall.py --windowed --bench 600
"""

import argparse
import math
import random
import sys
import time

import pygame

# --------------------------------------------------------------------- palette

SPACE_TOP = (3, 5, 16)          # obsidian, top of the frame
SPACE_MID = (10, 19, 52)        # space navy through the middle
SPACE_BOT = (5, 8, 26)          # falls away again at the floor

CYAN = (0, 232, 255)
MAGENTA = (255, 44, 168)
PURPLE = (150, 84, 255)

GRID_MINOR = (14, 26, 58)
GRID_MAJOR = (24, 46, 96)

# Streaks pick from these.  Cyan dominates, magenta and purple accent.
STREAK_COLOURS = (CYAN, CYAN, CYAN, PURPLE, PURPLE, MAGENTA)

# ------------------------------------------------------------------ fixed point

#: Sub-pixel bits.  Positions are stored as `pixels << SUB`, so a streak can
#: move at a fractional pixel rate without a single float in the frame loop.
SUB = 8

#: One full turn of the sine table.  A power of two so the index can be masked
#: with `& SIN_MASK` instead of a modulo, which on a Pi 3 is a real saving when
#: it happens for every panel every frame.
SIN_BITS = 10
SIN_SIZE = 1 << SIN_BITS
SIN_MASK = SIN_SIZE - 1

#: sin() scaled to 0..255 and stored as ints.  Building this costs a thousand
#: sin() calls once; reading it costs an array index.
SIN_TABLE = [int(127.5 + 127.4 * math.sin(i * math.tau / SIN_SIZE))
             for i in range(SIN_SIZE)]

#: Extra fractional bits on a panel's position in the sine table.  Whole steps
#: per millisecond is far too coarse: the slowest possible pulse would still be
#: a full cycle per second, which reads as a flicker rather than as ambience.
#: With 8 bits of fraction the same integer arithmetic gives cycles measured in
#: seconds, and slow pulses are also what makes the dirty rectangle path pay,
#: because a panel is only repainted when its quantised level actually moves.
PHASE_FRAC = 8

#: How many brightness steps each panel glow is pre rendered at.  A pulsing
#: glow is then "pick sprite N" rather than "rebuild a gradient", and 16 steps
#: is far finer than the eye can follow at these intensities.
GLOW_LEVELS = 16

#: The dimmest step a panel is allowed to fall to.  The panels are the lit
#: alcoves the frames sit in, so they breathe rather than blink: the pulse
#: modulates the top two thirds of the range and never puts one out.
GLOW_FLOOR = 5

#: Pixels of overdraw tolerated when combining two dirty rectangles.  Roughly
#: the cost of one small sprite: below this, one blit beats two.
MERGE_SLACK = 3000


# ------------------------------------------------------------- sprite building

def _smooth(tiny, size):
    """Scale a small hand-plotted template up to its final size.

    This is the trick that lets the whole file avoid numpy and avoid per pixel
    Python loops at full resolution.  A soft round glow is plotted into, say,
    a 32x32 surface (1024 set_at calls, a few milliseconds once) and then
    smoothscale does the interpolation in C.  The result is a smooth gradient
    that would have cost two million Python iterations to plot directly.
    """
    return pygame.transform.smoothscale(tiny, (max(1, int(size[0])),
                                               max(1, int(size[1]))))


def build_background(size):
    """The static plate: gradient, vignette and the digital grid.

    Drawn once and thereafter used two ways, both of them pure memory copies:
    as the opening blit of a full frame, and as the "erase" source when a dirty
    rectangle needs the scene underneath a sprite put back.

    `.convert()` at the end matters more than it looks.  It stores the surface
    in the display's own pixel format, so every later blit is a straight copy
    instead of a per pixel format translation.  On a Pi that is the difference
    between a background blit costing a fraction of a millisecond and costing
    several.
    """
    width, height = size
    surf = pygame.Surface(size)

    # Vertical gradient, one filled row at a time.  `fill` on a 1px tall rect
    # is a C memset, so a thousand of them at startup is nothing, and it avoids
    # any per pixel work.
    for y in range(height):
        t = y / max(1, height - 1)
        if t < 0.5:                      # top colour into the mid colour
            k = t * 2.0
            a, b = SPACE_TOP, SPACE_MID
        else:                            # mid colour back down to the floor
            k = (t - 0.5) * 2.0
            a, b = SPACE_MID, SPACE_BOT
        k = k * k * (3.0 - 2.0 * k)      # smoothstep, so the stops do not band
        surf.fill((int(a[0] + (b[0] - a[0]) * k),
                   int(a[1] + (b[1] - a[1]) * k),
                   int(a[2] + (b[2] - a[2]) * k)),
                  (0, y, width, 1))

    # The digital grid.  Cell size is derived from the height so it looks the
    # same on a 720p panel and a 1080p TV.
    cell = max(16, height // 22)
    for x in range(0, width + cell, cell):
        major = (x // cell) % 4 == 0
        pygame.draw.line(surf, GRID_MAJOR if major else GRID_MINOR,
                         (x, 0), (x, height))
    for y in range(0, height + cell, cell):
        major = (y // cell) % 4 == 0
        pygame.draw.line(surf, GRID_MAJOR if major else GRID_MINOR,
                         (0, y), (width, y))

    # Vignette, so the middle of the wall reads as the lit area.  Plotted at
    # 24x14 and scaled up, which is both smooth and instant.
    tiny = pygame.Surface((24, 14), pygame.SRCALPHA)
    tiny.fill((0, 0, 0, 190))
    pygame.draw.ellipse(tiny, (0, 0, 0, 0), pygame.Rect(-7, -5, 38, 24))
    surf.blit(_smooth(tiny, size), (0, 0))

    return surf.convert()


def build_streak_sprite(length, thickness, colour, peak):
    """One neon streak: a bright core that fades out along its own length.

    Returned as a 24 bit surface with no alpha channel, because it is drawn
    with BLEND_RGB_ADD.  Additive blending needs no destination read and no
    alpha lane, so it is the cheapest per pixel operation available, and the
    black margins of the sprite add zero and are effectively free.
    """
    # Plot the falloff small.  The core is a horizontal bar with a soft edge
    # above and below it; along x the head is bright and the tail fades.
    tw, th = 64, 16
    tiny = pygame.Surface((tw, th))
    mid = (th - 1) / 2.0
    for ty in range(th):
        # Across the streak: a narrow bright core, falling off to nothing.
        dy = abs(ty - mid) / mid
        across = max(0.0, 1.0 - dy * dy)
        across *= across
        for tx in range(tw):
            # Along the streak: bright head, long tail, both ends soft, so the
            # sprite can be blitted anywhere without a visible cut.
            u = tx / (tw - 1)
            along = (u ** 0.55) * (1.0 - u) * 4.0
            v = across * min(1.0, along)
            tiny.set_at((tx, ty), (int(colour[0] * peak * v / 255),
                                   int(colour[1] * peak * v / 255),
                                   int(colour[2] * peak * v / 255)))
    return _smooth(tiny, (length, thickness)).convert()


def build_glow_levels(size, colour, peak):
    """A soft rounded panel glow, pre rendered at GLOW_LEVELS brightnesses.

    Pulsing a glow by rebuilding it, or by recolouring it, would be per pixel
    work every frame.  Pre rendering a ladder of brightnesses turns the pulse
    into an integer index and a blit.  Sixteen surfaces of a panel sized area
    is a trivial amount of memory and it never grows.
    """
    tw, th = 48, 34
    tiny = pygame.Surface((tw, th))
    cx, cy = (tw - 1) / 2.0, (th - 1) / 2.0
    for ty in range(th):
        for tx in range(tw):
            # A squircle rather than a circle: raising the distance terms to
            # the fourth power squares off the middle and leaves the corners
            # rounded, so this reads as a lit panel behind a frame instead of
            # a ball of light.
            dx = abs(tx - cx) / cx
            dy = abs(ty - cy) / cy
            d = (dx ** 4 + dy ** 4) ** 0.25
            v = max(0.0, 1.0 - d * 0.94)
            v *= v
            tiny.set_at((tx, ty), (int(colour[0] * v * peak / 255),
                                   int(colour[1] * v * peak / 255),
                                   int(colour[2] * v * peak / 255)))
    full = _smooth(tiny, size)

    levels = []
    for i in range(GLOW_LEVELS):
        k = (i + 1) / GLOW_LEVELS
        step = full.copy()
        # One multiply per level, at build time, never in the frame loop.
        step.fill((int(255 * k), int(255 * k), int(255 * k)),
                  special_flags=pygame.BLEND_RGB_MULT)
        levels.append(step.convert())
    return levels


def build_seam_sprite(width, height):
    """The warp seam: a bright vertical rip that sweeps across the wall."""
    tiny = pygame.Surface((24, 4))
    for tx in range(24):
        u = tx / 23.0
        v = max(0.0, 1.0 - abs(u - 0.5) * 2.0) ** 2.4
        # A hot thin core with a wide, much dimmer halo, so it reads as light
        # tearing through rather than as a bar drawn on top.
        core = max(0.0, 1.0 - abs(u - 0.5) * 9.0) ** 2
        c = (int(PURPLE[0] * v * 0.22 + 255 * core * 0.30),
             int(CYAN[1] * v * 0.26 + 255 * core * 0.55),
             int(CYAN[2] * v * 0.34 + 255 * core * 0.70))
        pygame.draw.line(tiny, c, (tx, 0), (tx, 3))
    return _smooth(tiny, (width, height)).convert()


# ------------------------------------------------------------------- the scene

class Streak:
    """One flowing light, in fixed point.

    `x` and `vx` are in 1/256ths of a pixel.  `vx` is per millisecond, and the
    clock hands us whole milliseconds, so advancing a streak is one integer
    multiply and one integer add.  There is no float arithmetic in the frame
    loop at all.
    """

    __slots__ = ("x", "y", "vx", "sprite", "rect", "prev")

    def __init__(self, x, y, vx, sprite):
        self.x = x
        self.y = y
        self.vx = vx
        self.sprite = sprite
        self.rect = sprite.get_rect(topleft=(x >> SUB, y))
        self.prev = self.rect.copy()

    def advance(self, dt_ms, wrap_at, respawn_at):
        self.prev = self.rect
        self.x += self.vx * dt_ms
        if (self.x >> SUB) > wrap_at:
            self.x = respawn_at
        self.rect = self.sprite.get_rect(topleft=(self.x >> SUB, self.y))


class TimeTripWall:
    """The animated background.

    `update(dt_ms)` moves things, `draw(screen)` paints and returns the list of
    rectangles that changed.  Nothing else allocates.
    """

    #: Panels behind where the houseguest frames sit.
    PANEL_COLS = 8
    PANEL_ROWS = 2

    def __init__(self, size, seed=28):
        self.size = size
        self.width, self.height = size
        self.rng = random.Random(seed)

        self.background = build_background(size)

        # -- streaks ---------------------------------------------------------
        # A small pool of shared sprites.  Twenty streaks drawn from six
        # sprites means six surfaces in memory, not twenty, and every one of
        # them is already in the display's pixel format.
        self.streak_sprites = []
        for i in range(6):
            length = int(self.width * (0.16 + 0.07 * (i % 3)))
            thickness = max(3, int(self.height * (0.007 + 0.004 * (i % 2))))
            colour = STREAK_COLOURS[i % len(STREAK_COLOURS)]
            self.streak_sprites.append(
                build_streak_sprite(length, thickness, colour,
                                    peak=190 if colour is CYAN else 150))

        # One streak per horizontal band with a little jitter, rather than
        # twenty uniform random numbers.  Uniform random reliably leaves bald
        # patches and clumps; stratifying keeps the flow even without anything
        # having to check where the others are.
        self.streaks = []
        count = 20
        band = self.height / count
        for i in range(count):
            sprite = self.streak_sprites[i % len(self.streak_sprites)]
            y = int(band * i + self.rng.uniform(0, band * 0.8))
            y = min(y, self.height - sprite.get_height())
            # Pixels per second, converted once into fixed point per ms.
            pps = self.rng.uniform(40.0, 170.0)
            vx = max(1, int(pps * (1 << SUB) / 1000.0))
            x = self.rng.randrange(-sprite.get_width(), self.width) << SUB
            self.streaks.append(Streak(x, y, vx, sprite))

        self.longest = max(s.get_width() for s in self.streak_sprites)

        # -- panels ----------------------------------------------------------
        # A cell per houseguest, with the glow a little larger than the frame
        # it sits behind so it reads as light spilling around the edge.  The
        # gaps matter: overlapping glows merge into one wash and, less
        # obviously, they merge into one enormous dirty rectangle too.
        cell_w = self.width * 0.88 / self.PANEL_COLS
        panel_w = int(cell_w * 0.86)
        panel_h = int(panel_w * 0.74)
        glow_w, glow_h = int(panel_w * 1.30), int(panel_h * 1.34)

        self.panel_glows = [
            build_glow_levels((glow_w, glow_h), CYAN, 205),
            build_glow_levels((glow_w, glow_h), PURPLE, 190),
            build_glow_levels((glow_w, glow_h), MAGENTA, 175),
        ]

        self.panels = []
        left = (self.width - cell_w * self.PANEL_COLS) / 2.0
        row_y = (int(self.height * 0.33), int(self.height * 0.68))
        for row in range(self.PANEL_ROWS):
            for col in range(self.PANEL_COLS):
                cx = int(left + cell_w * (col + 0.5))
                cy = row_y[row % len(row_y)]
                glows = self.panel_glows[(col + row) % len(self.panel_glows)]
                rect = glows[0].get_rect(center=(cx, cy))
                # phase: where this panel sits in the sine table.
                # rate: table steps per millisecond, kept integer.
                phase = self.rng.randrange(SIN_SIZE << PHASE_FRAC)
                # One cycle every 5 to 9 seconds.
                period_ms = self.rng.randrange(5000, 9000)
                rate = max(1, (SIN_SIZE << PHASE_FRAC) // period_ms)
                self.panels.append([rect, glows, phase, rate, -1])

        # -- warp seam -------------------------------------------------------
        seam_w = max(10, int(self.width * 0.075))
        self.seam = build_seam_sprite(seam_w, self.height)
        self.seam_x = -seam_w << SUB
        self.seam_vx = max(1, int(220.0 * (1 << SUB) / 1000.0))
        self.seam_rect = self.seam.get_rect(topleft=(self.seam_x >> SUB, 0))
        self.seam_prev = self.seam_rect.copy()

        self.dirty_mode = True
        self.last_area = 0
        self.last_rects = 0

    # -- motion --------------------------------------------------------------

    def update(self, dt_ms):
        """Advance everything by an integer number of milliseconds."""
        if dt_ms <= 0:
            return
        respawn = -self.longest << SUB
        for streak in self.streaks:
            streak.advance(dt_ms, self.width, respawn)

        for panel in self.panels:
            panel[2] = (panel[2] + panel[3] * dt_ms) & ((SIN_SIZE << PHASE_FRAC) - 1)

        self.seam_prev = self.seam_rect
        self.seam_x += self.seam_vx * dt_ms
        if (self.seam_x >> SUB) > self.width:
            # Long pause between sweeps, so it reads as an event rather than a
            # metronome.
            self.seam_x = -(self.width * 3) << SUB
        self.seam_rect = self.seam.get_rect(topleft=(self.seam_x >> SUB, 0))

    # -- painting ------------------------------------------------------------

    def paint(self, screen, area):
        """Repaint one rectangle of the scene, in back to front order.

        `set_clip` hands the clipping to SDL, so each sprite can be blitted
        whole and the driver writes only the pixels inside `area`.  That is
        much cheaper than working out sub rectangles in Python, and it is what
        makes the dirty rectangle path worth having.
        """
        screen.set_clip(area)
        screen.blit(self.background, (0, 0))

        for rect, glows, phase, _rate, level in self.panels:
            if rect.colliderect(area):
                screen.blit(glows[level], rect, special_flags=pygame.BLEND_RGB_ADD)

        if self.seam_rect.colliderect(area):
            screen.blit(self.seam, self.seam_rect,
                        special_flags=pygame.BLEND_RGB_ADD)

        for streak in self.streaks:
            if streak.rect.colliderect(area):
                screen.blit(streak.sprite, streak.rect,
                            special_flags=pygame.BLEND_RGB_ADD)

        screen.set_clip(None)

    def draw(self, screen):
        """Paint the frame.  Returns the rectangles that need presenting."""
        # Brightness first: a panel is only dirty if its quantised level moved,
        # which for a slow pulse is a small fraction of frames.
        changed_panels = []
        for panel in self.panels:
            level = GLOW_FLOOR + (
                (SIN_TABLE[panel[2] >> PHASE_FRAC] * (GLOW_LEVELS - GLOW_FLOOR))
                >> 8)
            if level != panel[4]:
                panel[4] = level
                changed_panels.append(panel[0])

        if not self.dirty_mode:
            self.paint(screen, screen.get_rect())
            return None                              # caller flips everything

        dirty = []
        for streak in self.streaks:
            dirty.append(streak.rect.union(streak.prev))
        dirty.append(self.seam_rect.union(self.seam_prev))
        dirty.extend(changed_panels)

        # Coalesce, but only where merging is actually cheaper.  Two rectangles
        # that merely touch at a corner have a union many times their combined
        # area, and merging those is how a handful of small sprites turns into
        # most of the screen: measured at 18 rectangles covering 86% of a 720p
        # display, which made the dirty path slower than a full redraw.  So a
        # merge only happens when the union wastes little, and otherwise the
        # rectangles stay separate.
        bounds = screen.get_rect()
        merged = []
        for rect in dirty:
            rect = rect.clip(bounds)
            if not rect.width or not rect.height:
                continue
            area = rect.width * rect.height
            for i, other in enumerate(merged):
                union = other.union(rect)
                waste = (union.width * union.height
                         - other.width * other.height - area)
                if waste <= MERGE_SLACK:
                    merged[i] = union
                    break
            else:
                merged.append(rect)

        self.last_area = sum(r.width * r.height for r in merged)
        self.last_rects = len(merged)
        for rect in merged:
            self.paint(screen, rect)
        return merged


# ------------------------------------------------------------------- the shell

def parse_size(text):
    w, _, h = text.lower().partition("x")
    return int(w), int(h)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--size", default="1280x720", type=parse_size,
                    help="window size, e.g. 1920x1080")
    ap.add_argument("--windowed", action="store_true",
                    help="force a window rather than fullscreen")
    ap.add_argument("--bench", type=int, metavar="FRAMES",
                    help="render N frames, print timings, exit")
    ap.add_argument("--no-dirty", action="store_true",
                    help="start with full screen redraws, for comparison")
    args = ap.parse_args(argv)

    pygame.init()
    pygame.display.set_caption("Time Trip memory wall")

    flags = 0 if args.windowed or args.bench else pygame.FULLSCREEN
    screen = pygame.display.set_mode(args.size, flags)
    if flags & pygame.FULLSCREEN:
        pygame.mouse.set_visible(False)
    size = screen.get_size()

    wall = TimeTripWall(size)
    wall.dirty_mode = not args.no_dirty

    clock = pygame.time.Clock()
    font = pygame.font.Font(None, max(16, size[1] // 34))
    show_stats = True

    # First frame is always a full one: nothing on screen yet to be dirty
    # against.
    wall.paint(screen, screen.get_rect())
    pygame.display.flip()

    frames = 0
    worst = 0.0
    total = 0.0
    running = True
    while running:
        # tick() both caps the rate and hands back the elapsed milliseconds as
        # an integer, which is exactly the unit the fixed point motion wants.
        dt_ms = clock.tick(60)
        if dt_ms > 100:                 # a stall: do not teleport everything
            dt_ms = 100

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif event.key == pygame.K_d:
                    wall.dirty_mode = not wall.dirty_mode
                    wall.paint(screen, screen.get_rect())
                    pygame.display.flip()
                elif event.key == pygame.K_s:
                    show_stats = not show_stats
                    wall.paint(screen, screen.get_rect())
                    pygame.display.flip()
                elif event.key == pygame.K_f:
                    pygame.display.toggle_fullscreen()

        # perf_counter, not get_ticks: a frame here costs about a
        # millisecond, and get_ticks only resolves to whole milliseconds.
        start = time.perf_counter()
        wall.update(dt_ms)
        dirty = wall.draw(screen)

        if show_stats:
            label = font.render(
                "%5.1f fps   %s   %d streaks" % (
                    clock.get_fps(),
                    "dirty rects" if wall.dirty_mode else "full redraw",
                    len(wall.streaks)),
                True, (150, 235, 255))
            box = label.get_rect(topleft=(12, 10))
            # Repaint the scene under the box rather than just the background,
            # or a streak passing behind the text leaves a hole in itself.
            wall.paint(screen, box)
            screen.blit(label, box)
            if dirty is not None:
                dirty.append(box)

        if dirty is None:
            pygame.display.flip()
        else:
            pygame.display.update(dirty)

        spent = (time.perf_counter() - start) * 1000.0
        total += spent
        worst = max(worst, spent)
        frames += 1

        if args.bench and frames >= args.bench:
            running = False

    if args.bench:
        print("%d frames at %dx%d, %s" % (
            frames, size[0], size[1],
            "dirty rects" if wall.dirty_mode else "full redraw"))
        print("  mean %.2f ms/frame   worst %.2f ms" % (total / frames, worst))
        if wall.dirty_mode:
            print("  last frame: %d rect(s) covering %.0f%% of the screen"
                  % (wall.last_rects,
                     100.0 * wall.last_area / (size[0] * size[1])))

    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
