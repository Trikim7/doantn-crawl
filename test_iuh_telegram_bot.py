import contextlib
import argparse
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image

import iuh_telegram_bot as bot


def event(event_id="123", status="Đăng ký"):
    return dict(id=event_id, title="Hoạt động thử", semester="1", points="5",
                deadline="12/10/2026", status=status)


class BotTests(unittest.TestCase):
    def test_unauthorized_response_triggers_reauthentication(self):
        session = Mock()
        session.get.side_effect = [Mock(status_code=401, text="Unauthorized"), Mock(status_code=200, text="Đăng xuất bảng mới")]
        with patch.object(bot, "login") as login, patch.object(bot, "extract_events", return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bot.get_events(session, "student", "password"), [])
        login.assert_called_once_with(session, "student", "password")

    def test_expired_session_reauthenticates_then_fetches_fresh_page(self):
        session = Mock()
        session.get.side_effect = [Mock(text="Trang đăng nhập"), Mock(text="Đăng xuất bảng mới")]
        with patch.object(bot, "login") as login, patch.object(bot, "extract_events", return_value=[event()]) as extract, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(bot.get_events(session, "student", "password"), [event()])
        login.assert_called_once_with(session, "student", "password")
        extract.assert_called_once_with("Đăng xuất bảng mới")
        for call in session.get.call_args_list:
            self.assertEqual(call.kwargs["headers"], bot.FRESH_HEADERS)

    def test_valid_session_gets_new_response_on_every_poll(self):
        session = Mock()
        session.get.side_effect = [Mock(text="Đăng xuất bản 1"), Mock(text="Đăng xuất bản 2")]
        with patch.object(bot, "login") as login, patch.object(bot, "extract_events", side_effect=[[], [event()]]):
            self.assertEqual(bot.get_events(session, "student", "password"), [])
            self.assertEqual(bot.get_events(session, "student", "password"), [event()])
        self.assertEqual(session.get.call_count, 2)
        login.assert_not_called()

    def test_authentication_failure_keeps_monitor_alive_and_resets_delay(self):
        args = argparse.Namespace(interval=600, once=False, login_only=False)
        env = dict(IUH_MSSV="student", IUH_PASSWORD="password", TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="chat")
        session = Mock()
        with patch.dict(os.environ, env), patch.object(bot, "check_once", side_effect=[bot.AuthenticationError("failed"), None]) as check, patch.object(bot.time, "sleep", side_effect=[None, KeyboardInterrupt]) as sleep, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                bot.monitor(session, Path("state.json"), args)
        self.assertEqual(check.call_count, 2)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [900, 600])
        session.cookies.clear.assert_called_once()

    def test_network_failures_back_off_then_recover(self):
        args = argparse.Namespace(interval=600, once=False, login_only=False)
        env = dict(IUH_MSSV="student", IUH_PASSWORD="password", TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="chat")
        with patch.dict(os.environ, env), patch.object(bot, "check_once", side_effect=[bot.requests.ConnectionError("offline"), bot.requests.ConnectionError("offline"), None]), patch.object(bot.time, "sleep", side_effect=[None, None, KeyboardInterrupt]) as sleep, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(KeyboardInterrupt):
                bot.monitor(Mock(), Path("state.json"), args)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [600, 1200, 600])

    def test_once_authentication_failure_exits_without_waiting(self):
        args = argparse.Namespace(interval=600, once=True, login_only=False)
        env = dict(IUH_MSSV="student", IUH_PASSWORD="password", TELEGRAM_BOT_TOKEN="token", TELEGRAM_CHAT_ID="chat")
        with patch.dict(os.environ, env), patch.object(bot, "check_once", side_effect=bot.AuthenticationError("failed")), patch.object(bot.time, "sleep") as sleep, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bot.monitor(Mock(), Path("state.json"), args), 1)
        sleep.assert_not_called()

    def test_retry_delay_is_capped_and_respects_long_intervals(self):
        self.assertEqual([bot.retry_delay(600, i, True) for i in range(1, 6)], [900, 1800, 3600, 3600, 3600])
        self.assertEqual(bot.retry_delay(7200, 10), 7200)

    def test_captcha_background_matches_real_sample(self):
        with Image.open(bot.HERE / "tests/fixtures/captcha/14.png") as image:
            clean = bot.clean_captcha(image.convert("L"))
        self.assertIsNotNone(clean)
        self.assertLess(clean.width, 100)
        self.assertLess(clean.height, 33)

    def test_captcha_background_rejects_other_sizes_or_patterns(self):
        for image in (Image.new("L", (200, 66), 255), Image.new("L", (100, 33), 255), Image.new("L", (100, 33), 0)):
            self.assertIsNone(bot.clean_captcha(image))

    def test_captcha_rejects_chars_outside_configured_alphabet(self):
        variant = Image.new("L", (100, 33), 255)
        with patch.object(bot.subprocess, "run", return_value=Mock(returncode=0, stdout=b"36cz0\n")):
            self.assertEqual(bot.ocr_candidates([variant], "0123456789abcdef"), [])
            self.assertEqual(bot.ocr_candidates([variant], "0123456789abcdefghijklmnopqrstuvwxyz"), [("36cz0", 3)])

    def test_bitmap_recognition_without_tesseract(self):
        path = bot.HERE / "tests/fixtures/captcha/14.png"
        with patch.object(bot.shutil, "which", return_value=None), patch.object(bot.subprocess, "run") as run:
            self.assertEqual(bot.captcha_candidates(path.read_bytes()), [("58467", 1)])
            run.assert_not_called()

    def test_bitmap_rejects_damaged_character(self):
        with Image.open(bot.HERE / "tests/fixtures/captcha/14.png") as image:
            original = image.convert("L")
        original.paste(255, (28, 10, 37, 25))
        self.assertIsNone(bot.template_captcha(original))

    def test_captcha_timeout_does_not_crash(self):
        with patch.object(bot.subprocess, "run", side_effect=subprocess.TimeoutExpired("tesseract", 10)):
            self.assertEqual(bot.ocr_candidates([Image.new("L", (100, 33))], "0123456789abcdef"), [])

    def test_tied_captcha_answers_are_not_submitted(self):
        session = Mock()
        session.get.return_value = Mock(url=bot.LOGIN_URL, text=(
            '<form><input name="mssv"></form><img id="captcha" src="captcha.php">'))
        with patch.object(bot, "captcha_candidates", return_value=[("12345", 2), ("abcde", 2)]):
            with self.assertRaises(bot.AuthenticationError):
                bot.login(session, "student", "password", max_attempts=1)
        session.post.assert_not_called()

    def test_registration_status(self):
        for status in ("Đăng ký", "Đăng ký tham gia", "  Đăng\n ký  "):
            self.assertTrue(bot.is_open(event(status=status)))
        for status in ("Đã đăng ký", "Hết hạn đăng ký", "Chưa mở đăng ký",
                       "Không thể đăng ký", "Đóng đăng ký", "", "Chờ duyệt"):
            self.assertFalse(bot.is_open(event(status=status)), status)

    def test_table_with_and_without_tbody(self):
        header = "<tr><th>Hoạt động (T/gia)</th><th>Kết thúc đăng ký</th></tr>"
        row = ('<tr><td>1</td><td><a data-target="#myModal_123">Hoạt động thử</a></td>'
               '<td>1</td><td>5</td><td>12/10/2026</td><td>Đăng ký</td></tr>')
        for rows in (row, "<tbody>" + row + "</tbody>"):
            self.assertEqual(bot.extract_events("<table>" + header + rows + "</table>"), [event()])

    def test_missing_table_is_an_error(self):
        with self.assertRaises(RuntimeError):
            bot.extract_events("<html>Không có bảng</html>")

    def test_config_bom_and_environment_override(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"IUH_MSSV": "override"}, clear=True):
            path = Path(folder) / "bot.env"
            path.write_text('\ufeffIUH_MSSV=file\nIUH_PASSWORD="password"\n', encoding="utf-8")
            bot.load_config(path)
            self.assertEqual(os.environ["IUH_MSSV"], "override")
            self.assertEqual(os.environ["IUH_PASSWORD"], "password")

    def test_default_config_prefers_local_then_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            local = Path(folder)
            with patch.object(bot, "HERE", local), patch.object(bot, "RUNTIME_DIR", local / "work"):
                self.assertEqual(bot.default_config_path(), local / "work/iuh_bot.env")
                (local / "iuh_bot.env").touch()
                self.assertEqual(bot.default_config_path(), local / "iuh_bot.env")

    def test_notification_saved_once_and_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, "get_events", return_value=[event()]), patch.object(bot, "send_telegram") as send, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "nested/state.json"
            bot.check_once(Mock(), path, "student", "password")
            self.assertFalse(path.exists())
            bot.check_once(Mock(), path, "student", "password", "token", "12345")
            bot.check_once(Mock(), path, "student", "password", "token", "12345")
            self.assertEqual(send.call_count, 1)
            state = json.loads(path.read_text())
            self.assertEqual(state["sent"], [])
            self.assertNotIn("12345", path.read_text())
            self.assertEqual(bot.read_subscribers(state, "token")["subscribers"]["12345"]["sent"], ["123"])
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_telegram_start_and_stop_toggle_any_private_chat(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, "telegram_request") as api:
            path = Path(folder) / "state.json"
            listener = bot.TelegramCommandListener("token", path)
            listener.handle_update(Mock(), {"message": {"chat": {"id": 12345, "type": "private"}, "text": "/start"}})
            self.assertTrue(bot.read_subscribers(bot.read_state(path), "token")["subscribers"]["12345"]["enabled"])
            self.assertIn("Xin chào", api.call_args.kwargs["data"]["text"])
            listener.handle_update(Mock(), {"message": {"chat": {"id": 67890, "type": "private"}, "text": "/start"}})
            listener.handle_update(Mock(), {"message": {"chat": {"id": 12345, "type": "private"}, "text": "/stop"}})
            data = bot.read_subscribers(bot.read_state(path), "token")
            self.assertFalse(data["subscribers"]["12345"]["enabled"])
            self.assertTrue(data["subscribers"]["67890"]["enabled"])
            self.assertNotIn("12345", path.read_text())
            self.assertNotIn("67890", path.read_text())
            listener.handle_update(Mock(), {"message": {"chat": {"id": 12345, "type": "private"}, "text": "/status"}})
            self.assertIn("tạm dừng", api.call_args.kwargs["data"]["text"])

    def test_group_chat_cannot_subscribe(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, "telegram_request") as api:
            path = Path(folder) / "state.json"
            listener = bot.TelegramCommandListener("token", path)
            listener.handle_update(Mock(), {"message": {"chat": {"id": -67890, "type": "supergroup"}, "text": "/start"}})
            self.assertFalse(path.exists())
            api.assert_not_called()

    def test_each_subscriber_gets_an_event_once(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, "get_events", return_value=[event()]), patch.object(bot, "send_telegram") as send, contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "state.json"
            bot.write_subscribers(path, "token", {"subscribers": {
                "12345": {"enabled": True, "sent": []},
                "67890": {"enabled": True, "sent": []},
            }, "offset": None})
            bot.check_once(Mock(), path, "student", "password", "token")
            bot.check_once(Mock(), path, "student", "password", "token")
            self.assertEqual([call.args[2] for call in send.call_args_list], ["12345", "67890"])
            data = bot.read_subscribers(bot.read_state(path), "token")
            self.assertEqual(data["subscribers"]["12345"]["sent"], ["123"])
            self.assertEqual(data["subscribers"]["67890"]["sent"], ["123"])

    def test_blocked_subscriber_does_not_prevent_other_delivery(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, "get_events", return_value=[event()]), contextlib.redirect_stdout(io.StringIO()):
            path = Path(folder) / "state.json"
            bot.write_subscribers(path, "token", {"subscribers": {
                "12345": {"enabled": True, "sent": []},
                "67890": {"enabled": True, "sent": []},
            }, "offset": None})
            with patch.object(bot, "send_telegram", side_effect=[bot.TelegramDeliveryError(403), None]) as send:
                bot.check_once(Mock(), path, "student", "password", "token")
            self.assertEqual(send.call_count, 2)
            data = bot.read_subscribers(bot.read_state(path), "token")
            self.assertFalse(data["subscribers"]["12345"]["enabled"])
            self.assertEqual(data["subscribers"]["67890"]["sent"], ["123"])

    def test_legacy_owner_history_migrates(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, "telegram_request"):
            path = Path(folder) / "state.json"
            path.write_text('{"sent": ["123"], "notifications_enabled": false}')
            listener = bot.TelegramCommandListener("token", path, "12345")
            listener.handle_update(Mock(), {"message": {"chat": {"id": 12345, "type": "private"}, "text": "/start"}})
            data = bot.read_subscribers(bot.read_state(path), "token")
            self.assertEqual(data["subscribers"]["12345"], {"enabled": True, "sent": ["123"]})
            self.assertNotIn("notifications_enabled", bot.read_state(path))

    def test_encrypted_subscribers_need_same_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            bot.write_subscribers(path, "token", {"subscribers": {"12345": {"enabled": True, "sent": []}}, "offset": 8})
            self.assertNotIn("12345", path.read_text())
            self.assertEqual(bot.read_subscribers(bot.read_state(path), "token")["offset"], 8)
            with self.assertRaises(RuntimeError):
                bot.read_subscribers(bot.read_state(path), "another-token")

    def test_failed_send_does_not_mark_event_sent(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, "get_events", return_value=[event()]), patch.object(bot, "send_telegram", side_effect=RuntimeError("failed")):
            path = Path(folder) / "state.json"
            with self.assertRaises(RuntimeError):
                bot.check_once(Mock(), path, "student", "password", "token", "chat")
            self.assertFalse(path.exists())

    def test_invalid_state_is_not_overwritten_or_sent(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(bot, "get_events", return_value=[event()]), patch.object(bot, "send_telegram") as send:
            path = Path(folder) / "state.json"
            for state in ([], {"sent": "123"}, {"sent": [123]}, {"sent": [[]]}):
                original = json.dumps(state)
                path.write_text(original)
                with self.assertRaises(RuntimeError):
                    bot.check_once(Mock(), path, "student", "password", "token", "chat")
                self.assertEqual(path.read_text(), original)
            send.assert_not_called()

    def test_expired_session_after_login(self):
        session = Mock()
        session.get.return_value.text = "Trang đăng nhập"
        with patch.object(bot, "login") as login:
            with self.assertRaises(bot.AuthenticationError):
                bot.get_events(session, "student", "password")
            login.assert_called_once()

    def test_login_success_does_not_require_registration_menu(self):
        session = Mock()
        page = Mock(url=bot.LOGIN_URL, text=(
            '<form action="login.html"><input name="mssv">'
            '<input type="password" name="password">'
            '<input name="security_code"><button type="submit" name="btLogin">Đăng nhập</button>'
            '</form><img id="captcha" src="captcha.php">'))
        session.get.side_effect = [page, Mock(content=b"fake image")]
        session.post.return_value.text = "Đăng xuất"
        with patch.object(bot, "captcha_candidates", return_value=[("abc12", 3)]), contextlib.redirect_stdout(io.StringIO()):
            bot.login(session, "student", "password")
        self.assertEqual(session.post.call_args.kwargs["data"],
                         {"mssv": "student", "password": "password", "security_code": "abc12", "btLogin": ""})

    def test_unreadable_captcha_retries_without_posting(self):
        session = Mock()
        session.get.return_value = Mock(url=bot.LOGIN_URL, text=(
            '<form><input name="mssv"></form><img id="captcha" src="captcha.php">'))
        with patch.object(bot, "captcha_candidates", return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(bot.AuthenticationError):
                bot.login(session, "student", "password", max_attempts=2)
        self.assertEqual(session.get.call_count, 4)
        session.post.assert_not_called()

    def test_login_with_meta_refresh(self):
        session = Mock()
        page = Mock(url=bot.LOGIN_URL, text=(
            '<form><input name="mssv"><input type="password" name="password">'
            '<input name="security_code"><button name="btLogin">Đăng nhập</button>'
            '</form><img id="captcha" src="captcha.php">'))
        session.get.side_effect = [page, Mock(content=b"image"), Mock(text="Đăng xuất")]
        session.post.return_value.text = '<meta http-equiv="refresh" content="0;URL=doanvien.html@211@Tra-cuu">'
        with patch.object(bot, "captcha_candidates", return_value=[("abc12", 1)]), contextlib.redirect_stdout(io.StringIO()):
            bot.login(session, "student", "password", max_attempts=1)
        self.assertEqual(session.get.call_args.args[0], bot.EVENT_URL)
        self.assertEqual(session.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
