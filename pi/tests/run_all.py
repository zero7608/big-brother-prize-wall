#!/usr/bin/env python3
"""Run every test and report.

    python3 tests/run_all.py
"""
import glob, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
failed = []
for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
    name = os.path.basename(path)
    proc = subprocess.run([sys.executable, path], capture_output=True, text=True)
    ok = proc.returncode == 0
    print("%-18s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        failed.append(name)
        for line in (proc.stdout + proc.stderr).splitlines():
            if "FAIL" in line or "Error" in line or "Traceback" in line:
                print("    %s" % line)
print()
print("%d file(s), %d failed" % (len(glob.glob(os.path.join(HERE, "test_*.py"))),
                                 len(failed)))
sys.exit(1 if failed else 0)
