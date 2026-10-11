#!/usr/bin/env python3
"""Tạo dịch vụ systemd cho người dùng hiện tại, trên Ubuntu hoặc VPS Linux."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVICE = "iuh-bot.service"


def quote(value):
    # systemd có quy tắc quote riêng và mở rộng các specifier dạng %.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def unit_text(project, python, config, state_file, interval):
    return f"""[Unit]
Description=IUH activity notifier
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={str(project).replace('%', '%%')}
ExecStart={quote(python)} -u {quote(project / 'iuh_telegram_bot.py')} --config {quote(config)} --state-file {quote(state_file)} --interval {interval}
Environment=TZ=Asia/Ho_Chi_Minh
Restart=on-failure
RestartSec=900
TimeoutStopSec=30
UMask=0077
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", action="store_true", help="Cài, bật tự khởi động và chạy bot gửi thông báo")
    parser.add_argument("--print", dest="preview", action="store_true", help="Chỉ in cấu hình, không cài/chạy")
    parser.add_argument("--interval", type=int, default=600)
    parser.add_argument("--config", type=Path, default=HERE / "iuh_bot.env")
    parser.add_argument("--state-file", type=Path, default=HERE.parent / "work/iuh_event_state.json")
    args = parser.parse_args()
    if args.interval < 300:
        parser.error("Khoảng kiểm tra tối thiểu là 300 giây.")
    if args.preview and args.start:
        parser.error("Không kết hợp --print và --start.")
    python = Path(sys.executable).absolute()
    text = unit_text(HERE, python, args.config.resolve(), args.state_file.resolve(), args.interval)
    if args.preview:
        print(text)
        return 0
    if not shutil.which("systemctl"):
        parser.error("Máy này không có systemd/systemctl.")
    if not args.config.is_file():
        parser.error("Chưa có tệp cấu hình. Điền iuh_bot.env trước khi cài dịch vụ.")
    config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    target = config_dir / "systemd/user" / SERVICE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    print("Đã cài:", target)
    if args.start:
        subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE], check=True)
        print("Đã bật tự khởi động và chạy bot. Bot sẽ gửi Telegram khi có hoạt động mới mở đăng ký.")
    else:
        print("Chưa khởi chạy. Chạy: systemctl --user enable --now " + SERVICE)
    print("Xem log: journalctl --user -u " + SERVICE + " -f")
    print('Để chạy từ lúc boot và sau khi logout: sudo loginctl enable-linger "$USER"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
