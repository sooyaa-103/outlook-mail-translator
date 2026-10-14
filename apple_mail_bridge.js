// macOS Mail automation. JSON input/output; never reads account passwords.
ObjC.import('Foundation');
function run() {
    const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    const input = JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding)));
    const mail = Application('Mail');
    if (input.action === 'discover') {
        const found = [];
        for (const account of mail.accounts()) {
            if (!account.enabled()) continue;
            for (const email of account.emailAddresses()) {
                if (/@(?:e\.)?ntu\.edu\.sg$/i.test(email)) found.push({id: account.id(), email: email.toLowerCase()});
            }
        }
        return JSON.stringify(found);
    }
    const expected = input.email.trim().toLowerCase();
    const accounts = mail.accounts().filter(a =>
        a.enabled() && a.emailAddresses().some(e => e.toLowerCase() === expected));
    if (accounts.length !== 1) throw new Error('SCHOOL_ACCOUNT_NOT_FOUND');
    const account = accounts[0];
    const accountId = account.id();
    if (input.action === 'profile') {
        return JSON.stringify({id: accountId, email: expected});
    }
    if (input.action === 'refresh') {
        // Explicitly fetch the selected school account. Reading Inbox alone
        // does not ask Exchange for updates, and automatic fetch may stall.
        mail.checkForNewMail({for: account});
        return JSON.stringify({requested: true});
    }
    if (input.action === 'delivery') {
        const messages = mail.inbox.messages.whose({subject: input.subject})();
        return JSON.stringify({found: messages.some(m => m.mailbox.account.id() === accountId)});
    }
    if (input.action === 'messages') {
        // The app Inbox is the aggregate Inbox. Filter to the selected account
        // before reading any subjects, senders or message bodies.
        const since = new Date(input.since);
        const candidates = mail.inbox.messages.whose({dateReceived: {_greaterThan: since}})();
        const result = [];
        for (const message of candidates) {
            if (message.mailbox.account.id() !== accountId) continue;
            const subject = message.subject() || '(No subject)';
            if (subject.startsWith(input.prefix)) continue;
            result.push({id: message.messageId() || String(message.id()), subject: subject,
                sender: message.sender(), receivedDateTime: message.dateReceived().toISOString(),
                localId: message.id()});
        }
        result.sort((a, b) => a.receivedDateTime.localeCompare(b.receivedDateTime));
        return JSON.stringify(result);
    }
    if (input.action === 'body') {
        const matches = mail.inbox.messages.whose({id: input.localId})();
        if (matches.length !== 1 || matches[0].mailbox.account.id() !== accountId) {
            throw new Error('MESSAGE_NOT_FOUND');
        }
        return JSON.stringify({content: matches[0].content(), hasAttachments: matches[0].mailAttachments().length > 0});
    }
    if (input.action === 'send') {
        const outgoing = mail.OutgoingMessage({subject: input.subject, content: input.content,
            sender: expected, visible: false});
        mail.outgoingMessages.push(outgoing);
        outgoing.toRecipients.push(mail.ToRecipient({address: expected}));
        const accepted = mail.send(outgoing);
        return JSON.stringify({accepted: accepted === true});
    }
    throw new Error('UNKNOWN_ACTION');
}
