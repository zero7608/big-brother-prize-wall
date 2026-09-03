#!/usr/bin/env python3
"""The rules that decide who wins what.

Checks that a prize is only won once a night, that a rigged key pays out its
own prize on the first tap and falls back to a random one afterwards, that a
rigged prize is held back from walk-up guests, and that the shuffle always
lands on the prize it meant to.

    python3 tests/test_rules.py
"""
import contextlib, io, json, os, sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prize_wall as pw                                          # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = json.load(open(os.path.join(HERE, "config.json")))

# This checks the selection rules, not the artwork or the audio, and it builds
# a few hundred walls in one process.  Every wall loads sixty-odd Sound objects,
# and that many mixer teardowns in one interpreter segfaults at exit about four
# times in ten.  The app makes one wall and is unaffected: loading every cue on
# a single wall did not crash in ten runs.
for _p in BASE["prizes"]:
    _p["image"] = None
    _p["sound"] = None
for _k in ("start_sound", "loop_sound", "reveal_sound", "tick_sound",
           "bell_sound", "armed_sound"):
    BASE["spin"][_k] = None

RIGGED = BASE["rigged_tags"]
NRAND = sum(1 for p in BASE["prizes"] if p["weight"] > 0)


def wall(shuffle=False):
    cfg = json.loads(json.dumps(BASE))
    cfg["display"]["fullscreen"] = False
    cfg["behaviour"]["log_file"] = None
    cfg["behaviour"]["state_file"] = None
    cfg["theme"]["shuffle_wall"] = shuffle
    with contextlib.redirect_stdout(io.StringIO()):
        return pw.PrizeWall(cfg, simulate=True)


def cycle(w, uid):
    """One full turn, returning the prize won (or None if it never started)."""
    with contextlib.redirect_stdout(io.StringIO()):
        w.now += 40
        w.begin_spin(uid)
        if w.state != pw.SPINNING:
            return None
        won = w.winner                      # back_to_idle clears it
        w.now += 10
        w.finish_spin()
        w.now += 1
        w.begin_reveal()
        w.now += 8
        w.back_to_idle()
    return won


def check(shuffle):
    failures = []

    # Every prize at most once, and a plant can still claim theirs at the end.
    w = wall(shuffle)
    seen = []
    for i in range(NRAND):
        won = cycle(w, "WALKUP-%d" % i)
        if won:
            seen.append(won.id)
    if len(seen) != len(set(seen)):
        failures.append("a prize was won twice: %s" % seen)
    for uid, prize_id in RIGGED.items():
        won = cycle(w, uid)
        if won is None or won.id != prize_id:
            failures.append("plant %s did not get %s" % (uid, prize_id))

    # A rigged prize is never handed to a walk-up guest.
    w = wall(shuffle)
    for i in range(NRAND):
        won = cycle(w, "WALKUP-%d" % i)
        if won and won.id in RIGGED.values():
            failures.append("walk-up guest got the reserved %s" % won.id)

    # A rigged key's second tap falls through to the ordinary draw.
    w = wall(shuffle)
    uid, prize_id = next(iter(RIGGED.items()))
    first, second = cycle(w, uid), cycle(w, uid)
    if first is None or first.id != prize_id:
        failures.append("first tap did not pay out %s" % prize_id)
    if second is not None and second.id == prize_id:
        failures.append("second tap paid out %s again" % prize_id)

    # The shuffle lands where it says it lands.
    w = wall(shuffle)
    misses = 0
    for i in range(60):
        with contextlib.redirect_stdout(io.StringIO()):
            w.now += 40
            w.begin_spin("LAND-%d" % i)
            if w.state != pw.SPINNING:
                w.reset_night()
                continue
            target = w.winner
            w.now += w.spin_seconds
            if w.prizes[w.highlight_index()] is not target:
                misses += 1
            w.finish_spin(); w.now += 1; w.begin_reveal()
            w.now += 8; w.back_to_idle()
    if misses:
        failures.append("the shuffle missed its target %d time(s)" % misses)
    return failures


bad = []
for shuffle in (False, True):
    found = check(shuffle)
    print("  shuffle=%-3s %s" % ("on" if shuffle else "off",
                                 "PASS" if not found else "FAIL"))
    for line in found:
        print("      %s" % line)
    bad += found

print("test_rules: %s" % ("PASS" if not bad else "FAIL"))
sys.exit(1 if bad else 0)
