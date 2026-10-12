# IUH HCMYU Activity Bot

Công cụ theo dõi [cổng thông tin hoạt động của Đoàn Trường Đại học Công nghiệp TP. Hồ Chí Minh (IUH)](https://doantn.iuh.edu.vn/), thuộc **Đoàn Thanh niên Cộng sản Hồ Chí Minh** (*Ho Chi Minh Communist Youth Union*, HCMYU). Bot thông báo qua Telegram khi phát hiện hoạt động còn mở đăng ký.

Dự án viết bằng Python, hỗ trợ chạy trên Ubuntu, VPS Linux hoặc GitHub Actions. Đây là công cụ cộng đồng độc lập, không phải sản phẩm hay kênh thông tin chính thức của IUH hoặc HCMYU. Bot chỉ theo dõi và gửi thông báo; việc đăng ký hoạt động do bạn thực hiện trên cổng IUH.

## Tính năng

- Đăng nhập IUH và nhận dạng CAPTCHA bằng mẫu font bitmap, có Tesseract OCR dự phòng.
- Đọc danh sách hoạt động và phát hiện trạng thái **Đăng ký** hoặc **Đăng ký tham gia**.
- Mỗi người dùng Telegram có thể gửi `/start` trong chat riêng để nhận tin, `/stop` để tạm dừng.
- Gửi Telegram một lần cho mỗi mã hoạt động và mỗi người nhận, dựa trên lịch sử đã lưu.
- Làm mới trang tra cứu mỗi chu kỳ; tự đăng nhập lại khi phiên hết hạn.
- Tự tăng thời gian chờ khi gặp lỗi trong chế độ chạy liên tục.
- Hỗ trợ kiểm tra một lần, chạy nền bằng systemd và chạy theo lịch qua GitHub Actions.

## Cài đặt

Ví dụ dưới đây dành cho Ubuntu/Debian:

```bash
git clone https://github.com/Trikim7/iuh-hcmyu-activity-bot.git
cd iuh-hcmyu-activity-bot

sudo apt update
sudo apt install python3-venv tesseract-ocr
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

Repository hiện ở chế độ public nên có thể clone trực tiếp.

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
SUBSCRIBER_STATE_KEY=your_long_random_secret
```

Tạo Telegram bot qua [BotFather](https://t.me/BotFather), rồi gửi `/start` cho bot. Mỗi người muốn nhận tin đều tự gửi `/start` trong chat riêng; không cần biết chat ID.

| Biến | Mục đích |
| --- | --- |
| `IUH_MSSV` | MSSV đăng nhập IUH |
| `IUH_PASSWORD` | Mật khẩu IUH |
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram |
| `TELEGRAM_CHAT_ID` | Tùy chọn: chat ID cũ để chuyển lịch sử người nhận trước khi bot hỗ trợ nhiều người |
| `SUBSCRIBER_STATE_KEY` | Tùy chọn: khóa mã hóa cố định cho danh sách người nhận; nên đặt trước lần chạy đầu |
| `CAPTCHA_CHARSET` | Tập ký tự CAPTCHA; tùy chọn, mặc định gồm chữ thường và chữ số |

Chế độ `--login-only` chỉ cần MSSV và mật khẩu. Các chế độ gửi thông báo cần thêm `TELEGRAM_BOT_TOKEN`. Nếu chưa đặt `SUBSCRIBER_STATE_KEY`, bot dùng token Telegram làm khóa mã hóa; khi đổi token, dữ liệu người nhận cũ sẽ không giải mã được. Nếu đã đặt khóa riêng, hãy giữ nguyên khóa đó khi đổi token. Có thể dùng biến môi trường thay cho tệp cấu hình; biến môi trường được ưu tiên. Tùy chọn `--config PATH` chọn tệp khác.

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

Khi chạy liên tục, bot đồng thời nhận lệnh trong chat riêng: `/start` để được chào và bật thông báo, `/status` để xem trạng thái, `/stop` để tạm dừng hoặc `/help` để xem hướng dẫn. Bot không đăng ký chat nhóm. Danh sách người nhận, trạng thái bật/tắt, lịch sử gửi riêng của từng người và vị trí lệnh đã xử lý được mã hóa trước khi lưu để giữ nguyên sau khi GitHub Actions khởi động lại. Chế độ `--once` chỉ gửi cho những người đã đăng ký; để nhận `/start`, cần bật chế độ chạy liên tục.

## GitHub Actions

Workflow [`iuh-bot.yml`](.github/workflows/iuh-bot.yml) giữ bot chạy liên tục trên runner và kiểm tra IUH mỗi 5 phút. Lịch cron cũng chạy mỗi 5 phút; concurrency giữ tối đa một lượt kế tiếp trong hàng đợi để tiếp tục sau khi job hiện tại kết thúc. Mỗi job GitHub-hosted tối đa 6 giờ, nên đây là cách nối các phiên liên tục trên Actions chứ không phải một tiến trình vô hạn. Trong phiên chạy liên tục, bot đọc lệnh Telegram gần như ngay lập tức qua long polling.

### Thêm thông tin bí mật

Trong repo, mở **Settings → Secrets and variables → Actions → Secrets** và thêm ba repository Secrets bắt buộc:

- `IUH_MSSV`
- `IUH_PASSWORD`
- `TELEGRAM_BOT_TOKEN`

Nên thêm `SUBSCRIBER_STATE_KEY` (một chuỗi bí mật dài, giữ cố định) trước khi bot nhận người đăng ký đầu tiên. Có thể giữ `TELEGRAM_CHAT_ID` cũ để chuyển lịch sử nhận tin của tài khoản chủ bot; người dùng mới không cần chat ID.

Nhập từng giá trị vào GitHub Secrets; không tải tệp `iuh_bot.env` lên repo.

### Chạy thử thủ công

Vào **Actions → IUH HCMYU Activity Bot → Run workflow**, chọn nhánh `main`. Để thử riêng việc đăng nhập, giữ `send_notifications` và `run_continuous` tắt; lượt này không gửi Telegram. Để kiểm tra các lệnh `/start`, `/status`, `/stop`, bật `run_continuous`; worker sẽ chạy tối đa 6 giờ, nhận lệnh Telegram và gửi thông báo. Xem log ở bước **Check IUH**.

Khi muốn bắt đầu gửi thông báo, có thể bật `send_notifications` trong lần chạy thủ công. Hoạt động đang mở nào chưa có trong lịch sử sẽ được gửi.

### Bật lịch tự động

Repository Variable `IUH_SCHEDULE_ENABLED=true` hiện đã được bật. Workflow chạy liên tục, kiểm tra mỗi 5 phút và gửi Telegram khi phát hiện hoạt động phù hợp. Đổi biến thành `false` hoặc xóa biến trong **Settings → Secrets and variables → Actions → Variables** để tạm dừng lịch.

Thời gian chạy phụ thuộc GitHub và có thể bị trễ; đây không phải cập nhật tức thời. Runner chuẩn GitHub-hosted miễn phí cho repo public. Nếu đổi repo về private, mỗi job làm tròn lên phút kế tiếp; lịch 5 phút có thể tiêu thụ khoảng 8.640 phút trong 30 ngày nếu mỗi job dưới một phút, nên cần kiểm tra quota và billing. Xem [thông tin thanh toán GitHub Actions](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

Workflow lưu lịch sử trên nhánh `bot-state`, trong tệp `iuh_event_state.json`. Token `GITHUB_TOKEN` chỉ được cấp quyền ghi nội dung repo trong job. Danh sách chat ID, trạng thái đăng ký và lịch sử gửi từng người nằm trong phần mã hóa; tệp không chứa mật khẩu hoặc cookie. Nếu job bị hủy đột ngột hoặc không lưu được lịch sử sau khi gửi tin, lượt sau có thể gửi trùng.

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

Bot lưu trạng thái người nhận và mã các hoạt động đã gửi cho từng người dưới dạng mã hóa trong JSON, ví dụ cấu trúc (giá trị mã hóa đã rút ngắn):

```json
{"sent": [], "subscribers_encrypted": "gAAAAA..."}
```

Trường `sent` giữ tương thích với lịch sử cũ. Chạy local mặc định lưu ở `../work/iuh_event_state.json`. Dùng `--state-file` để đổi vị trí. GitHub Actions lưu lịch sử trên nhánh `bot-state`. Giữ lại tệp này cùng khóa mã hóa khi chuyển máy; xóa lịch sử có thể khiến bot gửi lại các hoạt động đang mở.

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

Phiên bản này đã đạt 39 kiểm thử local. Trước đó, phiên bản một người nhận đã đăng nhập IUH thực tế trên máy phát triển; lượt GitHub Actions thủ công cũng đã nhận dạng CAPTCHA, đăng nhập IUH và lưu lịch sử. Đã xác nhận một lượt chạy theo lịch hoàn tất thành công. Lượt kiểm tra thủ công gần nhất đọc được 0 hoạt động và không gửi Telegram. Chức năng nhiều người nhận cần được xác nhận thêm bằng một lượt Actions sau khi đưa lên `main`.

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
