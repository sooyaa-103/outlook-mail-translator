"""Use the user's normally authenticated macOS Mail account; no custom Entra app."""
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
import json
import os
from pathlib import Path
import subprocess
import sys
import time

BRIDGE = Path(__file__).with_name("apple_mail_bridge.js")


class AppleMailClient:
    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("apple_mail mode is supported only on macOS.")
        self.email = os.environ["SCHOOL_EMAIL"].strip().lower()
        self.sync_info = {}

    def call(self, action, **kwargs):
        payload = {"action": action, "email": self.email, **kwargs}
        try:
            result = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript", str(BRIDGE)],
                input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                text=True, timeout=180, check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("Timed out waiting for macOS Mail; check synchronization and system permission dialogs.")
        if result.returncode:
            if "SCHOOL_ACCOUNT_NOT_FOUND" in result.stderr:
                raise RuntimeError("No unique enabled Mail account matches SCHOOL_EMAIL. Add your school Exchange account first.")
            if "-1743" in result.stderr:
                raise RuntimeError("Allow your terminal/Python to control Mail in System Settings > Privacy & Security > Automation.")
            raise RuntimeError("macOS Mail automation failed. Open Mail, confirm account synchronization, and check automation permissions.")
        return json.loads(result.stdout)

    def profile(self):
        profile = self.call("profile")
        return "apple-mail:" + profile["id"], profile["email"]

    def messages(self, days):
        self.refresh()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        for item in self.call("messages", since=since, prefix="【中文译文】"):
            name, address = parseaddr(item.pop("sender"))
            item["from"] = {"emailAddress": {"name": name, "address": address}}
            yield item

    def refresh(self):
        result = self.call("refresh")
        if not result.get("requested"):
            raise RuntimeError("Mail did not confirm the request to fetch school email; synchronization cannot be confirmed this cycle.")
        self.sync_info = {"last_sync_requested_at": datetime.now(timezone.utc).isoformat(),
                          "sync_status": "requested"}
        # Mail's fetch is asynchronous. Give it a short opportunity to sync;
        # slower downloads are picked up by the next poll, not marked handled.
        time.sleep(3)

    def load_body(self, message):
        body = self.call("body", localId=message["localId"])
        message["body"] = {"contentType": "text", "content": body["content"]}
        message["hasAttachments"] = body["hasAttachments"]

    def send(self, email, subject, body):
        if email.strip().lower() != self.email:
            raise RuntimeError("The recipient must be the current school account owner.")
        from bs4 import BeautifulSoup
        # Mail's scripting interface composes text; no literal HTML in phone mail.
        text = BeautifulSoup(body, "html.parser").body.get_text("\n", strip=True)
        result = self.call("send", subject="【中文译文】 " + subject[:180], content=text)
        if not result.get("accepted"):
            raise RuntimeError("Mail did not confirm send submission. Check the Outbox; the script will not resend automatically.")
