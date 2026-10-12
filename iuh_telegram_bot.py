#!/usr/bin/env python3
"""Theo dõi hoạt động IUH và báo qua Telegram khi có thể đăng ký."""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageChops, ImageFilter, ImageOps
from cryptography.fernet import Fernet, InvalidToken


BASE = "https://doantn.iuh.edu.vn/"
LOGIN_URL = urljoin(BASE, "login.html")
EVENT_URL = urljoin(BASE, "doanvien.html@211@Tra-cuu")
HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE.parent / "work"
FRESH_HEADERS = {"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"}
STATE_LOCK = threading.RLock()
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def default_config_path():
    local = HERE / "iuh_bot.env"
    return local if local.is_file() else RUNTIME_DIR / "iuh_bot.env"


class AuthenticationError(RuntimeError):
    """Không thể đăng nhập sau số lần thử an toàn."""


class TelegramDeliveryError(RuntimeError):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__(f"Telegram trả về HTTP {status_code}.")


def load_config(path):
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_config(names):
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Thiếu cấu hình: " + ", ".join(missing))


def plain(text):
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in text if not unicodedata.combining(c)).replace("đ", "d")


def clean_captcha(original):
    """Chỉ trừ nền mẫu khi kích thước và đường nhiễu ngoài vùng chữ khớp."""
    path = HERE / "assets" / "captcha_background.png"
    if not path.is_file():
        return None
    with Image.open(path) as image:
        background = image.convert("L")
    if original.size != background.size:
        return None
    matches = total = 0
    for y in range(original.height):
        for x in range(original.width):
            if 25 <= x < 78 and 9 <= y < 27:
                continue
            expected_dark = background.getpixel((x, y)) < 40
            actual_dark = original.getpixel((x, y)) < 80
            if expected_dark or actual_dark:
                total += 1
                matches += expected_dark and actual_dark
    if total < 50 or matches / total < 0.95:
        return None
    clean = ImageChops.invert(ImageChops.subtract(background, original))
    binary = clean.point(lambda value: 255 if value > 150 else 0)
    bounds = ImageOps.invert(binary).getbbox()
    return binary.crop(bounds) if bounds else None


def template_captcha(original):
    """Khớp font bitmap; bỏ qua pixel bị nền che, chỉ nhận khớp chính xác."""
    path = HERE / "assets" / "captcha_glyphs.json"
    if not path.is_file():
        return None
    font = json.loads(path.read_text(encoding="utf-8"))
    with Image.open(HERE / "assets" / "captcha_background.png") as image:
        background = image.convert("L")
    charset = os.environ.get("CAPTCHA_CHARSET", "0123456789abcdefghijklmnopqrstuvwxyz")
    letters = []
    for slot in range(5):
        scores = []
        for char, shape in font["glyphs"].items():
            if char not in charset:
                continue
            mismatches = 0
            for index, bit in enumerate(shape):
                x = font["x"] + slot * font["width"] + index % font["width"]
                y = font["y"] + index // font["width"]
                if background.getpixel((x, y)) < 80:
                    continue
                mismatches += (bit == "1") != (original.getpixel((x, y)) < 80)
            scores.append((mismatches, char))
        scores.sort()
        if len(scores) < 2 or scores[0][0] != 0 or scores[1][0] < 2:
            return None
        letters.append(scores[0][1])
    return "".join(letters)


def ocr_candidates(variants, charset, raw=False):
    answers = []
    for variant in variants:
        data = io.BytesIO()
        variant.save(data, format="PNG")
        for psm in (7, 8, 13):
            try:
                command = ["tesseract", "stdin", "stdout", "--psm", str(psm),
                           "-c", "tessedit_char_whitelist=0123456789abcdefghijklmnopqrstuvwxyz"]
                if not raw:
                    command += ["-l", "eng", "-c", "load_system_dawg=0", "-c", "load_freq_dawg=0"]
                result = subprocess.run(
                    command,
                    input=data.getvalue(), capture_output=True, timeout=10, check=False,
                )
            except subprocess.TimeoutExpired:
                continue
            if result.returncode:
                raise RuntimeError("Tesseract thất bại; kiểm tra dữ liệu ngôn ngữ eng.")
            guess = "".join(result.stdout.decode("utf-8", "ignore").lower().split())
            if raw:
                guess = re.sub(r"[^a-z0-9]", "", guess)
            if len(guess) == 5 and all(char in charset for char in guess):
                answers.append(guess)
    return Counter(answers).most_common()


