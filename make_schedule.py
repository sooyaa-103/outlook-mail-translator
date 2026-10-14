#!/usr/bin/env python3
"""Generate a macOS launchd configuration without installing or starting it."""
import argparse
from pathlib import Path
import plistlib
import sys

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--hour", type=int, default=20)
parser.add_argument("--minute", type=int, default=0)
parser.add_argument("--source", choices=["apple_mail", "graph"], default="apple_mail")
args = parser.parse_args()
if not 0 <= args.hour <= 23 or not 0 <= args.minute <= 59:
    parser.error("Invalid time")
python = root / ".venv/bin/python"
if not python.exists():
    sys.exit("Create .venv and install dependencies as described in README first.")
(root / "private").mkdir(mode=0o700, exist_ok=True)
config = {
    "Label": "local.ntu.mail-translator",
    "ProgramArguments": [str(python), str(root / "mail_translator.py"), "--source", args.source, "--send", "--limit", "200"],
    "WorkingDirectory": str(root),
    "StartCalendarInterval": {"Hour": args.hour, "Minute": args.minute},
    "StandardOutPath": str(root / "private/schedule.log"),
    "StandardErrorPath": str(root / "private/schedule-error.log"),
}
path = root / "local.ntu.mail-translator.plist"
with path.open("wb") as handle:
    plistlib.dump(config, handle)
print(f"Generated {path}\nRuns daily at {args.hour:02}:{args.minute:02} in the Mac system timezone. See README for installation.")
