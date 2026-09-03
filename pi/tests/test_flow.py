#!/usr/bin/env python3
"""The state machine: key, greeting, button, spin, reveal, idle.

Also the two rules that only exist because real hardware behaves unlike the
simulator: a key rests on the reader for the whole turn and must not get a
second one, and the button does nothing until the announcer has finished
speaking.

    python3 tests/test_flow.py
"""
import contextlib, io, json, os, sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prize_wall as pw                                          # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RestingKey:
    """A tag sitting on the reader, seen through ThreadedReader.

    The PN532 reports the tag on every poll for as long as it is there, and
    the queue in front of it hands the main loop one sighting per poll.  The
    simulator's keyboard reader fires once per keypress, which is why it can
    never show the bug this stands in for.
    """
    POLL = 0.05

    def __init__(self, uid, clock):
        self.uid, self.present, self._clock, self._last = uid, True, clock, -1e9

    def read(self):
        now = self._clock()
        if self.present and now - self._last >= self.POLL:
            self._last = now
            return self.uid
        return None

    def close(self):
        pass


class Presser:
    def __init__(self):
        self.queued = False

    def push(self):
        self.queued = True

    def pressed(self):
        was, self.queued = self.queued, False
        return was

    def clear(self):
        self.queued = False

    def close(self):
        pass


def build(resting_uid=None):
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    cfg["display"].update(fullscreen=False, width=640, height=480)
    cfg["behaviour"]["state_file"] = None
    cfg["behaviour"]["log_file"] = None
    with contextlib.redirect_stdout(io.StringIO()):
        app = pw.PrizeWall(cfg, simulate=True, windowed=True)
    app.now = 0.0
    app.keypad = None
    app.button = Presser()
    app.key_button = Presser()
    app.reader = (RestingKey(resting_uid, lambda: app.now) if resting_uid else
                  type("R", (), {"read": lambda s: None,
                                 "close": lambda s: None})())
    return app


def step(app, press_when_asked=False):
    """One turn of run()'s state machine, without the event loop."""
    fresh = app.poll_keys()
    if app.state in (pw.IDLE, pw.ARMED, pw.EXHAUSTED):
        armed = None
        if fresh:
            with contextlib.redirect_stdout(io.StringIO()):
                app.arm(fresh[0])
            armed = fresh[0]
        if app.state == pw.ARMED:
            if app.now < app.speaking_until:
                pass                       # the announcer is still talking
            elif app.button_pressed():
                with contextlib.redirect_stdout(io.StringIO()):
                    app.begin_spin(app.armed_uid)
            elif press_when_asked:
                app.button.push()
            elif app.now - app.state_since >= app.armed_timeout:
                with contextlib.redirect_stdout(io.StringIO()):
                    app.back_to_idle()
        return armed
    with contextlib.redirect_stdout(io.StringIO()):
        if app.state == pw.SPINNING and app.now - app.state_since >= app.spin_seconds:
            app.finish_spin()
        elif app.state == pw.LANDED and app.now - app.state_since >= app.land_pause:
            app.begin_reveal()
        elif app.state == pw.REVEAL and app.now >= app.reveal_until:
            app.back_to_idle()
    return None


def run(app, seconds, dt=0.05, press=False, lift_at=None, replace_at=None):
    arms, end = [], app.now + seconds
    while app.now < end:
        app.now += dt
        if lift_at is not None and app.now >= lift_at:
            app.reader.present = False
        if replace_at is not None and app.now >= replace_at:
            app.reader.present = True
        if step(app, press_when_asked=press):
            arms.append(round(app.now, 2))
    return arms


fails = []


def check(name, got, want):
    ok = got == want
    print("  %-46s %s   (%s)" % (name, "PASS" if ok else "FAIL", got))
    if not ok:
        fails.append("%s: got %r want %r" % (name, got, want))


# -- a key left on the reader ------------------------------------------------
app = build("04:AA:BB:CC")
check("key never lifted, 90s -> one turn",
      len(run(app, 90.0, press=True)), 1)

app = build("04:AA:BB:CC")
check("lifted 10s, replaced -> two turns",
      len(run(app, 90.0, press=True, lift_at=30.0, replace_at=40.0)), 2)

app = build("04:AA:BB:CC")
check("lifted briefly, under the lockout -> one turn",
      len(run(app, 90.0, press=True, lift_at=30.0, replace_at=31.0)), 1)

# -- the button waits for the announcer --------------------------------------
app = build()
with contextlib.redirect_stdout(io.StringIO()):
    app.arm("04:A1:B2:C3")
greeting = app.speaking_until - app.now
app.button.push()                               # mashed immediately
started = None
while app.now < greeting + 3.0 and started is None:
    app.now += 0.02
    before = app.state
    step(app)
    if before != app.state:
        started = round(app.now, 2)
check("greeting is not talked over",
      started is not None and started >= greeting - 0.02, True)
check("the early press is held, not discarded",
      started is not None and started <= greeting + 0.15, True)

app = build()
with contextlib.redirect_stdout(io.StringIO()):
    app.arm("04:A1:B2:C3")
app.now = app.speaking_until + 0.01
app.button.push()
step(app)
check("a press after the greeting starts the spin", app.state, pw.SPINNING)

# -- the reveal waits for the line to finish ---------------------------------
# A fixed hold cut long announcements off mid-sentence: Zingbot's joke runs to
# eleven seconds against a six second hold. The hold is now whatever the clips
# actually take, so a longer line needs no other change.
import glob                                                      # noqa: E402

app = build()
lengths = {}
for pid in ("veto", "zing"):
    prize = app.by_id.get(pid)
    if prize is None or prize.sound is None:
        continue
    with contextlib.redirect_stdout(io.StringIO()):
        app.now = 0.0
        app.arm("04:A1:B2:C3")
        app.winner = prize
        app.begin_reveal()
    spoken = prize.sound.get_length()
    if prize.opener != "none":
        op = app.opener_sound(prize.opener, app.armed_name)
        if op is not None:
            spoken += op.get_length()
    lengths[pid] = (spoken, app.reveal_until - app.now)

for pid, (spoken, held) in lengths.items():
    check("%s: held past the end of the line" % pid, held >= spoken, True)
    check("%s: and not wildly past it" % pid, held <= spoken + 4.0, True)
if len(lengths) == 2 and lengths["zing"][0] > lengths["veto"][0]:
    check("the longer line is held longer",
          lengths["zing"][1] > lengths["veto"][1], True)

print("reveal hold: " + "  ".join(
    "%s spoken %.1fs held %.1fs" % (k, v[0], v[1]) for k, v in lengths.items()))
print("test_flow: %s" % ("PASS" if not fails else "FAIL"))
sys.exit(1 if fails else 0)