def raw_captcha_candidates(original):
    """Phương pháp cũ, dùng làm dự phòng khi mẫu nền không khớp."""
    large = original.resize((original.width * 5, original.height * 5))
    variants = [large, ImageOps.autocontrast(large)]
    variants.append(large.point(lambda v: 255 if v > 155 else 0))
    variants.append(large.filter(ImageFilter.MedianFilter(3)))
    return ocr_candidates(variants, "0123456789abcdefghijklmnopqrstuvwxyz", raw=True)


def captcha_candidates(image_bytes):
    with Image.open(io.BytesIO(image_bytes)) as image:
        original = image.convert("L")
    clean = clean_captcha(original)
    if clean is not None:
        code = template_captcha(original)
        if code:
            return [(code, 1)]
    if not shutil.which("tesseract"):
        raise RuntimeError("Chưa cài Tesseract OCR để nhận dạng dự phòng.")
    if clean is None:
        return raw_captcha_candidates(original)
    charset = os.environ.get("CAPTCHA_CHARSET", "0123456789abcdefghijklmnopqrstuvwxyz")
    variants = [ImageOps.expand(clean.resize((clean.width * scale, clean.height * scale),
                                            Image.Resampling.LANCZOS), border=20, fill=255)
                for scale in (4, 5, 6)]
    return ocr_candidates(variants, charset)


def login(session, mssv, password, max_attempts=2):
    for attempt in range(1, max_attempts + 1):
        page = session.get(LOGIN_URL, timeout=20)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")
        form = soup.find("form")
        captcha = soup.find("img", id="captcha")
        if not form or not captcha:
            if "Đăng xuất" in page.text:
                return
            raise RuntimeError("Không tìm thấy biểu mẫu đăng nhập hoặc ảnh CAPTCHA.")

        image_url = urljoin(page.url, captcha.get("src", ""))
        image = session.get(image_url, timeout=20)
        image.raise_for_status()
        guesses = captcha_candidates(image.content)
        if not guesses or (len(guesses) > 1 and guesses[0][1] == guesses[1][1]):
            if attempt < max_attempts:
                print(f"CAPTCHA lần {attempt}: OCR không có đáp án rõ ràng; lấy ảnh mới.")
                continue
            raise AuthenticationError("OCR không có đáp án rõ ràng đủ 5 ký tự; chưa gửi đăng nhập cho ảnh này.")
        code, votes = guesses[0]
        print(f"CAPTCHA lần {attempt}: OCR đọc được một đáp án từ {votes} phép thử.")

        fields = {}
        for field in form.select("input[name]"):
            kind = field.get("type", "text").lower()
            if kind not in ("submit", "button", "reset", "file", "image") and not field.has_attr("disabled"):
                if kind in ("checkbox", "radio") and not field.has_attr("checked"):
                    continue
                fields[field["name"]] = field.get("value", "")
        submit = form.select_one('button[name]:not([disabled]):not([type]), button[type="submit"][name]:not([disabled]), input[type="submit"][name]:not([disabled])')
        if submit:
            fields[submit["name"]] = submit.get("value", "")
        for field in form.select("input"):
            name = field.get("name")
            if not name:
                continue
            kind = field.get("type", "text").lower()
            ident = (field.get("id", "") + " " + name).lower()
            if kind == "password":
                fields[name] = password
            elif "mssv" in ident:
                fields[name] = mssv
            elif "security_code" in ident or "captcha" in ident:
                fields[name] = code
        if password not in fields.values() or code not in fields.values() or mssv not in fields.values():
            raise RuntimeError("Không xác định được các ô MSSV, mật khẩu và CAPTCHA.")

        action = urljoin(page.url, form.get("action") or page.url)
        response = session.post(action, data=fields, headers={"Referer": page.url}, timeout=20)
        response.raise_for_status()
        # IUH trả trang meta-refresh sau đăng nhập; requests không tự theo nó.
        if "Đăng xuất" not in response.text:
            response = session.get(EVENT_URL, headers=FRESH_HEADERS, timeout=20)
            response.raise_for_status()
        if "Đăng xuất" in response.text:
            print("Đăng nhập IUH thành công.")
            return
        print(f"Đăng nhập lần {attempt} chưa thành công.")
    raise AuthenticationError(f"Đã dừng sau {max_attempts} lần đăng nhập; không tiếp tục đoán CAPTCHA.")


