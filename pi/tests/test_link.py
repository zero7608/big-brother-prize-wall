#!/usr/bin/env python3
"""The wireless link: parsing, sharing, and surviving a drop.

Runs anywhere. There is no ESP32 and no Bluetooth here: a fake serial port
stands in, which is the only way to test a dropped link and a half-received
line without unplugging real hardware at the right moment.

    python3 tests/test_link.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reader as reader_mod                                      # noqa: E402

fails = []


def check(name, got, want):
    ok = got == want
    print("  %-52s %s   (%s)" % (name, "PASS" if ok else "FAIL", got))
    if not ok:
        fails.append("%s: got %r want %r" % (name, got, want))


class FakePort:
    """A serial port that yields whatever bytes it is given.

    `feed` queues bytes to be returned in chunks of whatever size `read` asks
    for, so a line can be delivered in pieces exactly the way a real link
    delivers it. `die` makes the next read raise, standing in for the unit
    going out of range.
    """

    def __init__(self):
        self.buf = b""
        self.dead = False
        self.closed = False

    def feed(self, data):
        self.buf += data

    def die(self):
        self.dead = True

    def read(self, n):
        if self.dead:
            raise OSError("link dropped")
        chunk, self.buf = self.buf[:n], self.buf[n:]
        if not chunk:
            time.sleep(0.01)
        return chunk

    def close(self):
        self.closed = True


PORTS = []


def link_on(fake):
    """A SerialLink wired to a fake port instead of pyserial."""
    PORTS.append(fake)
    link = reader_mod.SerialLink.__new__(reader_mod.SerialLink)
    link._port_name = "fake"
    link._baud = 115200
    link._timeout = 0.2
    link._reconnect = 0.05
    link._serial = fake
    link._tags = reader_mod.collections.deque(maxlen=4)
    link._buttons = reader_mod.collections.deque(maxlen=2)
    link._stop = reader_mod.threading.Event()
    link._last_line = time.time()
    link._identified = None
    link._errors = 0
    link._partial = b""
    link._thread = reader_mod.threading.Thread(target=link._loop, daemon=True)
    link._thread.start()
    return link


def settle(seconds=0.25):
    time.sleep(seconds)


# -- parsing -----------------------------------------------------------------
fake = FakePort()
link = link_on(fake)
reader = reader_mod.BluetoothReader(link)
button = reader_mod.BluetoothButton(link)

fake.feed(b"V PrizeShack pn532 esp32\n")
settle()
check("the unit identifies itself", link._identified, "PrizeShack pn532 esp32")

fake.feed(b"T 04:A1:B2:C3\n")
settle()
check("a tag arrives", reader.read(), "04:A1:B2:C3")
check("and is not delivered twice", reader.read(), None)

fake.feed(b"t 04:aa:bb:cc\n")
settle()
check("lowercase is normalised", reader.read(), None)   # 't' is not 'T'
fake.feed(b"T 04:aa:bb:cc\n")
settle()
check("a lowercase uid is upper-cased", reader.read(), "04:AA:BB:CC")

# -- a line split across two reads -------------------------------------------
fake.feed(b"T 04:11:22")
settle(0.1)
check("half a line is not acted on", reader.read(), None)
fake.feed(b":33\nB\n")
settle()
check("the rest completes it", reader.read(), "04:11:22:33")
check("and the button behind it still lands", button.pressed(), True)
check("a press is consumed once", button.pressed(), False)

# -- the button --------------------------------------------------------------
fake.feed(b"B\nB\nB\n")
settle()
check("a burst of presses counts once", button.pressed(), True)
fake.feed(b"B\n")
settle()
link.clear_button()
check("clear() drops a pending press", button.pressed(), False)

# -- heartbeats and health ---------------------------------------------------
fake.feed(b"H\n")
settle()
check("a heartbeat leaves no tag", reader.read(), None)
check("the link reports itself connected", link.connected, True)

# -- a drop ------------------------------------------------------------------
fake.die()
settle(0.4)
check("a dropped port is let go of", link._serial is None, True)
link.close()

# -- the two halves share one link -------------------------------------------
cfg = {
    "rigged_tags": {},
    "reader": {"type": "bluetooth", "port": "/dev/does-not-exist"},
    "button": {"type": "wireless"},
}
r, b = reader_mod.make_sources(cfg, simulate=False)
check("wireless reader and button share one link",
      isinstance(r, reader_mod.BluetoothReader)
      and isinstance(b, reader_mod.BluetoothButton)
      and r._link is b._link, True)
r._link.close()

# A missing port must not take the show down: the wall should still start and
# simply never see a tag.
cfg["reader"]["port"] = "/dev/nope"
r2, b2 = reader_mod.make_sources(cfg, simulate=False)
check("a missing port does not raise", r2.read(), None)
r2._link.close()

# -- the BLE transport shares the same parser --------------------------------
# No radio here, so this checks the part that is testable without one: that a
# BLE link demultiplexes identically, since only the transport differs.
ble = reader_mod.BleLink.__new__(reader_mod.BleLink)
reader_mod._LinkCore.__init__(ble, reconnect=0.05)
ble._client = None
ble.feed(b"V unit ble\nT 04:DE:AD:BE\nB\n")
check("BLE: identifies", ble._identified, "unit ble")
check("BLE: a tag arrives", ble.read_tag(), "04:DE:AD:BE")
check("BLE: the button arrives", ble.take_button(), True)
ble.feed(b"T 04:CA:FE")
check("BLE: a split packet waits", ble.read_tag(), None)
ble.feed(b":01\n")
check("BLE: and completes on the next", ble.read_tag(), "04:CA:FE:01")

cfg2 = {"rigged_tags": {}, "reader": {"type": "ble", "name": "NoSuchUnit"},
        "button": {"type": "ble"}}
r3, b3 = reader_mod.make_sources(cfg2, simulate=False)
check("a BLE unit that is not there does not raise", r3.read(), None)
check("and its button is the same link", r3._link is b3._link, True)
r3._link.close()

# -- a unit that cannot do its job says so --------------------------------
# It used to stop dead in setup() and blink, which is invisible from here: a
# broken reader and a unit out of range looked identical.
ble2 = reader_mod.BleLink.__new__(reader_mod.BleLink)
reader_mod._LinkCore.__init__(ble2, reconnect=0.05)
ble2._client = None
ble2.feed(b"E pn532 not found, check the dip switches are set to i2c\n")
check("a fault is picked up", ble2._fault,
      "pn532 not found, check the dip switches are set to i2c")
ble2.feed(b"H\nT 04:01:02:03\n")
check("and the link keeps working after one", ble2.read_tag(), "04:01:02:03")

# -- a line split across BLE packets ---------------------------------------
# A notification carries twenty bytes by default, so the unit sends long lines
# in pieces. "T " plus a seven byte uid is twenty-three, which is what NTAG and
# Ultralight tags carry, so this is the normal case rather than an edge one.
ble3 = reader_mod.BleLink.__new__(reader_mod.BleLink)
reader_mod._LinkCore.__init__(ble3, reconnect=0.05)
ble3._client = None
line = b"T 04:A1:B2:C3:D4:E5:F6\n"
for i in range(0, len(line), 20):
    ble3.feed(line[i:i + 20])
check("a seven byte uid survives chunking", ble3.read_tag(), "04:A1:B2:C3:D4:E5:F6")

long_fault = b"E pn532 not found, check the dip switches are set to i2c\n"
for i in range(0, len(long_fault), 20):
    ble3.feed(long_fault[i:i + 20])
check("and so does a long fault line", ble3._fault,
      "pn532 not found, check the dip switches are set to i2c")

# -- a dropped chunk must not look like a valid short line ------------------
# Real hardware dropped exactly one twenty byte chunk out of a fifty-seven byte
# line, and the result still ended in a newline: "pn532 not found, cs are set
# to i2c". Nothing on this side can tell that from a line the unit meant to
# send, which is why the fix belongs in the firmware. What is checked here is
# that the reassembly itself never invents or loses a line.
ble4 = reader_mod.BleLink.__new__(reader_mod.BleLink)
reader_mod._LinkCore.__init__(ble4, reconnect=0.05)
ble4._client = None
whole = b"T 04:A1:B2:C3\nB\nT 04:11:22:33\n"
for i in range(0, len(whole), 7):          # split at sizes that straddle lines
    ble4.feed(whole[i:i + 7])
check("every line survives an unaligned split",
      (ble4.read_tag(), ble4.take_button(), ble4.read_tag()),
      ("04:A1:B2:C3", True, "04:11:22:33"))
check("and nothing is left over", ble4.read_tag(), None)

print("test_link: %s" % ("PASS" if not fails else "FAIL"))
for line in fails:
    print("   %s" % line)
sys.exit(1 if fails else 0)
