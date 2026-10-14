import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import mail_translator as m


def message(id="source-1", subject="Course deadline"):
    return {"id": id, "subject": subject, "body": {"contentType": "text", "content": "Submit by Friday."},
            "from": {"emailAddress": {"address": "office@ntu.edu.sg"}},
            "webLink": "https://outlook.office.com/mail/example"}


class FakeGraph:
    def __init__(self, messages, error=None):
        self.items, self.error, self.sent = messages, error, []

    def messages(self, days):
        return iter(self.items)

    def send(self, email, subject, body):
        self.sent.append((email, subject, body))
        if self.error:
            raise self.error


class MailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = patch.object(m, "OUTPUT", Path(self.temp.name))
        self.output.start()
        self.translator = patch.object(m, "translate", side_effect=lambda text: "中文：" + text)
        self.mock_translate = self.translator.start()
        self.env = patch.dict(os.environ, {"SENDER_DOMAINS": ""})
        self.env.start()
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE translations (id TEXT PRIMARY KEY, subject TEXT, html TEXT, status TEXT)")

    def tearDown(self):
        self.db.close()
        self.env.stop()
        self.translator.stop()
        self.output.stop()
        self.temp.cleanup()

    def run_process(self, graph, send=True, limit=20):
        return m.process(graph, self.db, "me@e.ntu.edu.sg", 7, send, limit)

    def test_preview_then_send_and_restart_deduplication(self):
        graph = FakeGraph([message()])
        self.assertEqual(self.run_process(graph, False), 0)
        self.assertEqual(len(graph.sent), 0)
        self.assertEqual(self.run_process(graph), 0)
        self.assertEqual(self.run_process(graph), 0)
        self.assertEqual(len(graph.sent), 1)
        self.assertEqual(graph.sent[0][0], "me@e.ntu.edu.sg")
        self.assertEqual(self.mock_translate.call_count, 2)

    def test_generated_mail_is_not_translated(self):
        graph = FakeGraph([message(subject=m.PREFIX + " Course")])
        self.run_process(graph)
        self.assertEqual(graph.sent, [])
        self.mock_translate.assert_not_called()

    def test_sender_domain_filter(self):
        with patch.dict(os.environ, {"SENDER_DOMAINS": "e.ntu.edu.sg"}):
            self.assertFalse(m.allowed(message()))
        with patch.dict(os.environ, {"SENDER_DOMAINS": "NTU.EDU.SG"}):
            self.assertTrue(m.allowed(message()))

    def test_send_timeout_does_not_resend(self):
        graph = FakeGraph([message()], TimeoutError())
        self.assertEqual(self.run_process(graph), 1)
        self.assertEqual(self.db.execute("SELECT status FROM translations").fetchone()[0], "sending")
        graph.error = None
        self.run_process(graph)
        self.assertEqual(len(graph.sent), 1)

    def test_definitive_rejection_can_retry(self):
        graph = FakeGraph([message()], m.ApiError("Graph", 403))
        self.run_process(graph)
        self.assertEqual(self.db.execute("SELECT status FROM translations").fetchone()[0], "ready")
        graph.error = None
        self.run_process(graph)
        self.assertEqual(len(graph.sent), 2)

    def test_translation_failure_does_not_mark_processed(self):
        self.mock_translate.side_effect = RuntimeError("translation failed")
        self.assertEqual(self.run_process(FakeGraph([message()])), 1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM translations").fetchone()[0], 0)

    def test_limit_and_continue_past_sent_mail(self):
        graph = FakeGraph([message(str(i)) for i in range(3)])
        self.run_process(graph, limit=1)
        self.run_process(graph, limit=1)
        self.assertEqual(len(graph.sent), 2)

    def test_chunk_preserves_long_unicode_text(self):
        text = ("Welcome 😀\n" * 1500) + ("x" * 9000)
        parts = list(m.chunks(text))
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(p.encode("utf-16-le")) // 2 <= 4000 for p in parts))

    def test_untrusted_html_is_escaped(self):
        item = message(subject='<script>alert("x")</script>')
        item["webLink"] = "javascript:alert(1)"
        rendered = m.render(item, "<b>中文</b>", "<img src=x onerror=alert(1)>", "<script>bad</script>")
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("javascript:", rendered)
        self.assertIn("&lt;b&gt;", rendered)

    def test_graph_pagination_uses_nextlink(self):
        graph = m.GraphClient.__new__(m.GraphClient)
        with patch.object(graph, "call", side_effect=[
            {"value": [message("1")], "@odata.nextLink": m.GRAPH + "/me/mailFolders/inbox/messages?$skip=30"},
            {"value": [message("2")]},
        ]) as call:
            self.assertEqual([x["id"] for x in graph.messages(7)], ["1", "2"])
            self.assertIsNone(call.call_args_list[1].kwargs["params"])

    def test_preview_continues_to_new_messages(self):
        graph = FakeGraph([message(str(i)) for i in range(3)])
        self.run_process(graph, send=False, limit=1)
        self.run_process(graph, send=False, limit=1)
        self.assertEqual(self.db.execute("SELECT count(*) FROM translations").fetchone()[0], 2)
        self.assertEqual(graph.sent, [])

    def test_html_body_retains_links_without_script(self):
        item = message()
        item["body"] = {"contentType": "html", "content":
                        '<style>hidden</style><p>Apply <a href="https://example.org/apply">here</a></p><script>bad()</script>'}
        text = m.plain_body(item)
        self.assertIn("https://example.org/apply", text)
        self.assertNotIn("bad()", text)
        self.assertNotIn("hidden", text)

    def test_send_payload_only_targets_self_and_disables_retry(self):
        graph = m.GraphClient.__new__(m.GraphClient)
        with patch.object(graph, "call") as call:
            graph.send("me@e.ntu.edu.sg", "中文标题", "译文")
            self.assertFalse(call.call_args.kwargs["retry"])
            payload = call.call_args.kwargs["json"]
            self.assertEqual(payload["message"]["toRecipients"],
                             [{"emailAddress": {"address": "me@e.ntu.edu.sg"}}])
            self.assertTrue(payload["message"]["subject"].startswith(m.PREFIX))

    def test_free_provider_needs_no_api_key(self):
        with patch.dict(os.environ, {"TRANSLATION_PROVIDER": "argos"}), \
             patch("free_translate.translate", return_value="你好") as local:
            # Test the real provider dispatcher rather than the generic setUp translation mock.
            self.translator.stop()
            self.assertEqual(m.translate("Hello"), "你好")
            local.assert_called_once_with("Hello")

    def test_eml_translation_does_not_access_graph(self):
        path = Path(self.temp.name) / "sample.eml"
        path.write_bytes(b'From: Office <office@example.com>\r\nSubject: Reminder\r\n'
                         b'Date: Mon, 12 Oct 2026 16:00:00 +0800\r\n'
                         b'Content-Type: text/plain; charset=utf-8\r\n\r\nSubmit by Friday.')
        with patch.object(m, "GraphClient", side_effect=AssertionError("should not access mailbox")):
            m.translate_eml(path)
        files = list(Path(self.temp.name).glob("*.html"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text()
        self.assertIn("中文：Submit by Friday.", content)
        self.assertIn("2026-10-12T08:00:00+00:00", content)

    def test_new_english_mail_sent_once_old_and_chinese_skipped(self):
        old = message("old")
        old["receivedDateTime"] = "2026-10-12T08:00:00Z"
        new = message("new")
        new["receivedDateTime"] = "2026-10-12T10:01:00Z"
        chinese = message("chinese", "中文通知")
        chinese["receivedDateTime"] = "2026-10-12T10:02:00Z"
        chinese["body"]["content"] = "请同学们明天下午到办公室提交课程注册申请，谢谢。"
        new["body"]["content"] = "Please submit your course registration by Friday at five in the afternoon. Thank you for your cooperation."
        graph = FakeGraph([old, new, chinese])
        for _ in range(2):
            m.process(graph, self.db, "me@e.ntu.edu.sg", 7, True, english_only=True, since="2026-10-12T10:00:00+00:00")
        self.assertEqual(len(graph.sent), 1)
        self.assertIsNone(self.db.execute("SELECT status FROM translations WHERE id='old'").fetchone())
        self.assertEqual(self.db.execute("SELECT status FROM translations WHERE id='chinese'").fetchone()[0], "non_english")

    def test_monitor_baseline_survives_restart(self):
        first = m.monitor_since(self.db)
        self.assertEqual(m.monitor_since(self.db), first)

    def test_language_detection(self):
        self.assertTrue(m.is_english("Dear student, please submit your course registration by Friday at five in the afternoon."))
        self.assertFalse(m.is_english("各位同学请注意，课程注册将于本周五结束，请尽快完成注册，谢谢。"))
        self.assertFalse(m.is_english("Bonjour, veuillez confirmer votre inscription avant vendredi. Merci beaucoup pour votre aide."))


if __name__ == "__main__":
    unittest.main()
