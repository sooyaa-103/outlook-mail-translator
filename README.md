# Outlook Mail Translator

Translate English email into Chinese with a free local model, preview the original and translation side by side, and optionally send the result to your own mailbox for reading in Outlook on your phone.

The default route uses an Exchange account already signed in to **Apple Mail on macOS**. It does not require a custom Microsoft Entra app, a translation API key, or a mailbox password in the script. An optional Microsoft Graph route supports macOS and Linux when your organization permits app registration and delegated access.

Documentation, configuration comments, and command-line messages are in English. The translation target remains Chinese; Chinese example text and the existing `【中文译文】` subject marker are intentional. This project originated as a personal school-mail helper for NTU and is not affiliated with NTU or Microsoft.

## Features

- Offline English-to-Chinese translation with Argos Translate after model setup.
- Apple Mail integration that selects one explicitly configured account.
- Local HTML previews with translated text, sender details, and the original.
- Optional delivery to your own account, readable in iPhone Outlook.
- macOS background monitoring of new English email every 60 seconds.
- Persistent processing records, single-process locking, and protection against resending after an uncertain send result.
- Manual `.eml` import without automatic mailbox access.
- Optional Microsoft Graph access and Azure Translator.

## Requirements

- Python 3.10 or newer.
- macOS with Apple Mail for the default route and background monitor.
- An Exchange account that your organization allows Apple Mail to access.
- Internet access for initial dependency/model installation and mailbox synchronization.

The phone is a reader; Python runs on your computer. Keep the Mac logged in, awake, and able to synchronize Mail while monitoring.

## Quick start

### 1. Install dependencies and the local model

