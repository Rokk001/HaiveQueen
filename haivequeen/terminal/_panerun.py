"""Pane launcher: records its pid and the pid of the command in a file, runs the command, exits 0.

Usage: python _panerun.py <pidfile> <command> [args...]
Exiting 0 even when the command was killed lets the terminal close the pane by itself.
"""
import os
import subprocess
import sys


def main():
    """Start the command as a child, publish the pids and wait for it."""
    pidfile = sys.argv[1]
    child = subprocess.Popen(sys.argv[2:])
    tmp = pidfile + ".tmp"
    with open(tmp, "w", encoding="ascii") as handle:
        handle.write("%d %d" % (os.getpid(), child.pid))
    os.replace(tmp, pidfile)
    child.wait()
    sys.exit(0)


if __name__ == "__main__":
    main()
