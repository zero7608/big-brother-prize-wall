#!/usr/bin/env python3
"""Drawing: every state paints, the wall holds still, and memory does not grow.

    python3 tests/test_render.py
"""
import contextlib, io, json, os, sys, time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame                                                    # noqa: E402
import prize_wall as pw                                          # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
fails = []


def check(name, ok, detail=""):
    print("  %-44s %s   %s" % (name, "PASS" if ok else "FAIL", detail))
    if not ok:
        fails.append(name)


def build(w=1280, h=720, **theme):
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    cfg["display"].update(fullscreen=False, width=w, height=h)
    cfg["behaviour"]["state_file"] = None
    cfg["behaviour"]["log_file"] = None
    cfg["theme"].update(theme)
    with contextlib.redirect_stdout(io.StringIO()):
        app = pw.PrizeWall(cfg, simulate=True, windowed=True)
    app.now = 0.0
    return app


def rss():
    return int(open("/proc/self/statm").read().split()[1]) * 4096 // 1048576


# -- every state draws, in both backgrounds ---------------------------------
for style in ("timetrip", "pool"):
    app = build(background_style=style)
    app.draw_background()
    app._field.ready.wait(120)
    drawn = []
    with contextlib.redirect_stdout(io.StringIO()):
        app.draw(); drawn.append("idle")
        app.arm("04:A1:B2:C3"); app.now += 0.2; app.draw(); drawn.append("armed")
        app.begin_spin("04:A1:B2:C3"); app.now += 2; app.draw(); drawn.append("spin")
        app.now += app.spin_seconds; app.finish_spin(); app.draw(); drawn.append("landed")
        app.now += 1; app.begin_reveal(); app.now += 0.2; app.draw(); drawn.append("zoom")
        app.now = app.reveal_until + 0.1; app.draw(); drawn.append("reveal")
        app.back_to_idle(); app.draw(); drawn.append("idle again")
    check("%s: all seven states paint" % style, len(drawn) == 7, ",".join(drawn))
    check("%s: reveal settles to static" % style, app.is_static() is False)

# -- the wall does not rearrange itself under you ---------------------------
app = build()
order = app.order_ids()
moved = 0
with contextlib.redirect_stdout(io.StringIO()):
    for i in range(6):
        app.now += 40
        app.begin_spin("SPIN-%d" % i)
        if app.state != pw.SPINNING:
            break
        app.now += app.spin_seconds; app.finish_spin()
        app.now += 1; app.begin_reveal(); app.now += 8; app.back_to_idle()
        if app.order_ids() != order:
            moved += 1
check("the wall stays put across six spins", moved == 0, "%d moves" % moved)

# -- and memory holds steady -------------------------------------------------
app = build(1920, 1080)
app.draw_background(); app._field.ready.wait(120)
with contextlib.redirect_stdout(io.StringIO()):
    for i in range(200):
        app.now += 0.016; app.draw()
settled = rss()
with contextlib.redirect_stdout(io.StringIO()):
    for k in range(12):
        if k and k % 6 == 0:
            app.reset_night()
        app.arm("SOAK-%d" % k); app.now += 0.5
        app.begin_spin("SOAK-%d" % k)
        for _ in range(120):
            app.now += 0.05; app.draw()
        if app.state != pw.IDLE:
            app.back_to_idle()
grown = rss() - settled
check("memory holds across twelve cycles", grown < 200,
      "%d MB -> %d MB" % (settled, rss()))

print("test_render: %s" % ("PASS" if not fails else "FAIL"))
sys.exit(1 if fails else 0)
