# IUH Activity Bot

Bot theo dõi hoạt động trên [cổng Đoàn Thanh niên IUH](https://doantn.iuh.edu.vn/) và gửi thông báo Telegram khi có hoạt động còn mở đăng ký. Dự án viết bằng Python, có thể chạy trên máy Ubuntu, VPS Linux hoặc GitHub Actions.

> Bot chỉ theo dõi và gửi thông báo. Việc đăng ký hoạt động vẫn do bạn thực hiện trên trang IUH.

## Tính năng

- Đăng nhập IUH và nhận dạng CAPTCHA bằng mẫu font bitmap, có Tesseract OCR dự phòng.
- Đọc danh sách hoạt động và phát hiện trạng thái **Đăng ký** hoặc **Đăng ký tham gia**.
- Gửi Telegram một lần cho mỗi mã hoạt động, dựa trên lịch sử đã lưu.
- Làm mới trang tra cứu mỗi chu kỳ; tự đăng nhập lại khi phiên hết hạn.
- Tự tăng thời gian chờ khi gặp lỗi trong chế độ chạy liên tục.
- Hỗ trợ kiểm tra một lần, chạy nền bằng systemd và chạy theo lịch qua GitHub Actions.

## Cài đặt

Ví dụ dưới đây dành cho Ubuntu/Debian:

```bash
git clone https://github.com/Trikim7/doantn-crawl.git
cd doantn-crawl

sudo apt update
sudo apt install python3-venv tesseract-ocr
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

Repository hiện ở chế độ private; tài khoản GitHub cần được cấp quyền để clone.

## Cấu hình

Tạo tệp riêng từ cấu hình mẫu:

```bash
cp -n iuh_bot.env.example iuh_bot.env
chmod 600 iuh_bot.env
```

Điền thông tin vào `iuh_bot.env`:

```dotenv
IUH_MSSV=your_student_id
IUH_PASSWORD=your_password
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

Tạo Telegram bot qua [BotFather](https://t.me/BotFather), gửi `/start` cho bot rồi lấy chat ID qua [Telegram Bot API](https://core.telegram.org/bots/api#getupdates).

| Biến | Mục đích |
| --- | --- |
| `IUH_MSSV` | MSSV đăng nhập IUH |
| `IUH_PASSWORD` | Mật khẩu IUH |
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram |
| `TELEGRAM_CHAT_ID` | Cuộc trò chuyện nhận tin |
| `CAPTCHA_CHARSET` | Tập ký tự CAPTCHA; tùy chọn, mặc định gồm chữ thường và chữ số |

Chế độ `--login-only` chỉ cần MSSV và mật khẩu. Các chế độ gửi thông báo cần đủ bốn giá trị. Có thể dùng biến môi trường thay cho tệp cấu hình; biến môi trường được ưu tiên. Tùy chọn `--config PATH` chọn tệp khác.

**Không đưa thông tin đăng nhập lên Git.** `.gitignore` loại tệp `.env`, môi trường ảo và dữ liệu chạy; chỉ `iuh_bot.env.example` được commit.

## Chạy bot

### Kiểm tra đăng nhập và đọc danh sách

Không gửi Telegram:

```bash
venv/bin/python iuh_telegram_bot.py --login-only
```

### Kiểm tra một lần và gửi thông báo nếu có hoạt động mới

```bash
venv/bin/python iuh_telegram_bot.py --once
```

### Theo dõi liên tục

```bash
venv/bin/python -u iuh_telegram_bot.py
```

Mặc định bot kiểm tra mỗi 10 phút. Nhấn `Ctrl+C` để dừng. Khoảng kiểm tra tối thiểu là 300 giây. Có thể chỉ định khoảng khác, cấu hình riêng và tệp lịch sử:

```bash
venv/bin/python -u iuh_telegram_bot.py \
  --interval 600 \
  --config /path/to/iuh_bot.env \
  --state-file runtime/iuh_event_state.json
```

Bot giữ cookie trong phiên hiện tại, lấy trang tra cứu mới với header chống cache và đăng nhập lại khi hết phiên hoặc máy chủ trả HTTP 401/403. Chế độ liên tục sẽ chờ lâu hơn sau lỗi, tối đa 60 phút với khoảng mặc định, rồi trở về chu kỳ bình thường sau khi thành công. `--once` và `--login-only` kết thúc với mã lỗi nếu không chạy được.

## GitHub Actions

Workflow [`iuh-bot.yml`](.github/workflows/iuh-bot.yml) tạo một phiên IUH mới cho mỗi lượt, chạy một lần rồi kết thúc. Nó không chiếm runner để chạy vòng lặp liên tục.

### Thêm thông tin bí mật

Trong repo, mở **Settings → Secrets and variables → Actions → Secrets** và thêm bốn repository Secrets:

- `IUH_MSSV`
- `IUH_PASSWORD`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Nhập từng giá trị vào GitHub Secrets; không tải tệp `iuh_bot.env` lên repo.

### Chạy thử thủ công

Vào **Actions → IUH activity bot → Run workflow**, chọn nhánh `main` và để `send_notifications` tắt. Lượt này chỉ kiểm tra đăng nhập và đọc danh sách, không gửi Telegram. Xem log ở bước **Check IUH**.

Khi muốn bắt đầu gửi thông báo, có thể bật `send_notifications` trong lần chạy thủ công. Hoạt động đang mở nào chưa có trong lịch sử sẽ được gửi.

### Bật lịch tự động

Sau khi chạy thử thành công, vào **Settings → Secrets and variables → Actions → Variables**, thêm biến:

```text
IUH_SCHEDULE_ENABLED=true
```

Lịch workflow được đặt mỗi 5 phút. Các lượt theo lịch có gửi Telegram khi phát hiện hoạt động phù hợp. Nếu chưa tạo biến này, job theo lịch sẽ được bỏ qua. Đổi giá trị thành `false` hoặc xóa biến để dừng lịch.

Thời gian chạy phụ thuộc GitHub và có thể bị trễ; đây không phải cập nhật tức thời. Repository private chịu hạn mức phút Actions của tài khoản, bao gồm thời gian cài thư viện. Mỗi job private làm tròn lên phút kế tiếp; lịch 5 phút có thể tiêu thụ khoảng 8.640 phút trong 30 ngày nếu mỗi job dưới một phút. Kiểm tra quota và billing trước khi bật lịch. Xem [thông tin thanh toán GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

Workflow lưu lịch sử trên nhánh `bot-state`, trong tệp `iuh_event_state.json`. Token `GITHUB_TOKEN` chỉ được cấp quyền ghi nội dung repo trong job. Lịch sử chỉ chứa mã hoạt động đã báo, không chứa mật khẩu hoặc cookie. Nếu job bị hủy đột ngột hoặc không lưu được lịch sử sau khi gửi tin, lượt sau có thể gửi trùng.

## Chạy nền với systemd

Trên Ubuntu hoặc VPS Linux, trước tiên kiểm tra cấu hình và đăng nhập:

```bash
venv/bin/python iuh_telegram_bot.py --login-only
venv/bin/python install_service.py --print
```

Cài dịch vụ và bắt đầu chạy nền:

```bash
venv/bin/python install_service.py --start
sudo loginctl enable-linger "$USER"
```

Lệnh `--start` bật dịch vụ gửi Telegram và tự khởi động khi máy lên. Không có `--start` thì script chỉ cài dịch vụ. Xem log và trạng thái:

```bash
systemctl --user status iuh-bot.service
journalctl --user -u iuh-bot.service -f
```

Khởi động lại sau khi sửa mã hoặc cấu hình:

```bash
systemctl --user restart iuh-bot.service
```

Dừng và tắt tự khởi động:

```bash
systemctl --user disable --now iuh-bot.service
```

Khi chuyển lên VPS, cài lại các thư viện trên VPS, sao chép thư mục `assets/`, cấu hình và lịch sử thông báo. Đặt dự án trên ổ đĩa luôn được mount khi khởi động. Chỉ nên có một tiến trình gửi thông báo cho cùng cấu hình để tránh báo trùng.

## Lịch sử thông báo

Bot lưu mã các hoạt động đã gửi trong JSON, ví dụ:

```json
{"sent": ["event_id_1", "event_id_2"]}
```

Chạy local mặc định lưu ở `../work/iuh_event_state.json`. Dùng `--state-file` để đổi vị trí. GitHub Actions lưu lịch sử trên nhánh `bot-state`. Giữ lại tệp này khi chuyển máy; xóa lịch sử có thể khiến bot gửi lại các hoạt động đang mở.

## Kiểm thử và CAPTCHA

Chạy toàn bộ kiểm thử local:

```bash
venv/bin/python -m unittest -v
```

Đo nhận dạng CAPTCHA trên tập đánh giá offline:

```bash
venv/bin/python benchmark_captcha.py
```

Ảnh và nhãn nằm trong `tests/fixtures/captcha/`; kết quả được ghi vào `captcha_benchmark_results.json`. Tập đánh giá hiện có sáu ảnh: phương pháp mới nhận dạng đúng 6/6, phương pháp cũ 0/6. Đây là bộ mẫu nhỏ nên không đảm bảo độ chính xác khi IUH thay đổi CAPTCHA.

Trong lần phát triển, 33 kiểm thử local đã đạt và đăng nhập IUH thực tế bằng `--login-only` thành công. Các kiểm thử Actions dùng API giả lập; workflow đã qua kiểm tra tĩnh nhưng kết quả đó không thay thế cho việc chạy thử trên GitHub Actions. Lượt đăng nhập thực tế gần nhất đọc được một hoạt động, không có hoạt động mở đăng ký và không gửi Telegram.

## Cấu trúc

```text
.github/workflows/iuh-bot.yml  Workflow GitHub Actions
assets/                        Mẫu và dữ liệu tiền xử lý CAPTCHA
tests/fixtures/captcha/         Ảnh kiểm thử và nhãn CAPTCHA
iuh_telegram_bot.py             Bot đăng nhập, đọc hoạt động và gửi Telegram
github_state.py                 Đọc/ghi lịch sử trên GitHub
test_iuh_telegram_bot.py        Kiểm thử bot
test_github_state.py            Kiểm thử đồng bộ lịch sử
benchmark_captcha.py            Đo chất lượng nhận dạng CAPTCHA
install_service.py              Tạo dịch vụ systemd
README_IUH_BOT.md               Ghi chú debug và kỹ thuật bổ sung
```

Thông tin chi tiết về dữ liệu CAPTCHA và quá trình debug nằm trong [README_IUH_BOT.md](README_IUH_BOT.md). Thông báo bản quyền font được lưu ở [`assets/LIBGD_NOTICE.txt`](assets/LIBGD_NOTICE.txt).