def extract_events(html):
    soup = BeautifulSoup(html, "html.parser")
    table = next((t for t in soup.find_all("table")
                  if "hoatdong(t/gia)" in re.sub(r"\s+", "", plain(t.get_text(" ", strip=True)))
                  and "ket thuc dang ky" in " ".join(plain(t.get_text(" ", strip=True)).split())), None)
    if table is None:
        raise RuntimeError("Không tìm thấy bảng đăng ký hoạt động.")
    events = []
    for row in table.select("tr"):
        if row.find_parent("table") is not table:
            continue
        cells = row.find_all("td", recursive=False)
        if len(cells) < 6:
            continue
        link = cells[1].find("a")
        title = (link or cells[1]).get_text(" ", strip=True)
        modal = (link.get("data-target", "") if link else "")
        event_id = modal.removeprefix("#myModal_") if modal.startswith("#myModal_") else title
        events.append({
            "id": event_id,
            "title": title,
            "semester": cells[2].get_text(" ", strip=True),
            "points": cells[3].get_text(" ", strip=True),
            "deadline": cells[4].get_text(" ", strip=True),
            "status": cells[5].get_text(" ", strip=True),
        })
    return events


def is_open(event):
    status = " ".join(plain(event["status"]).split())
    return status in {"dang ky", "dang ky tham gia"}


def get_events(session, mssv, password):
    response = session.get(EVENT_URL, headers=FRESH_HEADERS, timeout=20)
    if response.status_code not in (401, 403):
        response.raise_for_status()
    if response.status_code in (401, 403) or "Đăng xuất" not in response.text:
        print("Phiên IUH hết hạn hoặc chưa đăng nhập; đang đăng nhập lại.")
        login(session, mssv, password)
        response = session.get(EVENT_URL, headers=FRESH_HEADERS, timeout=20)
        if response.status_code in (401, 403):
            raise AuthenticationError("IUH vẫn từ chối phiên sau khi đăng nhập lại.")
        response.raise_for_status()
        if "Đăng xuất" not in response.text:
            raise AuthenticationError("Phiên đăng nhập không hợp lệ khi đọc danh sách hoạt động.")
    return extract_events(response.text)


def send_telegram(session, token, chat_id, event):
    message = (f"Hoạt động IUH có thể đăng ký:\n{event['title']}\n"
               f"Học kỳ: {event['semester']} | Điểm: {event['points']}\n"
               f"Hạn đăng ký: {event['deadline']}\n{EVENT_URL}")
    try:
        response = session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": message}, timeout=20,
        )
    except requests.RequestException as exc:
        raise RuntimeError("Không kết nối được Telegram.") from exc
    if not response.ok:
        raise TelegramDeliveryError(response.status_code)
    if not response.json().get("ok"):
        raise RuntimeError("Telegram không xác nhận đã gửi tin nhắn.")


def read_state(state_file):
    if not state_file.exists():
        return {}
    state = json.loads(state_file.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or not isinstance(state.get("sent", []), list)
            or any(not isinstance(key, str) for key in state.get("sent", []))):
        raise RuntimeError("Tệp lịch sử không hợp lệ: 'sent' phải là danh sách mã dạng chuỗi.")
    if "subscribers_encrypted" in state and not isinstance(state["subscribers_encrypted"], str):
        raise RuntimeError("Tệp lịch sử không hợp lệ: danh sách đăng ký phải được mã hóa.")
    return state


