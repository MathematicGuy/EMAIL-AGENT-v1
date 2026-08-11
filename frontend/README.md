# F-Cowork Frontend v1

Thư mục này là bản frontend độc lập được tách từ `apps/web` của F-Cowork. Nó chỉ chứa ứng dụng React/Vite, mã giao diện, asset, test và cấu hình frontend; không chứa backend, database hay mã orchestration.

## Yêu cầu

- Node.js 20 trở lên
- pnpm 9

## Chạy local

```bash
corepack enable
pnpm install
Copy-Item .env.example .env.local
pnpm dev
```

Mặc định ứng dụng mở tại `http://localhost:5173`. Trong chế độ local, Vite chuyển tiếp các request bắt đầu bằng `/backend` tới `http://127.0.0.1:8000`.

Để kết nối backend mới, sửa `.env.local`:

```env
VITE_API_BASE_URL=http://localhost:8000
VITE_DOCUMENTS_API_BASE_URL=http://localhost:8000
```

Backend cần cho phép origin của frontend nếu hai ứng dụng chạy khác origin. Không đưa secret vào các biến `VITE_*`, vì chúng được đóng gói vào mã chạy trên trình duyệt.

## Kiểm tra

```bash
pnpm lint
pnpm check-types
pnpm test
pnpm build
```

Build production được tạo trong `dist/`.
