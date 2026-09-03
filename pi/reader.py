"""Tag sources for the memory wall.

Two implementations with the same tiny interface:

    src.read()   -> "04:A1:B2:C3" if a tag is present, else None
    src.close()

PN532Reader talks to an AITRIP/Elechouse PN532 V3 board over SPI.
KeyboardReader fakes tags so the show can be built and rehearsed on any
machine (press 1-9 for a rigged tag slot, SPACE for a random unknown tag).
"""

import collections
import random
import threading
import time


def format_uid(raw):
    """Bytes from the PN532 -> the colon-hex string used in config.json."""
    return ":".join(f"{b:02X}" for b in raw)


class PN532Reader:
    def __init__(self, cs_pin="D8", read_timeout=0.5):
        import board
        import busio
        import digitalio
        from adafruit_pn532.spi import PN532_SPI

        spi = busio.SPI(board.SCK, board.MOSI, board.MISO)
        cs = digitalio.DigitalInOut(getattr(board, cs_pin))
        self._pn532 = PN532_SPI(spi, cs, debug=False)

        ic, ver, rev, support = self._pn532.firmware_version
        print(f"PN532 found, firmware {ver}.{rev}")
        self._pn532.SAM_configuration()
        self._read_timeout = read_timeout

    def read(self):
        uid = self._pn532.read_passive_target(timeout=self._read_timeout)
        return None if uid is None else format_uid(uid)

    def close(self):
        pass


class KeyboardReader:
    """Simulator: fake taps typed at the keyboard.

    This does not touch the pygame event queue itself. The main loop owns all
    key handling and hands presses here via feed(), so a simulated tap can
    never be swallowed or double-handled depending on which ran first.
    """

    def __init__(self, rigged_uids):
        self._rigged = list(rigged_uids)
        self._pending = []

    def feed(self, key):
        """Turn a pygame key code into a fake tap. Returns True if it was one."""
        import pygame

        if key == pygame.K_SPACE:
            self._pending.append(
                format_uid(bytes(random.getrandbits(8) for _ in range(4))))
            return True
        if pygame.K_1 <= key <= pygame.K_9:
            idx = key - pygame.K_1
            if idx < len(self._rigged):
                self._pending.append(self._rigged[idx])
                return True
            print(f"  (no rigged key #{idx + 1}: only "
                  f"{len(self._rigged)} are configured)")
        return False

    def discard_pending(self):
        self._pending.clear()

    def read(self):
        return self._pending.pop(0) if self._pending else None

    def close(self):
        pass


class ThreadedReader:
    """Runs a blocking reader on its own thread so it never stalls the show.

    The PN532 blocks for up to `read_timeout` on every poll that finds no tag.
    Called from the render loop that is a hard stall on every idle frame, which
    looks exactly like the display freezing. Here the waiting happens off to
    one side and the main loop just checks a queue, which never blocks.
    """

    def __init__(self, reader, poll_interval=0.05):
        self._reader = reader
        self._poll_interval = poll_interval
        self._queue = collections.deque(maxlen=4)
        self._stop = threading.Event()
        self._errors = 0
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="pn532-reader")
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                uid = self._reader.read()
            except Exception as exc:                  # a wobbly bus, usually
                self._errors += 1
                if self._errors <= 3:
                    print(f"  ! reader error: {exc}")
                elif self._errors == 4:
                    print("  ! further reader errors will not be reported")
                time.sleep(0.5)
                continue
            if uid:
                self._queue.append(uid)
            self._stop.wait(self._poll_interval)

    def read(self):
        return self._queue.popleft() if self._queue else None

    @property
    def errors(self):
        return self._errors

    def close(self):
        self._stop.set()
        self._thread.join(timeout=1.5)
        self._reader.close()


