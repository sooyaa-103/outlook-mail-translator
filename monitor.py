#!/usr/bin/env python3
"""Install/start/stop/status the native macOS new-mail watcher."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
LABEL = "local.ntu.mail-translator.monitor"
PLIST = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
DOMAIN = f"gui/{os.getuid()}"


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"Command failed: {args[0]}; check your macOS login session and service permissions.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise RuntimeError("Background monitoring is supported only on macOS.")
    os.umask(0o077)
    if args.action == "start":
        python = ROOT / ".venv/bin/python"
        # Stop before initialization: the active watcher owns the database lock.
        # Reusing monitor_since preserves the original activation time.
        run("/bin/launchctl", "bootout", DOMAIN + "/" + LABEL, check=False)
        for attempt in range(10):
            result = run(str(python), str(ROOT / "mail_translator.py"), "--init-monitor", "--new-only", check=False)
            if result.returncode == 0:
                break
            if "Another translation process" not in result.stderr or attempt == 9:
                raise RuntimeError("Monitor initialization failed: " + result.stderr.strip())
            time.sleep(0.5)
        print(result.stdout.strip())
        config = {"Label": LABEL,
                  "ProgramArguments": [str(python), str(ROOT / "mail_translator.py"), "--source", "apple_mail",
                                       "--watch", "--send", "--new-only", "--english-only", "--interval", "60"],
                  "WorkingDirectory": str(ROOT), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
                  "StandardOutPath": str(ROOT / "private/monitor.log"),
                  "StandardErrorPath": str(ROOT / "private/monitor-error.log")}
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        with PLIST.open("wb") as handle:
            plistlib.dump(config, handle)
        run("/bin/launchctl", "bootstrap", DOMAIN, str(PLIST))
        print("Background monitoring started. Checks new English emails every 60 seconds; the terminal can be closed.")
    elif args.action == "stop":
        run("/bin/launchctl", "bootout", DOMAIN + "/" + LABEL, check=False)
        PLIST.unlink(missing_ok=True)
        print("Background monitoring stopped.")
    else:
        result = run("/bin/launchctl", "print", DOMAIN + "/" + LABEL, check=False)
        print("Service is loaded." if result.returncode == 0 else "Service is not loaded.")
        for line in result.stdout.splitlines():
            if line.strip().startswith(("state =", "pid =", "last exit code =")):
                print(line.strip())
        path = ROOT / "private/monitor-status.json"
        if path.exists():
            state = json.loads(path.read_text())
            print("Latest status (UTC):", json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
