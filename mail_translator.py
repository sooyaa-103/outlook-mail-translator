#!/usr/bin/env python3
"""Translate Outlook email into Chinese. Preview locally by default; --send sends only to your own account."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import html
import json
import os
import re
from pathlib import Path
import sqlite3
import sys
import time
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr, parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PRIVATE = ROOT / "private"
OUTPUT = ROOT / "output"
GRAPH = "https://graph.microsoft.com/v1.0"
PREFIX = "【中文译文】"


class ApiError(RuntimeError):
    def __init__(self, service, status):
        self.status = status
        super().__init__(f"{service} HTTP {status}. For 401, sign in again; for 403, check school permissions; 429 indicates rate limiting.")


def request_json(method, url, *, service, retry=True, **kwargs):
    import requests
    for attempt in range(4):
        response = requests.request(method, url, timeout=(10, 60), **kwargs)
        if retry and response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            try:
                delay = float(response.headers.get("Retry-After", 2 ** attempt))
            except ValueError:
                delay = 2 ** attempt
            time.sleep(min(max(delay, 1), 60))
            continue
        if not 200 <= response.status_code < 300:
            # Omit response bodies to avoid leaking email content or credentials into error logs.
            raise ApiError(service, response.status_code)
        return response.json() if response.content else None


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)


class GraphClient:
    def __init__(self, send=False, interactive=False, device=False):
        import msal
        self.scopes = ["User.Read", "Mail.Read"] + (["Mail.Send"] if send else [])
        self.cache = msal.SerializableTokenCache()
        self.cache_file = PRIVATE / "token-cache.json"
        if self.cache_file.exists():
            self.cache.deserialize(self.cache_file.read_text(encoding="utf-8"))
        self.app = msal.PublicClientApplication(
            os.environ["MS_CLIENT_ID"],
            authority="https://login.microsoftonline.com/" + os.getenv("MS_TENANT_ID", "organizations"),
            token_cache=self.cache,
        )
        self.interactive = interactive
        self.device = device

    def token(self):
        accounts = self.app.get_accounts(username=os.environ["SCHOOL_EMAIL"])
        result = self.app.acquire_token_silent(self.scopes, account=accounts[0]) if accounts else None
        try:
            if not result or "access_token" not in result:
                if not self.interactive:
                    raise RuntimeError("Sign-in required: run python mail_translator.py --login (add --send for automatic sending).")
                if self.device:
                    flow = self.app.initiate_device_flow(scopes=self.scopes)
                    if "user_code" not in flow:
                        raise RuntimeError("Unable to start device sign-in; check the app registration and school policies.")
                    print(flow["message"], flush=True)
                    result = self.app.acquire_token_by_device_flow(flow)
                else:
                    result = self.app.acquire_token_interactive(
                        scopes=self.scopes, login_hint=os.environ["SCHOOL_EMAIL"], timeout=180,
                    )
            if not result or "access_token" not in result:
                code = (result or {}).get("error", "login_failed")
                raise RuntimeError(f"Microsoft sign-in failed: {code}. Contact your school IT team if administrator approval is required.")
            return result["access_token"]
        finally:
            if self.cache.has_state_changed:
                atomic_write(self.cache_file, self.cache.serialize())

    def call(self, method, path, **kwargs):
        url = path if path.startswith("https://") else GRAPH + path
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com":
            raise RuntimeError("Refusing to send an access token to a non-Microsoft Graph address.")
        return request_json(method, url, service="Microsoft Graph", headers={
            "Authorization": "Bearer " + self.token(),
            "Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"',
        }, **kwargs)

    def profile(self):
        profile = self.call("GET", "/me?$select=id,mail,userPrincipalName")
        email = profile.get("mail") or profile.get("userPrincipalName")
        expected = os.environ["SCHOOL_EMAIL"].strip().lower()
        if not email or expected not in {
            (profile.get("mail") or "").lower(),
            (profile.get("userPrincipalName") or "").lower(),
        }:
            raise RuntimeError("The signed-in account does not match SCHOOL_EMAIL. Check your configuration and account.")
        return profile["id"], email

    def messages(self, days):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        path = "/me/mailFolders/inbox/messages"
        params = {"$filter": f"receivedDateTime ge {since}", "$orderby": "receivedDateTime asc",
                  "$top": 30, "$select": "id,subject,from,receivedDateTime,body,webLink,hasAttachments"}
        while path:
            page = self.call("GET", path, params=params)
            yield from page.get("value", [])
            path = page.get("@odata.nextLink")
            params = None

    def send(self, email, subject, body):
        # sendMail has no idempotency key; disable retries to avoid duplicate mail after timeouts.
        self.call("POST", "/me/sendMail", retry=False, json={"message": {
            "subject": PREFIX + " " + subject[:180],
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": [{"emailAddress": {"address": email}}],
            "internetMessageHeaders": [{"name": "x-ntu-chinese-translation", "value": "1"}],
        }, "saveToSentItems": True})


def plain_body(message):
    body = message.get("body") or {}
    content = body.get("content", "")
    if body.get("contentType", "text").lower() == "html":
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        # Preserve original links without loading email images or external resources.
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if urlsplit(href).scheme in ("https", "http", "mailto"):
                link.append(" (" + href + ")")
        content = soup.get_text("\n", strip=True)
    return content


def chunks(text, size=4000):
    """Split all text, preferably at paragraphs; limit UTF-16 units to handle emoji."""
    while text:
        end = min(len(text), size)
        while len(text[:end].encode("utf-16-le")) // 2 > size:
            end -= 1
        split = text.rfind("\n", 0, end)
        if end < len(text) and split > end // 2:
            end = split + 1
        yield text[:end]
        text = text[end:]


def translate(text):
    if not text.strip():
        return "（没有可翻译的文字正文）"
    provider = translation_provider()
    if provider == "argos":
        from free_translate import translate as local_translate
        return local_translate(text)
    headers = {"Ocp-Apim-Subscription-Key": os.environ["TRANSLATOR_KEY"]}
    if os.getenv("TRANSLATOR_REGION"):
        headers["Ocp-Apim-Subscription-Region"] = os.environ["TRANSLATOR_REGION"]
    results = []
    for part in chunks(text):
        response = request_json("POST", "https://api.cognitive.microsofttranslator.com/translate",
                                service="Azure Translator", headers=headers,
                                params={"api-version": "3.0", "to": "zh-Hans", "textType": "plain"},
                                json=[{"Text": part}])
        results.append(response[0]["translations"][0]["text"])
    return "\n".join(results)


def translation_provider():
    provider = os.getenv("TRANSLATION_PROVIDER", "argos").strip().lower()
    if provider not in ("argos", "azure"):
        raise RuntimeError("TRANSLATION_PROVIDER must be argos (free, local) or azure.")
    return provider


def translate_eml(path):
    """Translate manually exported email without a school app registration."""
    raw = path.read_bytes()
    source = BytesParser(policy=policy.default).parsebytes(raw)
    part = source.get_body(preferencelist=("plain", "html"))
    content = part.get_content() if part is not None else ""
    sender_name, sender_email = parseaddr(source.get("From", ""))
    received = "Unknown"
    try:
        date = parsedate_to_datetime(source.get("Date", ""))
        if date.tzinfo is not None:
            received = date.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        pass
    item = {"subject": str(source.get("Subject", "(No subject)")),
            "body": {"contentType": "html" if part is not None and part.get_content_type() == "text/html" else "text",
                     "content": content if isinstance(content, str) else ""},
            "from": {"emailAddress": {"name": sender_name, "address": sender_email}},
            "receivedDateTime": received, "hasAttachments": any(source.iter_attachments())}
    original = plain_body(item)
    result = render(item, translate(item["subject"]), translate(original), original)
    output = OUTPUT / (hashlib.sha256(raw).hexdigest()[:24] + ".html")
    atomic_write(output, result)
    print("Local email translation:", output)


def safe_link(url):
    return url if urlsplit(url).scheme == "https" else ""


def render(message, subject_zh, body_zh, original):
    esc = html.escape
    sender = (message.get("from") or {}).get("emailAddress") or {}
    link = safe_link(message.get("webLink", ""))
    link_html = f'<p><a href="{esc(link, quote=True)}">View the original email in Outlook</a></p>' if link else ""
    attachment = "Attachments are not translated. Open the original email to view them." if message.get("hasAttachments") else ""
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{esc(subject_zh)}</title></head>
<body style="margin:0;background:#f3f6fa;color:#19283b;font-family:-apple-system,Arial,sans-serif">
<main style="max-width:720px;margin:auto;padding:24px;background:white">
<p style="color:#3563b0">Email Translation · English to Chinese</p><h1 style="font-size:24px">{esc(subject_zh)}</h1>
<p style="font-size:14px;color:#59687b">From: {esc(sender.get('name', ''))} &lt;{esc(sender.get('address', ''))}&gt;<br>
Received (UTC): {esc(message.get('receivedDateTime', ''))}</p>
{link_html}
<div style="white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8">{esc(body_zh)}</div>
<p style="color:#59687b">{esc(attachment)} Machine translation. Check dates, fees, and deadlines against the original.</p>
<hr><h2 style="font-size:18px">Original English: {esc(message.get('subject', ''))}</h2>
<div style="white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.6">{esc(original)}</div>
</main></body></html>'''


def database(account_id):
    name = hashlib.sha256(account_id.encode()).hexdigest()[:20]
    db = sqlite3.connect(PRIVATE / f"{name}.sqlite3")
    db.execute("CREATE TABLE IF NOT EXISTS translations (id TEXT PRIMARY KEY, subject TEXT, html TEXT, status TEXT)")
    return db


def monitor_since(db):
    """Persist the first activation time so restarting does not replay old mail."""
    db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    db.execute("INSERT OR IGNORE INTO settings VALUES ('monitor_since', ?)",
               (datetime.now(timezone.utc).isoformat(),))
    db.commit()
    return db.execute("SELECT value FROM settings WHERE key='monitor_since'").fetchone()[0]


def is_english(text):
    from langdetect import DetectorFactory, detect_langs, LangDetectException
    DetectorFactory.seed = 0
    text = re.sub(r"https?://\S+|\b\S+@\S+\b", " ", text)
    if not text.strip():
        return False
    try:
        languages = detect_langs(text[:12000])
        return any(item.lang == "en" and item.prob >= 0.5 for item in languages)
    except LangDetectException:
        return False


def allowed(message):
    if (message.get("subject") or "").startswith(PREFIX):
        return False
    domains = {s.strip().lower() for s in os.getenv("SENDER_DOMAINS", "").split(",") if s.strip()}
    address = ((message.get("from") or {}).get("emailAddress") or {}).get("address", "")
    return not domains or address.rpartition("@")[2].lower() in domains


def process(graph, db, email, days, send=False, limit=20, english_only=False, since=None, stats=None):
    count = failed = 0
    if stats is None:
        stats = {}
    stats.update(scanned=0, sent=0, skipped_non_english=0, awaiting_send_confirmation=0)
    for message in graph.messages(days):
        stats["scanned"] += 1
        received = message.get("receivedDateTime")
        if received and received > stats.get("latest_source_received_at", ""):
            stats["latest_source_received_at"] = received
        if not allowed(message):
            continue
        if since:
            received = message.get("receivedDateTime")
            if not received or datetime.fromisoformat(received.replace("Z", "+00:00")) < datetime.fromisoformat(since):
                continue
        row = db.execute("SELECT subject,html,status FROM translations WHERE id=?", (message["id"],)).fetchone()
        if row and row[2] in ("sent", "sending", "non_english"):
            if row[2] == "sending":
                stats["awaiting_send_confirmation"] += 1
                print("A send result is uncertain; resending is paused. See the recovery instructions in README.", flush=True)
            continue
        if row and not send:
            # Previously previewed messages do not consume the new cycle limit, preventing starvation.
            continue
        if count >= limit:
            break
        count += 1
        try:
            if not row:
                if hasattr(graph, "load_body"):
                    graph.load_body(message)
                original = plain_body(message)
                if english_only and not is_english((message.get("subject") or "") + "\n" + original):
                    db.execute("INSERT INTO translations VALUES (?,?,?,?)", (message["id"], "", "", "non_english"))
                    db.commit()
                    stats["skipped_non_english"] += 1
                    print("Skipping a non-English message or a message with uncertain language.", flush=True)
                    continue
                subject = translate(message.get("subject") or "(No subject)")
                body = render(message, subject, translate(original), original)
                db.execute("INSERT INTO translations VALUES (?,?,?,?)", (message["id"], subject, body, "ready"))
                db.commit()
            else:
                subject, body, _ = row
            filename = hashlib.sha256(message["id"].encode()).hexdigest()[:24] + ".html"
            atomic_write(OUTPUT / filename, body)
            if send:
                db.execute("UPDATE translations SET status='sending' WHERE id=?", (message["id"],))
                db.commit()
                try:
                    graph.send(email, subject, body)
                except ApiError as exc:
                    # Explicit rejection (ordinary 4xx except 408) can be retried on the next cycle.
                    if 400 <= exc.status < 500 and exc.status != 408:
                        db.execute("UPDATE translations SET status='ready' WHERE id=?", (message["id"],))
                        db.commit()
                    raise
                db.execute("UPDATE translations SET status='sent' WHERE id=?", (message["id"],))
                db.commit()
                stats["sent"] += 1
            print(f"{'Send submitted' if send else 'Local preview'}: output/{filename}", flush=True)
        except Exception as exc:
            failed += 1
            detail = str(exc) if isinstance(exc, ApiError) else type(exc).__name__
            print(f"Message processing failed ({detail}); records retained for the next run.", file=sys.stderr, flush=True)
    print(f"Processed {count} messages this cycle; {failed} failed.", flush=True)
    return failed


def demo():
    message = {"subject": "Reminder: Submit your course registration", "receivedDateTime": "2026-10-12T08:00:00Z",
               "from": {"emailAddress": {"name": "Example Academic Office", "address": "demo@example.com"}},
               "webLink": "https://outlook.office.com/mail/", "hasAttachments": True}
    body = render(message, "提醒：提交课程注册（演示）",
                  "亲爱的同学：\n\n请在 10 月 15 日下午 5 点前完成课程注册。\n登录学生门户确认你的课程。如有问题，请联系教务办公室。\n\n这是虚构示例，不是 NTU 的真实通知；译文已预置，未调用翻译 API。",
                  "Dear student,\nPlease complete your course registration by 15 October at 5 pm.\nLog in to the student portal to confirm your courses. Contact the academic office if you need help.")
    atomic_write(OUTPUT / "demo.html", body)
    print("Demo generated:", OUTPUT / "demo.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Generate an offline mobile reading demo without signing in or sending")
    parser.add_argument("--setup-free", action="store_true", help="Download and install the free local English-to-Chinese model without accessing email")
    parser.add_argument("--eml", type=Path, help="Translate an exported .eml file locally without school app permissions or sending")
    parser.add_argument("--source", choices=["apple_mail", "graph"], help="Mail source; defaults to MAIL_SOURCE in .env or apple_mail")
    parser.add_argument("--check-account", action="store_true", help="Check account access without translating or sending")
    parser.add_argument("--login", action="store_true", help="Allow interactive sign-in to check the account without translating or sending")
    parser.add_argument("--device", action="store_true", help="Use device-code sign-in; your school may disable this flow")
    parser.add_argument("--send", action="store_true", help="Send translations to your own school mailbox; request Mail.Send during sign-in")
    parser.add_argument("--watch", action="store_true", help="Continuously check the Inbox; press Ctrl+C to stop")
    parser.add_argument("--english-only", action="store_true", help="Translate only messages detected as English")
    parser.add_argument("--new-only", action="store_true", help="Process only messages received after initial monitor activation; preserve the start time on restart")
    parser.add_argument("--init-monitor", action="store_true", help="Record the monitor start time without reading or sending messages")
    parser.add_argument("--interval", type=int, default=300, help="Polling interval in seconds (default: 300)")
    parser.add_argument("--limit", type=int, default=20, help="Maximum messages per cycle (default: 20)")
    args = parser.parse_args()
    if args.limit < 1 or args.interval < 60:
        parser.error("limit must be positive and interval must be at least 60 seconds")
    os.umask(0o077)
    if args.demo:
        demo()
        return 0
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if args.setup_free:
        from free_translate import install, verify_installation
        install()
        print("Local translation check:", verify_installation(), flush=True)
        return 0
    provider = translation_provider()
    if args.eml:
        if args.send or args.watch or args.login:
            parser.error("--eml is for local preview only and cannot be combined with --send / --watch / --login")
        if provider == "azure" and not os.getenv("TRANSLATOR_KEY"):
            raise RuntimeError("Azure translation requires TRANSLATOR_KEY. Set TRANSLATION_PROVIDER=argos for free local translation.")
        translate_eml(args.eml)
        return 0
    source = args.source or os.getenv("MAIL_SOURCE", "apple_mail").strip().lower()
    if source not in ("apple_mail", "graph"):
        raise RuntimeError("MAIL_SOURCE must be apple_mail or graph.")
    if args.login and source != "graph":
        parser.error("--login is for Graph mode. For macOS Mail, sign in through Mail first, then run --check-account")
    checking = args.login or args.check_account or args.init_monitor
    required = ["SCHOOL_EMAIL"] + (["MS_CLIENT_ID"] if source == "graph" else []) + (
        ["TRANSLATOR_KEY"] if not checking and provider == "azure" else [])
    missing = [key for key in required if not os.getenv(key) or os.getenv(key) == "your_name@e.ntu.edu.sg"]
    if missing:
        raise RuntimeError("Set these values in .env: " + ", ".join(missing))
    days = int(os.getenv("LOOKBACK_DAYS", "7"))
    if days < 1:
        raise RuntimeError("LOOKBACK_DAYS must be at least 1")
    PRIVATE.mkdir(mode=0o700, exist_ok=True)
    with (PRIVATE / "run.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another translation process is running.")
        if source == "apple_mail":
            from apple_mail import AppleMailClient
            graph = AppleMailClient()
        else:
            graph = GraphClient(send=args.send, interactive=args.login, device=args.device)
        account, email = graph.profile()
        if args.login or args.check_account:
            print("Account access check succeeded. " + (
                "This is a macOS Mail account; no messages have been translated or sent." if source == "apple_mail" else
                ("Send permission requested." if args.send else "Read permission requested.")))
            return 0
        db = database(account)
        try:
            since = monitor_since(db) if args.new_only or args.init_monitor else None
            if args.init_monitor:
                print("Monitor start time recorded; older messages will not be translated automatically.")
                return 0
            while True:
                state = {"last_check_at": datetime.now(timezone.utc).isoformat(), "monitor_since": since,
                         "interval_seconds": args.interval, "state": "checking"}
                if args.watch:
                    atomic_write(PRIVATE / "monitor-status.json", json.dumps(state))
                try:
                    stats = {}
                    failed = process(graph, db, email, days, args.send, args.limit, args.english_only, since, stats)
                    needs_attention = failed or stats["awaiting_send_confirmation"]
                    state.update(state="degraded" if needs_attention else "ok", failed=failed, **stats)
                    state.update(getattr(graph, "sync_info", {}))
                except Exception as exc:
                    if not args.watch:
                        raise
                    failed = 1
                    state.update(state="error", error=type(exc).__name__)
                    if isinstance(exc, RuntimeError):
                        state["error_detail"] = str(exc)
                    print(f"Check failed ({type(exc).__name__}); retrying next cycle.", file=sys.stderr, flush=True)
                state["last_completed_at"] = datetime.now(timezone.utc).isoformat()
                if args.watch:
                    atomic_write(PRIVATE / "monitor-status.json", json.dumps(state))
                if not args.watch:
                    return 1 if failed else 0
                time.sleep(args.interval)
        finally:
            db.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
    except Exception as error:
        # Do not print raw requests exceptions, which may contain keys or sensitive URLs.
        print(str(error) if isinstance(error, (RuntimeError, ApiError)) else
              f"Run failed: {type(error).__name__}. Check your network, configuration, and permissions.", file=sys.stderr)
        sys.exit(1)
