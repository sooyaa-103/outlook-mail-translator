#!/usr/bin/env python3
"""Configure the free macOS Mail route without asking for any password."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from dotenv import set_key

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--email", help="School email address; prompted in the terminal if omitted")
parser.add_argument("--detect", action="store_true", help="Detect the single NTU account configured in macOS Mail")
args = parser.parse_args()
if args.detect:
    bridge = Path(__file__).with_name("apple_mail_bridge.js")
    result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", str(bridge)],
                            input=json.dumps({"action": "discover"}), capture_output=True, text=True, timeout=180)
    if result.returncode:
        raise SystemExit("Unable to detect accounts. Check that macOS allows your terminal/Python to automate Mail.")
    accounts = json.loads(result.stdout)
    if len(accounts) != 1:
        raise SystemExit("No unique NTU account found. Specify --email or run without arguments for interactive setup.")
    email = accounts[0]["email"]
else:
    email = (args.email or input("Enter your school email address (not your password): ")).strip().lower()
if not re.fullmatch(r"[^\s@'\"\r\n]+@[^\s@'\"\r\n]+\.[^\s@'\"\r\n]+", email):
    parser.error("Invalid email address")
os.umask(0o077)
path = Path(__file__).with_name(".env")
path.touch(mode=0o600, exist_ok=True)
for key, value in {"MAIL_SOURCE": "apple_mail", "TRANSLATION_PROVIDER": "argos", "SCHOOL_EMAIL": email}.items():
    set_key(str(path), key, value)
path.chmod(0o600)
print("Free macOS Mail mode configured. Your email address is stored only in the local .env file.")
