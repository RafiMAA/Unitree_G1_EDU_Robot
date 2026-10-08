#!/usr/bin/env python3
"""Watch the console owner and clean up this isolated process group on exit."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def group_alive(group):
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] != 'Z' and int(fields[2]) == group:
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False


def main():
    owner = int(sys.argv[1])
    group = os.getpid()
    if os.getpgrp() != group:
        raise SystemExit('owned_process requires an isolated process group')
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    # Do not start a service if the console died before this watchdog started.
    if os.getppid() != owner or stopping:
        return 0
    child = subprocess.Popen(sys.argv[2:])
    try:
        while not stopping and os.getppid() == owner and child.poll() is None:
            time.sleep(.1)
    finally:
        for sig, seconds in ((signal.SIGINT, 8), (signal.SIGTERM, 3)):
            if not group_alive(group):
                break
            os.killpg(group, sig)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline and group_alive(group):
                child.poll()
                time.sleep(.05)
        if group_alive(group):
            os.killpg(group, signal.SIGKILL)
        child.wait()
    return child.returncode if child.returncode >= 0 else 128 - child.returncode


if __name__ == '__main__':
    sys.exit(main())
