# Microsoft Graph and Azure Setup

This optional route reads your Outlook Inbox through Microsoft Graph, translates email, and can send the bilingual result to your own mailbox. It runs on macOS or Linux with Python 3.10+. Organization app-registration, consent, and conditional-access policies determine whether it is available.

For the Apple Mail route, use [the main README](README.md). Set `MAIL_SOURCE=graph` in `.env` or pass `--source graph` when following this guide.

## Install and configure

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-free.txt
cp .env.example .env
chmod 600 .env
```

Edit `.env` with your own settings. Do not put your mailbox password in this file.

## Register an Entra application

1. Sign in to [Microsoft Entra admin center](https://entra.microsoft.com/) with your organization account and open **App registrations > New registration**. Contact your IT team if registration is restricted.
2. Choose a name such as `School Mail Translator`. For an app registered in your school's tenant, select accounts in that organizational directory only.
3. Copy the Application (client) ID and Directory (tenant) ID into `MS_CLIENT_ID` and `MS_TENANT_ID`.
4. Under **Authentication**, add the **Mobile and desktop applications** platform and the `http://localhost` redirect URI for interactive browser sign-in.
5. Add Microsoft Graph **delegated** permissions: `User.Read` and `Mail.Read`; add `Mail.Send` if you want translations delivered to yourself. No application permissions or client secret are needed.
6. Set `SCHOOL_EMAIL` to your own address and `MAIL_SOURCE=graph`.

An app registered in a different tenant needs an appropriate multitenant configuration, and your school must allow external apps. `MS_TENANT_ID=organizations` can select organizational accounts but does not bypass consent or multifactor authentication.

Check read access:

```bash
python mail_translator.py --login
```

Complete Microsoft's browser sign-in and consent. If administrator approval is required, ask your school IT team to review the app and delegated permissions. Do not substitute another application's client ID to bypass restrictions.

For a machine without a usable browser, `--login --device` uses device-code sign-in. The registration must allow public-client flows, and your organization may prohibit this method. Scheduled runs do not open interactive login windows.

## Translation providers

### Free local translation

Keep `TRANSLATION_PROVIDER=argos` and install the model:

```bash
python mail_translator.py --setup-free
```

Setup does not access your mailbox or require an Entra client ID. It downloads model resources into `private/argos-models`; normal translation is local. The model is specifically English-to-Chinese. It can require substantial disk space, and specialized terminology may need correction.

If Graph authorization is unavailable, manual import still works:

```bash
python mail_translator.py --eml "/absolute/path/message.eml"
```

This generates a local HTML preview without signing in to a mailbox or sending mail.

### Optional Azure Translator

Install `requirements.txt` if you need only Azure instead of the Argos dependencies. Create a Translator resource in [Azure Portal](https://portal.azure.com/), then configure:

```dotenv
TRANSLATION_PROVIDER=azure
TRANSLATOR_KEY=your_resource_key
TRANSLATOR_REGION=your_resource_region
```

A global single-service resource may use an empty region; regional resources need their actual region. This script uses the global text-translation endpoint; custom endpoints and VNet configurations are not supported. Check your resource's pricing and quota. A school Microsoft 365 account does not itself provide Translator API credentials.

Azure mode sends email subjects and text bodies to Azure Translator. Argos mode keeps translation local. Neither translates attachments or images.

## Preview and delivery

```bash
# Preview up to three Inbox messages from the lookback window.
python mail_translator.py --limit 3
open output  # macOS; use your file manager on Linux

# Request send permission without sending messages.
python mail_translator.py --login --send

# Send up to three translations to your own account.
python mail_translator.py --send --limit 3
```

Messages begin with `【中文译文】` and include the original text and an Outlook link where available. The recipient is obtained from `/me` and checked against `SCHOOL_EMAIL`. The script saves a Sent copy and does not delete, modify, or deliberately mark source messages read. The API accepting a send request does not guarantee final delivery.

Defaults: the last seven days of the Inbox, including read and unread messages, with a maximum of 20 per cycle. Set `LOOKBACK_DAYS` or `--limit` to change these. The first run may process old mail. Exact sender-domain filtering is available through `SENDER_DOMAINS`. Argos assumes English unless `--english-only` is enabled; Azure detects source language automatically.

## Foreground polling

```bash
python mail_translator.py --watch --send
```

Checks every five minutes by default. Keep the process running and the computer awake; Ctrl+C stops it. Processing records survive restarts. Add `--english-only --new-only` to detect English messages and persist a start time that excludes older mail.

## Daily macOS schedule

Verify sign-in, preview, and delivery before installing a schedule. Choose one runner; avoid overlapping it with the background monitor or foreground polling.

```bash
.venv/bin/python make_schedule.py --source graph --hour 20 --minute 0
mkdir -p "$HOME/Library/LaunchAgents"
cp local.ntu.mail-translator.plist "$HOME/Library/LaunchAgents/"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.ntu.mail-translator.plist"
```

Use `--source apple_mail` for the Apple Mail route. The generator creates a configuration without installing it. The schedule uses the Mac system timezone and processes up to 200 messages per run. The Mac must be logged in; sleep and shutdown affect execution. Inspect `private/schedule.log` and `private/schedule-error.log`. Terminal success does not prove background automation permissions are available.

Uninstall before moving the project or regenerating the installed schedule:

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.ntu.mail-translator.plist"
rm "$HOME/Library/LaunchAgents/local.ntu.mail-translator.plist"
```

## Recovery

- **401 / expired sign-in:** run `python mail_translator.py --login --send` again.
- **403 / administrator approval:** verify delegated permissions and organization consent with IT.
- **Translation error:** complete Argos setup, or check your Azure key, region, quota, and connectivity.
- **Uncertain delivery:** a timeout may occur after Microsoft accepts the message. The record stays in `sending`, blocking resends. Verify Inbox and Sent before changing its SQLite status to `sent` (confirmed submission) or `ready` (confirmed unsent). Leave uncertain records unchanged.
- **Source message moved or outside the window:** cached unsent translations require the original to remain in the selected Inbox and lookback window.

Do not delete `private/` for routine troubleshooting; it contains tokens and deduplication state. Keep `.env`, `private/`, and `output/` private. Removing processing records can cause old mail to be resent.

## Validation

```bash
python -m unittest discover -s tests -v
```

Tests mock external services. Real tenant authorization, translation quality, and phone delivery need local verification.

## References

- [Desktop app registration](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-configuration)
- [Microsoft Graph: List messages](https://learn.microsoft.com/en-us/graph/api/user-list-messages?view=graph-rest-1.0)
- [Microsoft Graph: Send mail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)
- [Azure Translator](https://learn.microsoft.com/en-us/azure/cognitive-services/translator/quickstart-translator)
- [Argos Translate](https://github.com/argosopentech/argos-translate)