class _LinkCore:
    """The protocol, with no opinion about how the bytes arrive.

    The reader and the button are a single wireless unit, so they cannot each
    own a connection: the link is shared and demultiplexed here, with
    `BluetoothReader` and `BluetoothButton` as thin views onto it.  Everything
    about the wire lives in a subclass, so adding a transport is a subclass
    rather than a copy of the parsing.

    Lines from the wireless unit:

        V <text>          identifies itself, once, on connect
        T <uid>           a tag is on the reader, repeated while it stays there
        B                 the button was pressed
        H                 heartbeat, so a dead link is distinguishable from a
                          quiet one

    `T` repeating is not chattiness, it is the contract.  The wall decides a
    key has been taken off the pad by not having seen it for a while, which is
    what stops one guest taking a second turn while their key sits there.  A
    unit that announced each tag once would break that, and the failure would
    look like the wall handing out extra prizes rather than like a protocol
    bug.
    """

    def __init__(self, reconnect=3.0):
        self._reconnect = reconnect
        self._tags = collections.deque(maxlen=4)
        self._buttons = collections.deque(maxlen=2)
        self._stop = threading.Event()
        self._last_line = 0.0
        self._identified = None
        self._fault = None
        self._errors = 0
        self._partial = b""

    def _start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="wireless-link")
        self._thread.start()

    def feed(self, chunk):
        """Bytes off the wire, however they got here.

        Split on the line ending, and keep any partial line for the next chunk
        rather than throwing it away: BLE delivers in packets that fall wherever
        they like, and a serial read returns whatever happened to be buffered.
        """
        self._partial += chunk
        while b"\n" in self._partial:
            line, self._partial = self._partial.split(b"\n", 1)
            self._handle(line.strip().decode("ascii", "replace"))

    def _handle(self, line):
        if not line:
            return
        self._last_line = time.time()
        kind, _, rest = line.partition(" ")
        if kind == "T" and rest:
            self._tags.append(rest.strip().upper())
        elif kind == "B":
            self._buttons.append(True)
        elif kind == "V":
            self._identified = rest.strip()
            print(f"  wireless unit: {self._identified}")
        elif kind == "E":
            # The unit is alive but cannot do its job. Worth saying out loud:
            # without it, a broken reader and a unit out of range look
            # identical from here, which is a bad half hour at a party.
            fault = rest.strip()
            if fault != self._fault:
                self._fault = fault
                print(f"  ! wireless unit reports: {fault}")
        # H is a heartbeat and needs nothing beyond the timestamp above.

    # -- what the two views need --------------------------------------------

    def read_tag(self):
        return self._tags.popleft() if self._tags else None

    def take_button(self):
        if self._buttons:
            self._buttons.clear()
            return True
        return False

    def clear_button(self):
        self._buttons.clear()

    @property
    def connected(self):
        return time.time() - self._last_line < 10.0

    def close(self):
        self._stop.set()
        self._thread.join(timeout=2.0)


class SerialLink(_LinkCore):
    """A unit on a serial port: Bluetooth Classic bound to rfcomm, or USB.

    Needs a chip with Bluetooth Classic, which rules out most recent ESP32s.
    For a BLE-only board use BleLink.
    """

    def __init__(self, port, baud=115200, timeout=0.2, reconnect=3.0):
        super().__init__(reconnect=reconnect)
        self._port_name = port
        self._baud = baud
        self._timeout = timeout
        self._serial = None
        self._start()

    def _open(self):
        import serial                      # pyserial, only needed on the Pi

        self._serial = serial.Serial(self._port_name, self._baud,
                                     timeout=self._timeout)
        self._last_line = time.time()
        print(f"Wireless unit on {self._port_name}")

    def _loop(self):
        while not self._stop.is_set():
            if self._serial is None:
                try:
                    self._open()
                except Exception as exc:
                    self._errors += 1
                    if self._errors <= 2:
                        print(f"  ! no wireless unit on {self._port_name}: {exc}")
                    elif self._errors == 3:
                        print("  ! further link errors will not be reported")
                    self._stop.wait(self._reconnect)
                    continue
            try:
                chunk = self._serial.read(64)
            except Exception as exc:
                print(f"  ! wireless link dropped ({exc}); reconnecting")
                self._close_port()
                self._stop.wait(self._reconnect)
                continue

            if chunk:
                self.feed(chunk)
            elif time.time() - self._last_line > 10.0:
                # Not even a heartbeat: the unit is gone or asleep.
                print("  ! wireless unit has gone quiet; reconnecting")
                self._close_port()
                self._stop.wait(self._reconnect)

    def _close_port(self):
        try:
            if self._serial is not None:
                self._serial.close()
        except Exception:
            pass
        self._serial = None

    @property
    def connected(self):
        return self._serial is not None and super().connected

    def close(self):
        super().close()
        self._close_port()


