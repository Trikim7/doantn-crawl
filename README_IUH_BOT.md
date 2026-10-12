# IUH HCMYU Activity Bot

Công cụ cộng đồng độc lập theo dõi cổng hoạt động của Đoàn TNCS Hồ Chí Minh tại Trường Đại học Công nghiệp TP. Hồ Chí Minh (IUH), gửi thông báo qua Telegram khi hoạt động còn mở đăng ký. Không thuộc IUH hoặc HCMYU.

Bot tự đăng nhập bằng MSSV và mật khẩu, đọc CAPTCHA bằng đối chiếu font bitmap và Tesseract OCR dự phòng, kiểm tra mục **Đăng ký tham gia** và gửi tin nhắn khi thấy hoạt động mới còn có thể đăng ký. Bot xử lý tối đa hai ảnh CAPTCHA mỗi chu kỳ; nếu bộ nhận dạng không đọc được hoặc các đáp án đứng đầu hòa phiếu, nó không gửi đáp án cho ảnh đó. Chế độ kiểm tra một lần dừng khi lỗi; chế độ liên tục chờ rồi thử lại.

## Chuẩn bị

Trên Ubuntu, cài Tesseract OCR và các thư viện Python:

```bash
sudo apt install tesseract-ocr
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

Sao chép `iuh_bot.env.example` thành `iuh_bot.env` ngay trong thư mục dự án, điền MSSV, mật khẩu, Telegram bot token và chat ID. Nếu đã có `iuh_bot.env`, chỉnh sửa tệp hiện có. Giữ tệp cấu hình riêng tư:

```bash
cp -n iuh_bot.env.example iuh_bot.env
chmod 600 iuh_bot.env
```

Tạo bot Telegram qua [BotFather](https://t.me/BotFather), gửi `/start` cho bot, rồi lấy chat ID của cuộc trò chuyện qua API `getUpdates` của Telegram. Không gửi token hoặc mật khẩu trong chat.

Bot ưu tiên `iuh_bot.env` trong thư mục dự án, rồi mới dùng đường dẫn cũ `../work/iuh_bot.env`. Có thể chọn tệp khác bằng `--config /duong/dan/iuh_bot.env`. Biến môi trường đã thiết lập được ưu tiên hơn giá trị trong tệp. Tên tệp phải chính xác, không chứa ký tự vô hình.

## Chạy

Kiểm tra đăng nhập và đọc danh sách mà không gửi Telegram:

```bash
venv/bin/python iuh_telegram_bot.py --login-only
```

Kiểm tra một lần và gửi hoạt động đang mở, nếu có:

```bash
venv/bin/python iuh_telegram_bot.py --once
```

Theo dõi liên tục, mặc định mỗi 10 phút:

```bash
venv/bin/python iuh_telegram_bot.py
```

Bot lưu mã hoạt động đã gửi trong `../work/iuh_event_state.json` để tránh gửi lặp. Có thể chọn đường dẫn bằng `--state-file`. Xóa mã trong tệp này nếu muốn nhận lại thông báo. Để chạy liên tục, máy phải bật và có mạng. Nếu trang thay đổi cấu trúc hoặc quy định truy cập, cần kiểm tra lại bot.

## Cập nhật trang và tự đăng nhập lại

Bot không cần mở trình duyệt: mỗi chu kỳ nó GET trang tra cứu mới, kèm `Cache-Control: no-cache, no-store` và `Pragma: no-cache`. Sau một chu kỳ thành công, chờ 600 giây rồi GET tiếp. Đây là khoảng phát hiện theo chu kỳ, không phải cập nhật tức thời; dữ liệu phụ thuộc những gì máy chủ IUH trả về.

Cookie được giữ trong cùng một `requests.Session`. Khi trang mất dấu hiệu đăng nhập hoặc trả HTTP 401/403, bot đăng nhập lại bằng cấu hình, xử lý CAPTCHA và GET lại trang tra cứu. Không cần đăng nhập ở mọi lần kiểm tra. Sau khi tiến trình khởi động lại, bot tự tạo phiên mới và đăng nhập.

Nếu đăng nhập thất bại, bot xóa cookie cũ rồi chờ 15, 30, 60 phút; các lần lỗi sau tiếp tục chờ tối đa 60 phút với interval mặc định. Nếu mạng, máy chủ hoặc việc xử lý dữ liệu lỗi, chờ 10, 20, 40, 60 phút. Một chu kỳ thành công đặt lại thời gian chờ về 10 phút. `--once` và `--login-only` vẫn thoát khi lỗi. Nếu đặt interval lớn hơn một giờ, bot giữ tối thiểu interval đó.

## Treo bằng systemd trên Ubuntu hoặc VPS

Máy Ubuntu hiện tại dùng để thử và có thể chạy khi đóng terminal; máy phải còn bật, có mạng và không sleep. Nếu cần chạy 24/7 độc lập với laptop, dùng một VPS Linux nhỏ; mức 1 vCPU và 1 GB RAM là cấu hình khởi đầu hợp lý cho khối lượng kiểm tra này. Dùng Ubuntu/Debian và tài khoản người dùng thông thường.

Khi chuyển lên VPS, sao chép mã Python, `requirements.txt`, toàn bộ `assets/`, tệp cấu hình và lịch sử thông báo hiện có. Tạo lại venv trên máy đích theo phần Chuẩn bị, không sao chép venv từ máy cũ. Dùng cùng tệp lịch sử để hạn chế nhận lại những hoạt động đã báo. Đặt dự án trong thư mục home trên ổ luôn được mount từ lúc boot; đường dẫn hiện tại của dự án nằm dưới `/media`, nên cần bảo đảm ổ đó được mount khi dịch vụ chạy.

Đã có `install_service.py`. Trong thư mục dự án, kiểm tra trước:

```bash
venv/bin/python iuh_telegram_bot.py --login-only
venv/bin/python install_service.py --print
```

Cài và bắt đầu theo dõi (lệnh này bật gửi Telegram khi có hoạt động mới mở đăng ký):

```bash
venv/bin/python install_service.py --start
sudo loginctl enable-linger "$USER"
```

Script ghi dịch vụ vào `~/.config/systemd/user/iuh-bot.service`, tự dùng đường dẫn Python của venv, cấu hình và lịch sử. `--interval 300` cho chu kỳ 5 phút nếu cần. Chạy script không có `--start` chỉ cài dịch vụ, chưa bật hay chạy; `--print` chỉ xem cấu hình.

`Restart=on-failure` khởi động lại tiến trình nếu bị lỗi ngoài vòng lặp, sau 15 phút. `enable-linger` cho phép dịch vụ của người dùng khởi động từ lúc boot và tồn tại sau logout. Xem [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html) và [loginctl](https://www.freedesktop.org/software/systemd/man/252/loginctl.html). Nếu `systemctl --user` không kết nối được user bus trên VPS, đăng nhập SSH trực tiếp bằng tài khoản sẽ chạy dịch vụ rồi thực hiện các lệnh trên.

Quản lý dịch vụ:

```bash
systemctl --user status iuh-bot.service
journalctl --user -u iuh-bot.service -f
systemctl --user restart iuh-bot.service
systemctl --user stop iuh-bot.service
```

Sau khi đổi mật khẩu/cấu hình/mã, dùng `restart`. Để tắt cả tự chạy khi khởi động:

```bash
systemctl --user disable --now iuh-bot.service
```

Chỉ chạy một tiến trình bot cho cùng cấu hình và lịch sử. Chạy đồng thời trên laptop và VPS có thể báo lặp; dừng bản cũ khi chuyển máy. Dịch vụ systemd đã được kiểm tra cú pháp nhưng chưa được cài/bật trong lần chuẩn bị này, vì chưa chọn máy để triển khai.

## GitHub Actions

Repository triển khai: `Trikim7/iuh-hcmyu-activity-bot` (public). Workflow `.github/workflows/iuh-bot.yml` chạy trên Ubuntu với Python 3.12. Mỗi lần chạy tạo phiên IUH mới, kiểm tra một lần rồi kết thúc; không dùng vòng lặp treo 24/7 trên runner.

Trong Settings → Secrets and variables → Actions → Secrets, đặt bốn repository Secrets: `IUH_MSSV`, `IUH_PASSWORD`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Nhập riêng từng giá trị, không upload tệp `.env`.

Trong Actions → IUH HCMYU Activity Bot → Run workflow, bỏ chọn `send_notifications` để chỉ kiểm tra CAPTCHA, đăng nhập và đọc danh sách. Chỉ bật tùy chọn này khi muốn gửi hoạt động đang mở chưa có trong lịch sử. Không có tin nhắn thử tự động; nếu không có hoạt động đang mở thì không gửi Telegram.

Repository Variable `IUH_SCHEDULE_ENABLED=true` đã được bật để chạy lịch mỗi 5 phút (`*/5 * * * *`). Đổi giá trị thành `false` hoặc xóa biến để dừng lịch. Runner chuẩn GitHub-hosted miễn phí cho repo public. Nếu chuyển lại private, mỗi job làm tròn phút lên; lịch 5 phút có thể dùng khoảng 8.640 phút trong 30 ngày khi mỗi lượt dưới một phút, cần kiểm tra quota và billing. GitHub có thể trì hoãn lịch chạy; đây không phải dịch vụ thời gian thực. Xem [billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions) và [schedule troubleshooting](https://docs.github.com/en/actions/how-tos/troubleshoot-workflows).

Lịch sử được đọc và cập nhật qua `github_state.py` trên nhánh `bot-state`, tệp `iuh_event_state.json`; không dùng cache tạm làm nguồn lịch sử. Workflow dùng `GITHUB_TOKEN` tạm với `contents: write` để tạo/cập nhật nhánh này. Nhánh chỉ lưu mã hoạt động đã thông báo, không lưu mật khẩu, token hoặc cookie. Job không chạy chồng nhau. Bước lưu lịch sử vẫn chạy sau lỗi ở bước bot để giữ các thông báo đã gửi thành công trước lỗi. Nếu tiến trình bị hủy đột ngột hoặc GitHub không ghi được lịch sử sau khi gửi, lần sau có thể báo lại.

Khi chuyển từ máy local, giữ các mã đã gửi trong lịch sử hiện có bằng cách cập nhật tệp trên nhánh `bot-state` trước khi bật gửi tự động. Dừng bản chạy local để tránh hai nơi gửi cùng hoạt động. Không xóa nhánh lịch sử trừ khi muốn nhận lại các hoạt động đang mở.

## Trạng thái kiểm thử

Trong lần debug ngày 11/10/2026: GET trang đăng nhập IUH trả HTTP 200, các ô MSSV/mật khẩu/CAPTCHA khớp với mã, môi trường `venv` có đủ thư viện và có lệnh Tesseract. Mã biên dịch được và 11 bài kiểm thử chạy đạt, gồm trạng thái đăng ký, bảng có/không có `tbody`, cấu hình, phiên đăng nhập, trường nút `btLogin`, giới hạn thử CAPTCHA, lịch sử gửi và gửi thất bại. Các bài kiểm thử dùng dữ liệu giả lập, không gửi Telegram.

Sau cải thiện nhận dạng CAPTCHA, sửa chuyển trang đăng nhập, thêm chế độ chạy lâu dài và lưu lịch sử Actions: **33 bài kiểm thử local chạy đạt**. Các bài thử kiểm tra cả làm mới dữ liệu, tái đăng nhập khi hết phiên/HTTP 401, giữ bot chạy khi lỗi, tăng thời gian chờ, đặt lại thời gian chờ sau khi hồi phục và đọc/ghi lịch sử GitHub bằng API giả lập. Dịch vụ sinh bởi `install_service.py --print` đã qua `systemd-analyze --user verify`. Workflow GitHub Actions đã qua `actionlint`; các kiểm tra local này chưa xác nhận runner GitHub thực tế truy cập IUH thành công.

Chạy lại kiểm thử bằng:

```bash
venv/bin/python -m unittest -v
```

Đã xác minh thực tế bằng `venv/bin/python -u iuh_telegram_bot.py --login-only` ngày 11/10/2026: nhận dạng CAPTCHA và đăng nhập thành công ngay lần đầu, đọc được **1 hoạt động, 0 hoạt động có thể đăng ký**, thoát với mã 0. Trang IUH trả `meta refresh` tới trang tra cứu sau POST đăng nhập; `requests` không tự theo kiểu chuyển trang này. Bot đã được sửa để GET trang tra cứu trước khi kết luận đăng nhập thất bại. **Chưa thử gửi Telegram.** Số hoạt động trên là kết quả tại thời điểm kiểm tra, có thể thay đổi.

## Cải thiện CAPTCHA

Các ảnh quan sát được có kích thước 100×33, nền đường nhiễu cố định và font bitmap 9×15. `assets/captcha_background.png` được tạo từ 12 ảnh bằng cách lấy pixel sáng nhất ở từng vị trí. Trước khi dùng nền này, bot kiểm tra kích thước và mức khớp đường nhiễu ngoài vùng chữ (ít nhất 95%).

Bot ưu tiên đối chiếu từng ký tự với font `gdFontGetGiant` tại vị trí đã xác minh trên ảnh mẫu, bỏ qua pixel bị nền che. Chỉ nhận khi mẫu tốt nhất không có pixel sai và mẫu đứng sau sai ít nhất 2 pixel. Không cần libgd trên máy chạy: dữ liệu ký tự đã có trong `assets/captcha_glyphs.json`. Nguồn font và thông tin bản quyền được lưu trong tệp đó và `assets/LIBGD_NOTICE.txt`; xem [tài liệu libgd](https://libgd.github.io/manuals/2.4.0-dev/gdfontg_8h_source.html).

Nếu font không khớp, bot dùng Tesseract trên ảnh đã tách nền, cắt vùng chữ, phóng 4/5/6 lần bằng Lanczos và thêm viền trắng. Nếu nền không khớp, bot dùng tiền xử lý cũ. Các bước phóng ảnh, thêm viền và chọn PSM dựa trên [hướng dẫn Tesseract](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html). Có thể đặt `CAPTCHA_CHARSET` trong cấu hình để giới hạn ký tự; mặc định là số và chữ thường, không tự đổi `z` thành `2` hay `o` thành `0`.

Bộ ảnh trong `tests/fixtures/captcha` gồm 12 ảnh tạo nền, 12 ảnh phát triển và 6 ảnh đánh giá cuối. Sáu ảnh cuối được lấy sau khi chốt bộ nhận dạng, gán nhãn bằng mắt trước khi chạy đo: phương pháp cũ đúng **0/6**, phương pháp mới đúng **6/6** toàn bộ chuỗi. Bộ mẫu nhỏ này chưa chứng minh tỷ lệ đúng trên mọi CAPTCHA hoặc khi trang thay đổi nền/font. Quá trình cải thiện chỉ GET ảnh và chạy nhận dạng offline, không POST đăng nhập hay gửi Telegram.

Chạy phép đo offline, kết quả lưu vào `captcha_benchmark_results.json`:

```bash
venv/bin/python benchmark_captcha.py
```

Thêm `--all` để đo cả ảnh dùng tạo nền và phát triển; kết quả đó không phải đánh giá độc lập. Số phiếu của Tesseract không phải xác suất đúng; kết quả đối chiếu bitmap được biểu diễn là một lần nhận dạng.
