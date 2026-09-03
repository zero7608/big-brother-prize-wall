#!/usr/bin/env python3
"""Hold a tag on the reader and this prints its UID in config.json format.

Use it to fill in the "rigged_tags" section:

    $ python3 scan_uid.py
    04:A1:B2:C3:D4:E5:F6
"""

import json
import os
import time

import reader as reader_mod

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "config.json")) as fh:
    config = json.load(fh)

src = reader_mod.make_reader(config)
known = config.get("rigged_tags", {})
print("Waiting for tags... Ctrl-C to stop.\n")

last, last_time = None, 0.0
try:
    while True:
        uid = src.read()
        now = time.time()
        if uid and not (uid == last and now - last_time < 2.0):
            last, last_time = uid, now
            tag = f"  -> already rigged to '{known[uid]}'" if uid in known else ""
            print(f"{uid}{tag}")
        time.sleep(0.1)
except KeyboardInterrupt:
    src.close()
    print("\nbye")