class BleLink(_LinkCore):
    """A unit over Bluetooth Low Energy, using the Nordic UART Service.

    Needed because most current chips have no Bluetooth Classic and therefore
    no serial profile: the ESP32-C3, S3 and C6 are BLE only, as is the
    nRF52840.  Only the older Xtensa ESP32 can do rfcomm.

    NUS is the convention for "a serial port over BLE": one characteristic the
    device notifies on, one the host writes to.  The bytes and the line
    protocol are identical to the serial transport, so everything above this
    class is unchanged.

    bleak is asynchronous and the rest of the show is not, so the event loop
    lives on its own thread and hands lines to the same parser.
    """

    NUS = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
    TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"      # device notifies us
    RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"      # we write to the device

    def __init__(self, address=None, name="PrizeShack", reconnect=3.0):
        super().__init__(reconnect=reconnect)
        # An address is accepted but rarely useful: the board advertises with a
        # random privacy address that BlueZ will only connect to if it has just
        # seen it in a scan, so connecting to a remembered string fails with
        # "device not found". Leaving this empty and searching is the reliable
        # path.
        self._address = address
        self._name = name
        self._client = None
        self._start()

    def _loop(self):
        import asyncio

        asyncio.run(self._run())

    async def _run(self):
        import asyncio

        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            print("  ! BLE needs bleak: pip install bleak")
            return

        while not self._stop.is_set():
            try:
                target = self._address
                if not target:
                    target = await self._look(BleakScanner)

                async with BleakClient(target) as client:
                    self._client = client
                    self._last_line = time.time()
                    print(f"Wireless unit over BLE ({self._name})")
                    await client.start_notify(
                        self.TX, lambda _c, data: self.feed(bytes(data)))
                    while not self._stop.is_set() and client.is_connected:
                        await asyncio.sleep(0.2)
                        if time.time() - self._last_line > 10.0:
                            print("  ! unit has gone quiet; reconnecting")
                            break
            except Exception as exc:
                self._errors += 1
                if self._errors <= 2:
                    print(f"  ! BLE link: {exc}")
                elif self._errors == 3:
                    print("  ! further BLE errors will not be reported")
            finally:
                self._client = None
            if not self._stop.is_set():
                await asyncio.sleep(self._reconnect)

    async def _look(self, scanner_cls, seconds=10.0):
        """Find the unit, by the service it offers rather than by its name.

        Two reasons it is not done by name.  The board advertises its name in
        the scan response, and plenty of adapters do not resolve that: on one
        laptop here the unit showed up with an empty name and only its service
        uuid, so a name search would never have matched something that was
        sitting right there advertising.  And the name is a string anyone can
        change, while the service uuid is what actually makes it this device.

        Watching continuously rather than taking a snapshot, because the
        advertising window can fall between two scans.
        """
        import asyncio

        hit = asyncio.get_event_loop().create_future()

        def seen(device, advert):
            if hit.done():
                return
            uuids = [u.lower() for u in (advert.service_uuids or [])]
            named = (advert.local_name or device.name or "").lower()
            if self.NUS in uuids or named == self._name.lower():
                hit.set_result(device)

        async with scanner_cls(seen):
            try:
                return await asyncio.wait_for(hit, timeout=seconds)
            except asyncio.TimeoutError:
                raise RuntimeError(
                    "no unit offering the Nordic UART Service, and none named "
                    "%r" % self._name)

    @property
    def connected(self):
        return self._client is not None and super().connected


class BluetoothReader:
    """The tag half of a wireless unit."""

    def __init__(self, link):
        self._link = link

    def read(self):
        return self._link.read_tag()

    def close(self):
        pass                     # the link is shared; whoever made it closes it


