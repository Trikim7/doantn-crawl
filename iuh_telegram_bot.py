#!/usr/bin/env python3
"""Theo dõi hoạt động IUH và báo qua Telegram khi có thể đăng ký."""

import argparse
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
        raise RuntimeError(f"Telegram trả về HTTP {response.status_code}.")
    if not response.json().get("ok"):
        raise RuntimeError("Telegram không xác nhận đã gửi tin nhắn.")


def read_state(state_file):
    if not state_file.exists():
        return {}
    state = json.loads(state_file.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or not isinstance(state.get("sent", []), list)
            or any(not isinstance(key, str) for key in state.get("sent", []))):
        raise RuntimeError("Tệp lịch sử không hợp lệ: 'sent' phải là danh sách mã dạng chuỗi.")
    return state


def write_state(state_file, state):
    state_file.parent.mkdir(parents=True, exist_ok=True)
    pending = state_file.with_name(state_file.name + ".tmp")
    pending.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(state_file)


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


def set_notifications_enabled(state_file, enabled):
    with STATE_LOCK:
        state = read_state(state_file)
        state["notifications_enabled"] = enabled
        write_state(state_file, state)


def command_text(command, enabled=True):
    if command == "start":
        return ("Xin chào! 👋\n\n"
                "Mình là bot theo dõi hoạt động của Đoàn IUH. Mình sẽ kiểm tra cổng hoạt động "
                "và gửi tin khi có hoạt động còn mở đăng ký.\n\n"
                + ("✅ Bạn đã bật nhận thông báo." if enabled else "🔕 Thông báo đang tắt.")
                + "\nDùng /help để xem hướng dẫn.")
    if command == "help":
        return ("🤖 Bot thông báo hoạt động Đoàn IUH\n\n"
                "/start — bắt đầu hoặc bật lại thông báo\n"
                "/status — xem trạng thái nhận tin\n"
                "/stop — tạm dừng thông báo\n"
                "/help — xem hướng dẫn\n\n"
                "Bot chỉ gửi thông báo hoạt động; bạn tự mở cổng IUH để xem chi tiết và đăng ký.")
    if command == "status":
        return "✅ Bạn đang nhận thông báo." if enabled else "🔕 Bạn đã tạm dừng thông báo. Gửi /start để bật lại."
    if command == "stop":
        return "Đã tạm dừng thông báo. Gửi /start bất cứ lúc nào để bật lại."
    return "Mình chưa hiểu lệnh này. Gửi /help để xem các lệnh có sẵn."


class TelegramCommandListener:
    """Nhận lệnh Telegram cho chat duy nhất đã được cấu hình trong TELEGRAM_CHAT_ID."""

    def __init__(self, token, chat_id, state_file):
        self.token = token
        self.chat_id = str(chat_id)
        self.state_file = state_file
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

    def handle_update(self, session, update):
        message = update.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        text = message.get("text", "").strip()
        if not text.startswith("/"):
            return
        command = text.split()[0][1:].split("@", 1)[0].lower()
        if chat_id != self.chat_id:
            # Không lưu hoặc đăng ký chat ID ngoài cấu hình để tránh đưa dữ liệu cá nhân
            # vào nhánh bot-state công khai.
            if command == "start":
                self.send_text(session, chat_id,
                               "Bot này hiện chỉ gửi thông báo đến cuộc trò chuyện đã được chủ bot cấu hình.")
            return
        enabled = bool(read_state(self.state_file).get("notifications_enabled", True))
        if command == "start":
            set_notifications_enabled(self.state_file, True)
            enabled = True
        elif command == "stop":
            set_notifications_enabled(self.state_file, False)
            enabled = False
        elif command not in {"help", "status"}:
            command = "unknown"
        self.send_text(session, chat_id, command_text(command, enabled))

    def run(self):
        offset = None
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
                except (RuntimeError, ValueError) as exc:
                    print(f"Lỗi nhận lệnh Telegram: {exc}", file=sys.stderr)
                    self.stop_event.wait(5)


def check_once(session, state_file, mssv, password, token=None, chat_id=None):
    events = get_events(session, mssv, password)
    open_events = [e for e in events if is_open(e)]
    with STATE_LOCK:
        state = read_state(state_file)
    if token and chat_id and not state.get("notifications_enabled", True):
        print(f"Tìm thấy {len(events)} hoạt động, {len(open_events)} hoạt động có thể đăng ký; thông báo đang tạm dừng.")
        return
    sent = set(state.get("sent", []))
    if token and chat_id:
        state_file.parent.mkdir(parents=True, exist_ok=True)
    for event in open_events:
        key = str(event["id"])
        if key in sent:
            continue
        if token and chat_id:
            send_telegram(session, token, chat_id, event)
            sent.add(key)
            with STATE_LOCK:
                latest = read_state(state_file)
                latest["sent"] = sorted(sent)
                write_state(state_file, latest)
            print("Đã báo Telegram:", event["title"])
        else:
            print("Có thể đăng ký:", event["title"])
    print(f"Tìm thấy {len(events)} hoạt động, {len(open_events)} hoạt động có thể đăng ký.")


def retry_delay(interval, failures, authentication=False):
    base = max(interval, 900) if authentication else interval
    return min(base * 2 ** min(failures - 1, 4), max(base, 3600))


def monitor(session, state_file, args):
    failures = 0
    listener = None
    if not args.once and not args.login_only:
        listener = TelegramCommandListener(os.environ["TELEGRAM_BOT_TOKEN"],
                                           os.environ["TELEGRAM_CHAT_ID"], state_file)
        listener.start()
        print("Đang nhận lệnh Telegram: /start, /status, /stop, /help.")
    while True:
        delay = args.interval
        print(time.strftime("[%Y-%m-%d %H:%M:%S] Kiểm tra danh sách hoạt động mới..."))
        try:
            check_once(session, state_file, os.environ["IUH_MSSV"], os.environ["IUH_PASSWORD"],
                       None if args.login_only else os.environ["TELEGRAM_BOT_TOKEN"],
                       None if args.login_only else os.environ["TELEGRAM_CHAT_ID"])
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
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Kiểm tra một lần rồi thoát")
    parser.add_argument("--login-only", action="store_true", help="Chỉ kiểm tra đăng nhập và danh sách sự kiện")
    parser.add_argument("--interval", type=int, default=600, help="Giây giữa hai lần kiểm tra (mặc định 600)")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--state-file", type=Path, default=RUNTIME_DIR / "iuh_event_state.json",
                        help="Tệp lưu các hoạt động đã thông báo")
    args = parser.parse_args()
    if args.interval < 300:
        parser.error("Khoảng kiểm tra tối thiểu là 300 giây.")
    try:
        load_config(args.config)
        require_config(["IUH_MSSV", "IUH_PASSWORD"])
        if not args.login_only:
            require_config(["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"])
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
