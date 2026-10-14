import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from apple_mail import AppleMailClient


class AppleMailTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"SCHOOL_EMAIL": "me@e.ntu.edu.sg"})
        self.env.start()
        self.platform = patch("apple_mail.sys.platform", "darwin")
        self.platform.start()
        self.client = AppleMailClient()

    def tearDown(self):
        self.platform.stop()
        self.env.stop()

    def test_profile_keeps_mail_state_separate(self):
        with patch.object(self.client, "call", return_value={"id": "abc", "email": "me@e.ntu.edu.sg"}):
            self.assertEqual(self.client.profile(), ("apple-mail:abc", "me@e.ntu.edu.sg"))

    def test_message_maps_sender_and_plain_body(self):
        message = {"id": "id", "subject": "Deadline", "sender": "Office <office@ntu.edu.sg>",
                   "localId": 42, "receivedDateTime": "2026-10-12T08:00:00Z"}
        with patch.object(self.client, "refresh") as refresh, patch.object(self.client, "call", return_value=[message]):
            item = list(self.client.messages(7))[0]
            refresh.assert_called_once()
        self.assertEqual(item["from"]["emailAddress"]["address"], "office@ntu.edu.sg")
        self.assertNotIn("body", item)
        with patch.object(self.client, "call", return_value={"content": "Submit on Friday.", "hasAttachments": False}) as call:
            self.client.load_body(item)
            call.assert_called_once_with("body", localId=42)
        self.assertEqual(item["body"], {"contentType": "text", "content": "Submit on Friday."})

    def test_send_has_text_content_and_self_recipient(self):
        with patch.object(self.client, "call", return_value={"accepted": True}) as call:
            self.client.send("me@e.ntu.edu.sg", "标题", "<html><body><p>中文</p><p>English</p></body></html>")
            self.assertEqual(call.call_args.args, ("send",))
            self.assertEqual(call.call_args.kwargs["content"], "中文\nEnglish")
            self.assertTrue(call.call_args.kwargs["subject"].startswith("【中文译文】"))
        with self.assertRaises(RuntimeError):
            self.client.send("someone@example.com", "标题", "body")

    def test_automation_permission_error_is_actionable(self):
        with patch("apple_mail.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "error -1743")):
            with self.assertRaisesRegex(RuntimeError, "Automation"):
                self.client.profile()

    def test_payload_is_stdin_not_shell_interpolation(self):
        with patch("apple_mail.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "{}", "")) as run:
            self.client.call("send", content="$(do-not-execute) `do-not-execute`")
            payload = json.loads(run.call_args.kwargs["input"])
            self.assertEqual(payload["email"], "me@e.ntu.edu.sg")
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertNotIn(payload["content"], run.call_args.args[0])

    def test_refresh_requested_before_scanning(self):
        with patch.object(self.client, "call", side_effect=[{"requested": True}, []]) as call, \
             patch("apple_mail.time.sleep"):
            self.assertEqual(list(self.client.messages(7)), [])
            self.assertEqual([x.args[0] for x in call.call_args_list], ["refresh", "messages"])
            self.assertEqual(self.client.sync_info["sync_status"], "requested")

    def test_refresh_failure_does_not_report_empty_success(self):
        with patch.object(self.client, "call", return_value={"requested": False}) as call:
            with self.assertRaisesRegex(RuntimeError, "synchronization"):
                list(self.client.messages(7))
            self.assertEqual(call.call_count, 1)


if __name__ == "__main__":
    unittest.main()
