# Shopee Crawler — Điện Thoại & Phụ Kiện

Go pipeline crawl danh sách sản phẩm từ category **Điện Thoại & Phụ Kiện** trên Shopee VN.

## Cách hoạt động

Dùng **go-rod + stealth** để mở browser thật, intercept request `api/v4/search/search_items` mà Shopee tự gọi khi load trang, parse JSON response và lưu vào SQLite.

```
Browser (go-rod + stealth)
        │
        ▼
shopee.vn/Điện-Thoại-Phụ-Kiện-cat.11036030
        │
        ├─ Hijack: */api/v4/search/search_items*
        │         └─ Parse JSON → []Product
        │
        ▼
   SQLite (shopee.db)
```

## Schema SQLite

```sql
products (
    item_id, shop_id, name,
    price_min, price_max, price,  -- VND
    stock, sold, liked_count, comment_count,
    rating_star, rating_count,
    shop_name, brand, location,
    image_url, item_url,
    cat_id, cat_name, page_number,
    crawled_at, updated_at
)
```

## Build & Run

```bash
# Build
CGO_ENABLED=1 go build -o shopee-crawler .

# Chạy (cần Xvfb hoặc display thật)
./shopee-crawler --display :99

# Headless (không cần display)
./shopee-crawler --display ""

# Tùy chọn
./shopee-crawler --max-pages 50 --db custom.db --reset
```

## Options

| Flag | Default | Mô tả |
|------|---------|-------|
| `--max-pages` | 100 | Số trang tối đa (60 sp/trang) |
| `--db` | shopee.db | Path SQLite |
| `--display` | :99 | X display (empty = headless) |
| `--resume` | true | Tiếp tục từ checkpoint |
| `--reset` | false | Xóa progress, crawl lại từ đầu |

---

## Python Crawler (Playwright)

Crawler Python dùng **Playwright Firefox + pycookiecheat** — bypass bot detection tốt hơn Go.

### Cài đặt

```bash
pip install playwright pycookiecheat aiohttp
playwright install firefox
```

### Chạy

```bash
# Cơ bản (dùng cookies Chrome hiện tại)
DISPLAY=:99 MAX_PAGES=100 python3 crawl_shopee.py

# Với commercial proxy (bypass IP rate-limit)
DISPLAY=:99 PROXY_URL=http://user:pass@proxy.host:8080 MAX_PAGES=100 python3 crawl_shopee.py
```

### Proxy rotation

Shopee rate-limit theo IP (~1 trang/session). Để crawl nhiều trang cần:

- **Commercial proxy** (khuyến nghị): Bright Data, Oxylabs, Smartproxy...
  ```bash
  export PROXY_URL=http://user:pass@rotating.proxy.com:8080
  ```
- **Free proxy**: tự động fetch + validate từ public lists (thường không đủ ổn định)

### Anti-bot features

- Firefox (ít bị detect hơn Chromium)
- Human-like smooth scroll + random mouse movement
- Variable delay giữa các trang (20–40s)
- Auto browser restart mỗi N trang
- Cookie re-injection khi bị captcha
- Proxy rotation khi bị block
