# Cách chạy project
1. Vào extension cài Live Server, Python Debugger (nếu có yêu cầu cầu cài Python thì cài thêm Python)
2. Chọn view => terminal rồi chạy lần lượt từng đoạn code sau: 
Nếu chạy lần đầu thì chạy code sau: python -m venv .venv hoặc py -m venv .venv 
3. python -m pip install -r requirements.txt hoặc py -m pip install -r requirements.txt
4. python main.py hoặc py main.py

### Chức năng của từng file

| File | Chức năng |
|---|---|
| `main.py` | Khởi tạo FastAPI, kết nối Supabase, cung cấp API và phục vụ giao diện |
| `dify_client.py` | Gửi dữ liệu sang Dify Workflow và nhận kết quả |
| `UI/index.html` | Cấu trúc giao diện dashboard |
| `UI/style.css` | Giao diện, màu sắc và bố cục |
| `UI/frontend.js` | Gọi API backend và cập nhật dashboard |
| `.env` | Chứa URL và API key của Supabase, Dify |
| `requirements.txt` | Danh sách thư viện Python cần cài |
| `.gitignore` | Ngăn file bí mật và file tạm được đưa lên Git |

## 3. Chuẩn bị môi trường

Mở Terminal tại thư mục dự án.

### Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Nếu PowerShell chặn việc kích hoạt môi trường ảo, chạy:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.venv\Scripts\Activate.ps1
```

### Windows Command Prompt

```cmd
python -m venv .venv
.venv\Scripts\activate.bat
```

### macOS hoặc Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 4. Cài thư viện

```bash
python -m pip install -r requirements.txt
```



## 12. Các lỗi thường gặp

### `ModuleNotFoundError`

Cài lại thư viện:

```bash
python -m pip install -r requirements.txt
```

Đảm bảo môi trường `.venv` đã được kích hoạt.

### `Thiếu SUPABASE_URL` hoặc `SUPABASE_KEY`

Kiểm tra file `.env` nằm cùng cấp với `main.py` và đã điền đúng giá trị.

### `401 Unauthorized` từ Dify

Kiểm tra:

```env
DIFY_API_KEY=app-...
```

API key phải thuộc đúng ứng dụng Dify đã publish.

### `404 Not Found` khi mở giao diện

Nếu backend chỉ khai báo route `/UI`, hãy mở:

```text
http://127.0.0.1:8000/UI
```

### CSS hoặc JavaScript không tải

Kiểm tra trong `UI/index.html`:

```html
<link rel="stylesheet" href="/UI/style.css">
<script src="/UI/frontend.js" defer></script>
```

### Lỗi CORS

Nếu frontend chạy bằng Live Server ở cổng `5500`, sửa `.env`:

```env
ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5500,http://localhost:5500
```

Khởi động lại backend sau khi sửa `.env`.

