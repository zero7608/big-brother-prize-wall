#!/usr/bin/env python3
"""Turn an Intel HEX build into a UF2, for drag-and-drop flashing.

The Adafruit bootloader on a Nice!Nano presents a drive called NICENANO when
you double-tap reset. Copying a UF2 onto that drive flashes it: no toolchain,
no adafruit-nrfutil, no serial port, no permissions to get wrong. It is by far
the easiest way to put firmware on the board, and the only one that works from
a machine with nothing installed.

    python3 hex2uf2.py .pio/build/nicenano/firmware.hex build/prize_reader.uf2

Written out rather than pulled in because the format is a few dozen lines and
this avoids a dependency for something the build should always be able to do.
"""

import argparse
import struct
import sys

# UF2 header constants, from the format specification.
MAGIC_START0 = 0x0A324655          # "UF2\n"
MAGIC_START1 = 0x9E5D5157
MAGIC_END = 0x0AB16F30
FLAG_FAMILY_ID = 0x00002000
PAYLOAD = 256                      # bytes of real data per 512 byte block

#: Which chip the file is for. The bootloader checks this and refuses a UF2
#: built for anything else, which is what stops a Feather image bricking a
#: different board.
FAMILY = {
    "nrf52840": 0xADA52840,
    "samd51": 0x55114460,
    "rp2040": 0xE48BFF56,
    "esp32c6": 0x540DDF62,
}


def read_hex(path):
    """Intel HEX to {address: byte}, following the extended-address records.

    A hex file addresses in 16 bit chunks and switches banks with type 04 and
    type 02 records. Ignoring those silently produces a file that looks fine
    and flashes to the wrong place, so they are handled rather than skipped.
    """
    memory = {}
    upper = 0
    with open(path) as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or not line.startswith(":"):
                continue
            raw = bytes.fromhex(line[1:])
            count, addr_hi, addr_lo, rec = raw[0], raw[1], raw[2], raw[3]
            data = raw[4:4 + count]
            if (sum(raw) & 0xFF) != 0:
                raise ValueError("bad checksum on line %d" % lineno)
            if rec == 0x00:
                base = upper + (addr_hi << 8) + addr_lo
                for i, byte in enumerate(data):
                    memory[base + i] = byte
            elif rec == 0x01:
                break
            elif rec == 0x04:
                upper = (data[0] << 8 | data[1]) << 16
            elif rec == 0x02:
                upper = (data[0] << 8 | data[1]) << 4
    return memory


def to_uf2(memory, family):
    """Contiguous runs of memory, as 512 byte UF2 blocks."""
    if not memory:
        raise ValueError("nothing to convert: the hex file held no data")

    # Group into aligned chunks so each block covers one 256 byte page.
    pages = {}
    for addr, byte in memory.items():
        page = addr & ~(PAYLOAD - 1)
        pages.setdefault(page, bytearray(PAYLOAD))[addr - page] = byte

    order = sorted(pages)
    blocks = bytearray()
    total = len(order)
    for n, page in enumerate(order):
        chunk = pages[page]
        block = struct.pack("<IIIIIIII", MAGIC_START0, MAGIC_START1,
                            FLAG_FAMILY_ID, page, PAYLOAD, n, total, family)
        block += bytes(chunk) + b"\x00" * (476 - PAYLOAD)
        block += struct.pack("<I", MAGIC_END)
        assert len(block) == 512
        blocks += block
    return blocks, order[0], order[-1] + PAYLOAD, total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("hexfile")
    ap.add_argument("uf2file")
    ap.add_argument("--family", default="nrf52840", choices=sorted(FAMILY))
    args = ap.parse_args(argv)

    memory = read_hex(args.hexfile)
    blocks, first, last, count = to_uf2(memory, FAMILY[args.family])
    with open(args.uf2file, "wb") as fh:
        fh.write(blocks)

    print("%s -> %s" % (args.hexfile, args.uf2file))
    print("  %d bytes across 0x%06X..0x%06X" % (len(memory), first, last))
    print("  %d blocks, %d bytes, family %s (0x%08X)"
          % (count, len(blocks), args.family, FAMILY[args.family]))
    if args.family == "nrf52840" and first < 0x26000:
        print("  ! starts below 0x26000, which is where the SoftDevice ends.")
        print("    Flashing this would overwrite the bootloader.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