class BluetoothButton:
    """The button half of a wireless unit."""

    def __init__(self, link):
        self._link = link

    def pressed(self):
        return self._link.take_button()

    def clear(self):
        self._link.clear_button()

    def close(self):
        pass


class GPIOButton:
    """A momentary switch on a GPIO pin, wired to ground.

    gpiozero delivers presses on its own thread, so this never blocks the
    render loop — presses land in a queue the main loop checks.
    """

    def __init__(self, pin=17, pull_up=True, bounce_seconds=0.05):
        from gpiozero import Button as GZButton

        self._queue = collections.deque(maxlen=2)
        self._button = GZButton(pin, pull_up=pull_up, bounce_time=bounce_seconds)
        self._button.when_pressed = lambda: self._queue.append(True)
        print(f"Button on GPIO {pin} ({'pull-up' if pull_up else 'pull-down'})")

    def pressed(self):
        """True if the button was pushed since the last check."""
        if self._queue:
            self._queue.clear()
            return True
        return False

    def clear(self):
        self._queue.clear()

    def close(self):
        self._button.close()


class KeyboardButton:
    """ENTER standing in for the physical button."""

    # ENTER only: SPACE already means "tap a key" in the simulator.
    KEYS = ("K_RETURN", "K_KP_ENTER")

    def __init__(self):
        self._pressed = False

    def feed(self, key):
        import pygame

        if key in tuple(getattr(pygame, name) for name in self.KEYS):
            self._pressed = True
            return True
        return False

    def pressed(self):
        was, self._pressed = self._pressed, False
        return was

    def clear(self):
        self._pressed = False

    def close(self):
        pass


def make_sources(config, simulate=False):
    """The tag source and the button, built together.

    Together rather than separately because they are not always separate
    things.  A wireless unit is one ESP32 sending both over one Bluetooth
    link, and opening that link twice would be wrong; wired, they genuinely
    are two devices.  Returning both from one call is what lets the wireless
    case share a link without the caller having to know.
    """
    if simulate:
        return KeyboardReader(config["rigged_tags"].keys()), KeyboardButton()

    rcfg = config.get("reader", {})
    bcfg = config.get("button", {})

    kind = str(rcfg.get("type", "pn532")).lower()
    if kind in ("ble", "bluetooth-le"):
        link = BleLink(
            address=rcfg.get("address") or None,
            name=rcfg.get("name", "PrizeShack"),
            reconnect=float(rcfg.get("reconnect_seconds", 3.0)),
        )
    elif kind in ("bluetooth", "wireless", "serial"):
        link = SerialLink(
            port=rcfg.get("port", "/dev/rfcomm0"),
            baud=int(rcfg.get("baud", 115200)),
            reconnect=float(rcfg.get("reconnect_seconds", 3.0)),
        )
    else:
        link = None

    if link is not None:
        reader = BluetoothReader(link)
        # The unit carries the button too unless one is wired to the Pi as well.
        if str(bcfg.get("type", "wireless")).lower() in ("bluetooth", "wireless",
                                                         "serial", "ble"):
            return reader, BluetoothButton(link)
        return reader, _wired_button(bcfg)

    reader = ThreadedReader(
        PN532Reader(
            cs_pin=rcfg.get("cs_pin", "D8"),
            read_timeout=rcfg.get("read_timeout", 0.5),
        ),
        poll_interval=rcfg.get("poll_interval", 0.05),
    )
    return reader, _wired_button(bcfg)


def _wired_button(bcfg):
    try:
        return GPIOButton(
            pin=bcfg.get("pin", 17),
            pull_up=bcfg.get("pull_up", True),
            bounce_seconds=bcfg.get("bounce_seconds", 0.05),
        )
    except Exception as exc:
        print(f"  ! no GPIO button ({exc})")
        print("    ENTER on the keyboard will start a spin instead.")
        return KeyboardButton()


# The two older entry points, kept so nothing that calls them has to change.
# They build their own halves, which is correct for wired hardware and wasteful
# for wireless, so prefer make_sources.

def make_button(config, simulate=False):
    return make_sources(config, simulate)[1]


def make_reader(config, simulate=False):
    return make_sources(config, simulate)[0]