def write_state(state_file, state):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    pending = state_file.with_name(state_file.name + ".tmp")
    with os.fdopen(os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as stream:
        stream.write(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    pending.chmod(0o600)
    pending.replace(state_file)


def subscriber_cipher(token, legacy=False):
    secret = token if legacy else (os.environ.get("SUBSCRIBER_STATE_KEY") or token)
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def read_subscribers(state, token, legacy_chat_id=None):
    encrypted = state.get("subscribers_encrypted")
    if encrypted:
        try:
            data = json.loads(subscriber_cipher(token).decrypt(encrypted.encode("ascii")))
        except InvalidToken as exc:
            if not os.environ.get("SUBSCRIBER_STATE_KEY"):
                raise RuntimeError("Không giải mã được danh sách đăng ký; kiểm tra token hoặc SUBSCRIBER_STATE_KEY.") from exc
            try:
                # Chuyển trạng thái cũ từ khóa dựa trên token sang khóa riêng ở lần ghi tiếp theo.
                data = json.loads(subscriber_cipher(token, legacy=True).decrypt(encrypted.encode("ascii")))
            except (InvalidToken, ValueError, UnicodeError) as legacy_exc:
                raise RuntimeError("Không giải mã được danh sách đăng ký; kiểm tra token hoặc SUBSCRIBER_STATE_KEY.") from legacy_exc
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("Danh sách đăng ký đã mã hóa không hợp lệ.") from exc
    else:
        data = {"subscribers": {}, "offset": None}
        if legacy_chat_id:
            data["subscribers"][str(legacy_chat_id)] = {
                "enabled": bool(state.get("notifications_enabled", True)),
                "sent": list(state.get("sent", [])),
            }
    if (not isinstance(data, dict) or not isinstance(data.get("subscribers"), dict)
            or data.get("offset") is not None and not isinstance(data["offset"], int)):
        raise RuntimeError("Danh sách đăng ký không hợp lệ.")
    for chat_id, item in data["subscribers"].items():
        if (not isinstance(chat_id, str) or not chat_id.isdecimal()
                or not isinstance(item, dict) or not isinstance(item.get("enabled"), bool)
                or not isinstance(item.get("sent"), list)
                or any(not isinstance(key, str) for key in item["sent"])):
            raise RuntimeError("Dữ liệu người đăng ký không hợp lệ.")
        if ("mssv" in item and not isinstance(item["mssv"], str)
                or "password" in item and not isinstance(item["password"], str)
                or "auto_register" in item and not isinstance(item["auto_register"], bool)
                or "pending_account" in item and item["pending_account"] not in {"mssv", "password"}):
            raise RuntimeError("Dữ liệu tài khoản người đăng ký không hợp lệ.")
    return data


def write_subscribers(state_file, token, data):
    if len(os.environ.get("SUBSCRIBER_STATE_KEY", "")) < 32 and any(
        item.get("mssv") or item.get("password") or item.get("pending_mssv")
        for item in data["subscribers"].values()
    ):
        raise RuntimeError("Không lưu tài khoản IUH khi thiếu SUBSCRIBER_STATE_KEY.")
    state = read_state(state_file)
    state.setdefault("sent", [])  # tương thích với lịch sử trên nhánh bot-state hiện tại
    state["subscribers_encrypted"] = subscriber_cipher(token).encrypt(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    state.pop("notifications_enabled", None)
    write_state(state_file, state)


def telegram_request(session, token, method, **kwargs):
    try:
        response = session.post(TELEGRAM_API.format(token=token, method=method), timeout=35, **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError("Không kết nối được Telegram.") from exc
    if not response.ok:
        raise RuntimeError(f"Telegram trả về HTTP {response.status_code} khi gọi {method}.")
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram không xác nhận thao tác {method}.")
    return data.get("result")


def command_text(command, enabled=True):
    if command == "start":
        return ("Xin chào! 👋\n\n"
                "Mình theo dõi hoạt động Đoàn - Hội IUH và báo khi có hoạt động còn mở đăng ký. "
                "Dùng /account để nhận danh sách theo tài khoản IUH của bạn.\n\n"
                + ("✅ Bạn đã bật nhận thông báo." if enabled else "🔕 Thông báo đang tắt.")
                + "\nDùng /help để xem hướng dẫn. Đây là bot cộng đồng, không phải kênh chính thức của IUH.")
    if command == "help":
        return ("🤖 Bot thông báo hoạt động Đoàn IUH\n\n"
                "/start — bắt đầu hoặc bật lại thông báo\n"
                "/status — xem trạng thái nhận tin\n"
                "/stop — tạm dừng thông báo\n"
                "/account — nhận thông báo theo tài khoản IUH riêng\n"
                "/forget — xóa tài khoản IUH đã lưu\n"
                "/help — xem hướng dẫn\n\n"
                "Bot chỉ gửi thông báo; bạn tự đăng ký trên cổng IUH. Đây là bot cộng đồng độc lập.")
    if command == "status":
        return "✅ Bạn đang nhận thông báo." if enabled else "🔕 Bạn đã tạm dừng thông báo. Gửi /start để bật lại."
    if command == "stop":
        return "Đã tạm dừng thông báo. Gửi /start bất cứ lúc nào để bật lại."
    return "Mình chưa hiểu lệnh này. Gửi /help để xem các lệnh có sẵn."


def account_ready():
    return (len(os.environ.get("SUBSCRIBER_STATE_KEY", "")) >= 32
            and os.environ.get("IUH_ACCOUNT_COLLECTION_ENABLED") == "true")


class TelegramCommandListener:
    """Nhận lệnh từ người dùng trong trò chuyện riêng với bot."""

    def __init__(self, token, state_file, legacy_chat_id=None):
        self.token = token
        self.state_file = state_file
        self.legacy_chat_id = legacy_chat_id
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name="telegram-commands", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=2)

    def send_text(self, session, chat_id, text):
        telegram_request(session, self.token, "sendMessage",
                         data={"chat_id": chat_id, "text": text})

    def delete_private_message(self, session, chat_id, message):
        message_id = message.get("message_id")
        if not isinstance(message_id, int):
            return False
        try:
            telegram_request(session, self.token, "deleteMessage",
                             data={"chat_id": chat_id, "message_id": message_id})
        except RuntimeError:
            return False
        return True

    def handle_account_input(self, session, chat_id, message, subscriber):
        # Xóa nội dung nhạy cảm trước khi kiểm tra hay ghi vào tệp trạng thái.
        if not self.delete_private_message(session, chat_id, message):
            self.send_text(session, chat_id, "Không xóa được tin nhắn chứa tài khoản. Mình chưa lưu dữ liệu; hãy xóa tin đó thủ công.")
            return
        value = message.get("text", "")
        step = subscriber.get("pending_account")
        if step == "mssv":
            if not re.fullmatch(r"[A-Za-z0-9]{5,20}", value):
                self.send_text(session, chat_id, "MSSV chưa đúng định dạng. Hãy gửi lại MSSV trong chat riêng.")
                return
            with STATE_LOCK:
                data = read_subscribers(read_state(self.state_file), self.token, self.legacy_chat_id)
                current = data["subscribers"][chat_id]
                current["pending_mssv"] = value
                current["pending_account"] = "password"
                write_subscribers(self.state_file, self.token, data)
            self.send_text(session, chat_id, "Đã nhận MSSV. Hãy gửi riêng mật khẩu IUH trong tin nhắn tiếp theo; bot sẽ xóa tin ngay khi nhận. Tin nhắn bot không phải Secret Chat.")
            return
        if step != "password" or not value or len(value) > 128:
            self.send_text(session, chat_id, "Mật khẩu trống hoặc quá dài. Hãy gửi lại, hoặc /forget để hủy.")
            return
        mssv = subscriber.get("pending_mssv")
        if not mssv:
            self.send_text(session, chat_id, "Phiên nhập tài khoản đã hết. Hãy gửi /account để bắt đầu lại.")
            return
        try:
            with requests.Session() as iuh_session:
                get_events(iuh_session, mssv, value)
        except (requests.RequestException, RuntimeError, ValueError):
            self.send_text(session, chat_id, "IUH chưa xác nhận đăng nhập. Mình không lưu mật khẩu; hãy kiểm tra và gửi lại.")
            return
        with STATE_LOCK:
            data = read_subscribers(read_state(self.state_file), self.token, self.legacy_chat_id)
            current = data["subscribers"][chat_id]
            current["mssv"] = mssv
            current["password"] = value
            current["enabled"] = True
            current.pop("pending_account", None)
            current.pop("pending_mssv", None)
            write_subscribers(self.state_file, self.token, data)
        self.send_text(session, chat_id, "Đăng nhập IUH thành công. Từ lần quét tiếp theo, bạn sẽ nhận thông báo theo những hoạt động tài khoản này thấy còn chỗ. Dùng /forget để xóa tài khoản.")

    def handle_update(self, session, update):
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if chat.get("type") != "private" or not chat_id.isdecimal():
            return
        text = message.get("text", "")
        if not isinstance(text, str):
            return
        command = text.split()[0][1:].split("@", 1)[0].lower() if text.startswith("/") else ""
        if command == "account" and len(text.split()) > 1:
            self.delete_private_message(session, chat_id, message)
            self.send_text(session, chat_id, "Đừng gửi MSSV hoặc mật khẩu kèm lệnh. Hãy dùng /account rồi làm theo từng bước.")
            return
        with STATE_LOCK:
            data = read_subscribers(read_state(self.state_file), self.token, self.legacy_chat_id)
            subscriber = data["subscribers"].get(chat_id)
            if subscriber and subscriber.get("pending_account") and not command:
                pending = subscriber.copy()
            else:
                pending = None
        if pending is not None:
            self.handle_account_input(session, chat_id, message, pending)
            return
        if not command:
            return
        response = None
        with STATE_LOCK:
            data = read_subscribers(read_state(self.state_file), self.token, self.legacy_chat_id)
            subscriber = data["subscribers"].get(chat_id)
            enabled = bool(subscriber and subscriber["enabled"])
            if command == "start":
                if subscriber is None:
                    subscriber = {"enabled": True, "sent": []}
                    data["subscribers"][chat_id] = subscriber
                subscriber["enabled"] = enabled = True
                write_subscribers(self.state_file, self.token, data)
            elif command == "stop":
                if subscriber is not None:
                    subscriber["enabled"] = False
                    write_subscribers(self.state_file, self.token, data)
                enabled = False
            elif command == "account":
                if not account_ready():
                    response = "Chức năng lưu tài khoản chưa được mở: chủ bot cần đặt khóa mã hóa riêng và bật tính năng này."
                else:
                    if subscriber is None:
                        subscriber = {"enabled": False, "sent": []}
                        data["subscribers"][chat_id] = subscriber
                    subscriber["pending_account"] = "mssv"
                    subscriber.pop("pending_mssv", None)
                    write_subscribers(self.state_file, self.token, data)
                    response = ("Hãy gửi MSSV trong tin nhắn tiếp theo. Bot sẽ xóa tin sau khi nhận. "
                                "Sau đó bot sẽ hỏi mật khẩu để kiểm tra đăng nhập IUH. "
                                "Tin nhắn với bot có thể được Telegram và người vận hành bot xử lý; không phải Secret Chat.")
            elif command == "forget":
                if subscriber is not None:
                    for key in ("mssv", "password", "pending_mssv", "pending_account", "auto_register"):
                        subscriber.pop(key, None)
                    write_subscribers(self.state_file, self.token, data)
                response = "Đã xóa tài khoản IUH. Nếu đang nhận thông báo, bạn sẽ trở về danh sách hoạt động chung."
            elif command == "status":
                account = ("***" + subscriber["mssv"][-4:]) if subscriber and subscriber.get("mssv") else "chưa liên kết"
                response = f"{command_text('status', enabled)}\nTài khoản IUH: {account}. Nguồn hoạt động: {'riêng theo tài khoản' if account != 'chưa liên kết' else 'danh sách chung'}."
            elif command not in {"help", "status"}:
                command = "unknown"
        self.send_text(session, chat_id, response or command_text(command, enabled))

    def run(self):
        with STATE_LOCK:
            data = read_subscribers(read_state(self.state_file), self.token, self.legacy_chat_id)
            offset = data["offset"]
            if os.environ.get("SUBSCRIBER_STATE_KEY"):
                write_subscribers(self.state_file, self.token, data)
        with requests.Session() as session:
            while not self.stop_event.is_set():
                try:
                    params = {"timeout": 20, "allowed_updates": json.dumps(["message"])}
                    if offset is not None:
                        params["offset"] = offset
                    updates = telegram_request(session, self.token, "getUpdates", data=params) or []
                    for update in updates:
                        try:
                            self.handle_update(session, update)
                        except (RuntimeError, ValueError, OSError) as exc:
                            print(f"Lỗi xử lý lệnh Telegram: {exc}", file=sys.stderr)
                        if isinstance(update.get("update_id"), int):
                            offset = update["update_id"] + 1
                            with STATE_LOCK:
                                data = read_subscribers(read_state(self.state_file), self.token, self.legacy_chat_id)
                                data["offset"] = offset
                                write_subscribers(self.state_file, self.token, data)
                except (RuntimeError, ValueError) as exc:
                    print(f"Lỗi nhận lệnh Telegram: {exc}", file=sys.stderr)
                    self.stop_event.wait(5)


def deliver_events(session, state_file, token, chat_id, events, expected_mssv=None,
                   legacy_chat_id=None):
    with STATE_LOCK:
        data = read_subscribers(read_state(state_file), token, legacy_chat_id)
        subscriber = data["subscribers"].get(chat_id)
        if not subscriber or not subscriber["enabled"] or subscriber.get("mssv") != expected_mssv:
            return
        sent = set(subscriber["sent"])
        for event in events:
            key = str(event["id"])
            if key in sent:
                continue
            try:
                send_telegram(session, token, chat_id, event)
            except TelegramDeliveryError as exc:
                if exc.status_code not in (400, 403):
                    raise
                subscriber["enabled"] = False
                write_subscribers(state_file, token, data)
                print("Đã tạm dừng người nhận không còn truy cập được Telegram.")
                break
            sent.add(key)
            subscriber["sent"] = sorted(sent)
            write_subscribers(state_file, token, data)
            print("Đã báo Telegram:", event["title"])


def check_once(session, state_file, mssv, password, token=None, chat_id=None,
               personal_sessions=None, personal_retry=None):
    if not token:
        events = get_events(session, mssv, password)
        open_events = [e for e in events if is_open(e)]
        for event in open_events:
            print("Có thể đăng ký:", event["title"])
        print(f"Tìm thấy {len(events)} hoạt động, {len(open_events)} hoạt động có thể đăng ký.")
        return

    owned_sessions = personal_sessions is None
    personal_sessions = {} if personal_sessions is None else personal_sessions
    personal_retry = {} if personal_retry is None else personal_retry
    with STATE_LOCK:
        subscribers = read_subscribers(read_state(state_file), token, chat_id)["subscribers"]
        targets = {key: item.copy() for key, item in subscribers.items() if item["enabled"]}

    try:
        active_personal = {key for key, item in targets.items() if item.get("mssv") and item.get("password")}
        for key in list(personal_sessions):
            if key not in active_personal:
                personal_sessions.pop(key)[1].close()
                personal_retry.pop(key, None)

        for subscriber_id, subscriber in targets.items():
            if subscriber_id not in active_personal:
                continue
            mssv_personal = subscriber["mssv"]
            password_personal = subscriber["password"]
            credential_id = hashlib.sha256((mssv_personal + "\0" + password_personal).encode()).hexdigest()
            retry = personal_retry.get(subscriber_id)
            if retry and retry[0] == credential_id and time.monotonic() < retry[1]:
                continue
            cached = personal_sessions.get(subscriber_id)
            if cached and cached[0] != credential_id:
                cached[1].close()
                cached = None
            if cached is None:
                personal_session = requests.Session()
                personal_session.headers.update({"User-Agent": "Mozilla/5.0 IUH-event-notifier/1.0"})
                personal_sessions[subscriber_id] = (credential_id, personal_session)
            else:
                personal_session = cached[1]
            try:
                events = get_events(personal_session, mssv_personal, password_personal)
                personal_retry.pop(subscriber_id, None)
                open_events = [event for event in events if is_open(event)]
                deliver_events(session, state_file, token, subscriber_id, open_events, mssv_personal,
                               chat_id)
                print(f"Tài khoản riêng: {len(events)} hoạt động, {len(open_events)} còn mở.")
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                personal_retry[subscriber_id] = (credential_id,
                    time.monotonic() + (900 if isinstance(exc, AuthenticationError) else 300))
                personal_sessions.pop(subscriber_id)[1].close()
                print("Không đọc được hoạt động của một tài khoản riêng; sẽ thử lại sau.", file=sys.stderr)

        shared_targets = [key for key in targets if key not in active_personal]
        if shared_targets or not targets:
            events = get_events(session, mssv, password)
            open_events = [event for event in events if is_open(event)]
            for subscriber_id in shared_targets:
                deliver_events(session, state_file, token, subscriber_id, open_events,
                               legacy_chat_id=chat_id)
            print(f"Danh sách chung: {len(events)} hoạt động, {len(open_events)} còn mở.")
    finally:
        if owned_sessions:
            for _, personal_session in personal_sessions.values():
                personal_session.close()


def retry_delay(interval, failures, authentication=False):
    base = max(interval, 900) if authentication else interval
    return min(base * 2 ** min(failures - 1, 4), max(base, 3600))


def monitor(session, state_file, args):
    failures = 0
    personal_sessions = {}
    personal_retry = {}
    listener = None
    if not args.once and not args.login_only:
        listener = TelegramCommandListener(os.environ["TELEGRAM_BOT_TOKEN"],
                                           state_file, os.environ.get("TELEGRAM_CHAT_ID"))
        listener.start()
        print("Đang nhận lệnh Telegram: /start, /status, /stop, /account, /forget, /help.")
    while True:
        delay = args.interval
        print(time.strftime("[%Y-%m-%d %H:%M:%S] Kiểm tra danh sách hoạt động mới..."))
        try:
            check_once(session, state_file, os.environ["IUH_MSSV"], os.environ["IUH_PASSWORD"],
                       None if args.login_only else os.environ["TELEGRAM_BOT_TOKEN"],
                       None if args.login_only else os.environ.get("TELEGRAM_CHAT_ID"),
                       personal_sessions, personal_retry)
            failures = 0
        except (requests.RequestException, RuntimeError, ValueError, OSError) as exc:
            print(f"Lỗi: {exc}", file=sys.stderr)
            if args.once or args.login_only:
                return 1
            failures += 1
            authentication = isinstance(exc, AuthenticationError)
            delay = retry_delay(args.interval, failures, authentication)
            if authentication:
                session.cookies.clear()
        if args.once or args.login_only:
            return 0
        print(f"Chờ {delay} giây trước lần kiểm tra tiếp theo.")
        try:
            time.sleep(delay)
        except KeyboardInterrupt:
            if listener:
                listener.stop()
            for _, personal_session in personal_sessions.values():
                personal_session.close()
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Kiểm tra một lần rồi thoát")
    parser.add_argument("--login-only", action="store_true", help="Chỉ kiểm tra đăng nhập và danh sách sự kiện")
    parser.add_argument("--interval", type=int, default=120, help="Giây giữa hai lần kiểm tra (mặc định 120)")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--state-file", type=Path, default=RUNTIME_DIR / "iuh_event_state.json",
                        help="Tệp lưu các hoạt động đã thông báo")
    args = parser.parse_args()
    if args.interval < 120:
        parser.error("Khoảng kiểm tra tối thiểu là 120 giây.")
    try:
        load_config(args.config)
        require_config(["IUH_MSSV", "IUH_PASSWORD"])
        if not args.login_only:
            require_config(["TELEGRAM_BOT_TOKEN"])
        args.state_file.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Lỗi cấu hình: {exc}", file=sys.stderr)
        return 1
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0 IUH-event-notifier/1.0"})
        try:
            return monitor(session, args.state_file, args)
        except KeyboardInterrupt:
            print("Đã dừng bot.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