Run these commands from the project directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-free.txt
.venv/bin/python mail_translator.py --setup-free
```

Model setup downloads the English-to-Chinese model and prepares sentence-processing resources. Models are stored in `private/argos-models`. Normal Argos translation does not call a cloud translation API or download resources.

To inspect the reading layout without an account or model:

```bash
.venv/bin/python mail_translator.py --demo
open output/demo.html
```

The demo is fictional and uses a preset translation; it does not verify translation quality.

### 2. Connect Apple Mail

In the macOS Mail app, add your account through **Microsoft Exchange**, complete Microsoft sign-in and multifactor authentication, and confirm that your Inbox synchronizes. Enter credentials only in Microsoft's sign-in flow.

Configure the email address explicitly:

```bash
.venv/bin/python configure_local.py --email your_name@e.ntu.edu.sg
.venv/bin/python mail_translator.py --check-account
```

For a single NTU account, `configure_local.py --detect` can discover its address. Without arguments, the helper prompts for an address. Explicit `--email` also works for other Exchange domains when Apple Mail supports the account.

The helper writes these settings to a private local `.env` file:

```dotenv
MAIL_SOURCE=apple_mail
TRANSLATION_PROVIDER=argos
SCHOOL_EMAIL=your_name@e.ntu.edu.sg
```

If macOS asks to let your terminal or Python control Mail, allow it. Existing permissions can be changed under **System Settings > Privacy & Security > Automation**. The account check does not translate or send messages.

### 3. Preview, then optionally send

Preview one recent Inbox message:

```bash
.venv/bin/python mail_translator.py --limit 1
open output
```

After reviewing the HTML, send its translation to your own mailbox:

```bash
.venv/bin/python mail_translator.py --send --limit 1
```

The subject begins with `【中文译文】`. Apple Mail sends plain text containing both the translation and original; local HTML previews retain their formatting. Check your Inbox, Sent folder, and Outbox to verify delivery.

By default, a run considers the last seven days of the selected Inbox, including read and unread messages, and processes at most 20 messages. The first ordinary run may process old mail. Set `LOOKBACK_DAYS=1` to narrow the window or use `--limit 100` to increase the per-run limit. Other accounts and folders are excluded.

## Automatic monitoring on macOS

After verifying a preview and a small real send:

```bash
.venv/bin/python monitor.py start
.venv/bin/python monitor.py status
```

This installs a user LaunchAgent that checks every 60 seconds. You can close the terminal. On first activation, it records a start time and excludes older messages; restarting preserves that start time and processing records. It translates only messages detected as English, skipping generated translations and messages with another or uncertain language. Short and mixed-language emails can be misclassified.

Each cycle requests a Mail synchronization before scanning local messages. Synchronization is asynchronous: a fetch request being accepted does not prove that server downloads have finished. Slower downloads are considered on a later cycle. Set Mail's own check-for-new-mail interval to one minute if available.

Inspect or stop the service:

```bash
.venv/bin/python monitor.py status
.venv/bin/python monitor.py stop
```

Logs: `private/monitor.log` and `private/monitor-error.log`. Status: `private/monitor-status.json`, including the last sync request, scan count, latest source-message time, send count, and uncertain sends. Times are recorded in UTC. The service starts again at login; temporary failures are retried on later cycles. Messages received while the computer is off are considered after it resumes, within `LOOKBACK_DAYS`.

Choose one automatic runner to avoid overlapping jobs. Alternatives:

- Foreground polling: `.venv/bin/python mail_translator.py --watch --send` (five-minute interval by default; Ctrl+C stops it).
- Daily macOS task: follow [the scheduling instructions](README_GRAPH.md#daily-macos-schedule), selecting `--source apple_mail` or `--source graph` as appropriate.

## Configuration

See [.env.example](.env.example) for the complete template.

| Variable | Purpose | Default |
| --- | --- | --- |
| `MAIL_SOURCE` | `apple_mail` or `graph` | `apple_mail` |
| `SCHOOL_EMAIL` | Your own mailbox address | Required |
| `TRANSLATION_PROVIDER` | `argos` or `azure` | `argos` |
| `LOOKBACK_DAYS` | Inbox lookback in days | `7` |
| `SENDER_DOMAINS` | Comma-separated exact sender domains | All senders |
| `MS_CLIENT_ID` | Entra app ID for Graph | Required for Graph |
| `MS_TENANT_ID` | Entra tenant ID or tenant selector | `organizations` |
| `TRANSLATOR_KEY` | Azure Translator key | Required for Azure |
| `TRANSLATOR_REGION` | Region of your Azure resource | Empty |

For example, `SENDER_DOMAINS=ntu.edu.sg,e.ntu.edu.sg` allows only those exact domains; add subdomains separately. Graph setup and optional Azure translation are described in [README_GRAPH.md](README_GRAPH.md).

## Manual email import

If automatic access is unavailable, export an email as `.eml` from Outlook and translate it locally:

```bash
.venv/bin/python mail_translator.py --eml "/absolute/path/message.eml"
open output
```

This path does not require an Entra registration or mailbox password and does not send email. It still requires the selected translation provider to be configured.

## Privacy and limitations

Argos translates text locally after setup. Azure mode sends subjects and text bodies to Azure Translator. Mailbox synchronization and delivery still use your email provider.

`.env`, `private/`, `output/`, local LaunchAgent files, logs, exported emails, and databases are excluded from Git. They can contain credentials, tokens, email text, and processing records; keep them local. The script does not delete or modify source messages or deliberately mark them read.

The free model is fixed to English-to-Chinese. Attachments, image text, and scanned documents are not translated. Remote email images are not loaded, and protected content is not decrypted. Check dates, amounts, deadlines, and specialized terms against the original.

## Troubleshooting and recovery

- **Account not found:** confirm Mail synchronization and the address in `.env`. If your organization blocks Apple Mail access, this route cannot bypass that policy.
- **Automation failure or timeout:** open Mail and check macOS permission dialogs and synchronization.
- **Missing model:** run `--setup-free` with Internet access.
- **Uncertain send:** the record stays in `sending`, preventing automatic resends. Inspect Inbox, Sent, and Outbox first. After confirming delivery, set the record's `status` to `sent` in `private/*.sqlite3`; only set it to `ready` if you have confirmed it was not sent. Leave it unchanged if uncertain. `sent` means the client/API accepted submission, not guaranteed final delivery.
- **Cached preview is not sent:** the source message must still be in the current Inbox and lookback window. Increase `LOOKBACK_DAYS` or move it back if needed.
- **Avoid deleting `private/` during ordinary troubleshooting:** it contains deduplication records; removing them can replay old mail. Recreating Mail accounts or switching source routes can also cause reprocessing.

## Development and verification

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests use mocked services and do not read a real mailbox or send messages. They cover deduplication, preview-to-send behavior, generated-message filtering, uncertain sends, safe HTML rendering, text chunking, language detection, and Mail bridge behavior. Real organization sign-in, mail delivery, translation quality, and background permissions require local verification.

| File | Role |
| --- | --- |
| `mail_translator.py` | CLI, translation pipeline, previews, Graph access, and state |
| `apple_mail.py` / `apple_mail_bridge.js` | Python client and JavaScript for Automation bridge to Mail |
| `free_translate.py` | Argos model setup and offline translation |
| `configure_local.py` | Local Apple Mail account configuration |
| `monitor.py` | macOS background monitor management |
| `make_schedule.py` | Daily LaunchAgent configuration generator |
| `tests/` | Mocked unit tests |

## References

- [Argos Translate](https://github.com/argosopentech/argos-translate)
- [Microsoft: Set up email in macOS Mail](https://support.microsoft.com/en-us/outlook/set-up-email-in-mac-os-x-mail)
- [Microsoft Graph: List messages](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0)
- [Microsoft Graph: Send mail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)
